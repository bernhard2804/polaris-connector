"""Orchestriert einen vollstaendigen Curator-Lauf fuer ein Projekt:

  neue Quellen sammeln -> Tagesbatches/Dokumente bilden -> Budget anwenden
  -> pro Einheit: Codex aufrufen -> lokal validieren -> mergen -> State
  aktualisieren.

llm_fn ist injizierbar, damit Tests ohne echte Codex-Aufrufe laufen.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import context as context_mod
from . import llm as llm_mod
from . import review as review_mod
from . import batching, change_schema, storage, chatgpt_source, documents_source, integrity, journal, layout, locking, merge, state as state_mod
from .config import CuratorConfig
from .llm import LlmOutputError, LlmResult, LlmUnavailableError, call_llm
from .logging_setup import setup_logging


@dataclass
class RunReport:
    project: str = ""
    dry_run: bool = False
    new_messages: int = 0
    new_documents: int = 0
    day_batches_planned: list[str] = field(default_factory=list)
    day_batches_processed: list[str] = field(default_factory=list)
    documents_planned: list[str] = field(default_factory=list)
    documents_processed: list[str] = field(default_factory=list)
    llm_calls_made: int = 0
    review_calls_made: int = 0  # unabhaengige Pruefung (Claude), zaehlt nicht gegen das Codex-Budget
    total_input_chars: int = 0
    knowledge_changed: bool = False
    decisions_changed: bool = False
    open_loops_changed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    aborted_reason: str | None = None
    integrity_findings: list[str] = field(default_factory=list)


REPEATED_FAILURE_THRESHOLD = 3


@dataclass
class _WorkItem:
    kind: str  # "chat" | "document"
    label: str
    char_count: int
    key: str = ""  # stabil ueber Laeufe (Batch-Labels verschieben sich bei Nachtraegen)
    chat_batch: object | None = None
    document: object | None = None


_project_lock = locking.project_lock  # frueherer Name, von aussen genutzt


def _build_work_items(
    project: Path, cfg: CuratorConfig, ignore_message_state: bool = False, skipped: list[str] | None = None,
    absorbed: list | None = None, unmapped: list | None = None,
) -> tuple[list[_WorkItem], int, int]:
    st = state_mod.load_state(project)
    allowed = chatgpt_source.allowed_project_ids(project)

    if ignore_message_state:
        newest: dict[str, tuple[int, object]] = {}
        for path in sorted(p for _src, d in layout.chat_messages_dirs(project) for p in d.glob("*.json")):
            msg = chatgpt_source.load_message(path)
            if msg is None or (allowed is not None and msg.project_id not in allowed):
                continue
            rev = chatgpt_source._revision_number(path)
            msg.revision = rev
            if msg.message_id not in newest or rev > newest[msg.message_id][0]:
                newest[msg.message_id] = (rev, msg)
        all_messages = [m for _r, m in newest.values()]
        all_messages.sort(key=lambda m: (m.local_date, m.captured_at, m.conversation_id))
        new_messages = all_messages
    else:
        new_messages = chatgpt_source.load_new_messages(project, st, absorbed=absorbed,
                                                        allowed_pids=allowed, unmapped=unmapped)

    day_batches = batching.build_day_batches(new_messages, cfg)
    new_documents = documents_source.load_new_documents(project, st, cfg, skipped=skipped)

    items: list[_WorkItem] = []
    for b in day_batches:
        items.append(
            _WorkItem(kind="chat", label=b.batch_id, char_count=b.total_chars, chat_batch=b,
                      key=f"chat:{b.messages[0].message_id}" if b.messages else f"chat:{b.batch_id}")
        )
    for d in sorted(new_documents, key=lambda d: (d.path.name, d.part)):
        items.append(
            _WorkItem(kind="document",
                      label=f"{d.kind}/{d.path.name}" + (f" (Teil {d.part}/{d.parts})" if d.parts > 1 else ""),
                      char_count=len(d.text), document=d,
                      key=f"doc:{d.state_key}" + (f"#{d.part}" if d.parts > 1 else ""))
        )
    # Mehrfach gescheiterte Einheiten nach hinten (stabil, sonst unveraendert)
    # - aber nur fuer 24 h nach dem letzten Fehlschlag, sonst verhungerten sie
    # bei taeglich neuen Chats still (Review Runde 2).
    def _demoted(it) -> bool:
        f = st.failures.get(it.key) or {}
        if int(f.get("count", 0)) < 2:
            return False
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(f.get("at", ""))
        except ValueError:
            return True
        return age.total_seconds() < 24 * 3600

    items.sort(key=_demoted)

    return items, len(new_messages), len(new_documents)


def dry_run_project(
    project: Path,
    cfg: CuratorConfig,
    integrity_check: Callable[[Path], list] = integrity.check_project,
) -> RunReport:
    items, n_msg, n_docs = _build_work_items(project, cfg)
    report = RunReport(project=project.name, dry_run=True, new_messages=n_msg, new_documents=n_docs)
    blocking = [f for f in integrity_check(project) if f.blocking]
    if blocking:
        report.integrity_findings = [f.summary() for f in blocking]
        report.aborted_reason = "integrity"

    total_chars = 0
    planned_calls = 0
    budget_open = True
    for item in items:
        if item.kind == "chat":
            report.day_batches_planned.append(item.label)
        else:
            report.documents_planned.append(item.label)
        # Geplant (Rueckstand) wird ALLES aufgelistet - Status und Widget zeigen
        # sonst zu wenig (Review Runde 2). Gezaehlt als "dieser Lauf" wird nach
        # denselben Regeln wie in run_project (dort: break).
        if budget_open:
            if planned_calls >= cfg.max_batches_per_run or planned_calls >= cfg.max_llm_calls_per_run:
                budget_open = False
            elif not _fits_budget(total_chars, planned_calls, item, cfg):
                budget_open = False
            else:
                total_chars += item.char_count
                planned_calls += 1

    report.llm_calls_made = planned_calls
    report.total_input_chars = total_chars
    return report


def _tail_at_boundary(text: str, max_chars: int) -> str:
    """Letzte max_chars Zeichen, aber an einer Eintrags-/Abschnittsgrenze
    beginnend (Zeile mit "- " oder "## ") - frueher mitten im Eintrag, das
    Modell sah dann Bruchstuecke ohne Einordnung (Review 2026-09-29)."""
    if len(text) <= max_chars:
        return text
    tail = text[-max_chars:]
    for marker in ("\n## ", "\n- "):
        pos = tail.find(marker)
        if pos != -1:
            return "[… aeltere Eintraege gekuerzt …]" + tail[pos:]
    return tail


def _number_existing_entries(project: Path, logger, report) -> None:
    """Nummerieren ist Komfort: scheitert es (kaputtes Byte, Mount weg beim
    Backup/Journal), wird das Projekt trotzdem kuratiert (Review 2026-09-29)."""
    from . import ids

    try:
        files = ids.assign_missing(project)
        if not files:
            return
        backups = layout.polaris_dir(project) / "backups"
        storage.ensure_dir(backups)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        for name, (old, _new) in files.items():
            storage.atomic_write_text(backups / f"{name}.{stamp}-before-ids", old)
        journal.begin(project, files, {"kind": "ids"})
        journal.write_files(project, files)
        journal.finish(project)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        msg = f"Einmalige Nummerierung uebersprungen: {type(exc).__name__}: {exc}"
        logger.warning(msg)
        report.warnings.append(msg)
        return
    msg = f"Eintraege einmalig nummeriert in: {', '.join(sorted(files))} (Backup .polaris/backups/*-before-ids)"
    logger.info(msg)
    report.warnings.append(msg)


def _finish_interrupted_step(project: Path, st, cfg: CuratorConfig, report: "RunReport", logger) -> bool:
    """False = Projekt in diesem Lauf nicht kuratieren (Journal passt nicht)."""
    try:
        record = journal.pending(project)
        if record is None:
            return True
        journal.replay_files(project, record)
    except journal.JournalConflict as exc:
        msg = (
            f"Unterbrochener Schritt nicht fortsetzbar: {exc}. Projekt wird nicht kuratiert, bis das "
            f"geklaert ist: `polaris-curator journal {project.name}` zeigt den Stand."
        )
        logger.error(msg)
        report.errors.append(msg)
        report.aborted_reason = "journal"
        return False
    update = record.get("state_update") or {}
    if update.get("kind") == "chat":
        st.mark_messages_processed(update["day"], update["message_ids"])
        for mid, rev in (update.get("revisions") or {}).items():
            st.processed_revisions[mid] = max(int(rev), int(st.processed_revisions.get(mid, 1)))
        for mid, keys in (update.get("fed") or {}).items():
            st.fed_paragraphs[mid] = sorted(set(st.fed_paragraphs.get(mid, [])) | set(keys))
    elif update.get("kind") == "document_part":
        st.document_parts[update["state_key"]] = {"hash": update["hash"], "done": update["done"],
                                                  "parts": update["parts"]}
    elif update.get("kind") == "document":
        st.document_parts.pop(update["state_key"], None)
        signature = dict(update.get("signature") or {})
        already = st.file_signature(update["state_key"])
        if already and already.get("hash") == signature.get("hash"):
            signature = already  # schon vermerkt (Abbruch nach dem State): Archivpfad/Anhaenge behalten
        for doc in documents_source.load_new_documents(project, st, cfg):
            if doc.state_key == update["state_key"] and doc.signature.get("hash") == signature.get("hash"):
                try:
                    archived = documents_source.archive_processed_document(
                        project, doc, project / update["planned_archive"]
                    )
                    signature["archive_path"] = str(archived.relative_to(project))
                except Exception as exc:  # noqa: BLE001
                    report.warnings.append(f"{doc.path.name}: Archivierung beim Fortsetzen fehlgeschlagen ({exc})")
        st.mark_file_processed(update["state_key"], signature)
    if update.get("failure_key"):
        st.failures.pop(update["failure_key"], None)
    state_mod.save_state(project, st)
    journal.finish(project)
    msg = "Unterbrochenen Kurationsschritt eines frueheren Laufs abgeschlossen"
    logger.warning(msg)
    report.warnings.append(msg)
    return True


def _note_fed(st, m) -> None:
    keys = getattr(m, "fed_keys", None)
    if keys is None:
        return
    st.fed_paragraphs[m.message_id] = sorted(set(st.fed_paragraphs.get(m.message_id, [])) | set(keys))


def _record_failure(st: state_mod.CuratorState, key: str, msg: str) -> None:
    entry = st.failures.setdefault(key, {"count": 0})
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["last_error"] = msg[:300]
    entry["at"] = datetime.now(timezone.utc).isoformat()


def _fits_budget(total_chars: int, calls_made: int, item: _WorkItem, cfg: CuratorConfig) -> bool:
    """Eine Einheit, die allein groesser ist als das Laufbudget, darf als
    erste des Laufs trotzdem laufen - sonst blockierte sie die Warteschlange
    fuer immer (Review 2026-09-29)."""
    if total_chars + item.char_count <= cfg.max_total_input_chars_per_run:
        return True
    return calls_made == 0 and total_chars == 0


def _provenance_for_chat(batch, cited: list[str] | None = None, warnings: list[str] | None = None) -> str:
    """Herkunft je Aussage (seit 2026-09-29): nur die Nachrichten, auf die sich
    der Eintrag laut Modell stuetzt - und nur solche aus diesem Batch. Fehlt
    die Angabe oder passt keine ID, gilt wie frueher der ganze Batch (nie ein
    Eintrag ohne Herkunft)."""
    known = {m.message_id: m for m in batch.messages}
    chosen = [i for i in dict.fromkeys(cited or []) if i in known]
    note = f"{batch.day}: source_message_ids passen nicht zum Batch - ganzer Batch als Herkunft"
    if cited and not chosen and warnings is not None and note not in warnings:
        warnings.append(note)
    msgs = [known[i] for i in chosen] if chosen else list(batch.messages)
    conv_ids = sorted({m.conversation_id for m in msgs})
    sources = sorted({getattr(m, "source", "chatgpt") for m in msgs}) or ["chatgpt"]
    # batch_ids nur, wenn der Eintrag eine Teilmenge nennt: retract und die
    # Kundentrennung gehen weiter vom ganzen Batch aus (Review 2026-09-29).
    batch_ids = ",".join(m.message_id for m in batch.messages) if chosen and len(msgs) < len(batch.messages) else None
    return merge.build_provenance(
        "+".join(sources),
        conversation_id=",".join(conv_ids),
        message_ids=",".join(m.message_id for m in msgs),
        batch_ids=batch_ids,
        captured_at=msgs[-1].captured_at if msgs else None,
    )


def _provenance_for_document(project: Path, doc, archive_path: Path) -> str:
    # Projektrelativer Archivpfad statt absolutem Inbox-Pfad: die Mail liegt
    # nach der Kuratierung dort, und der Pfad ist auch im Google-Drive-Ordner
    # (z. B. fuer ChatGPT) nachvollziehbar (2026-09-25).
    return merge.build_provenance(
        "document",
        path=str(archive_path.relative_to(project)),
        hash=doc.signature.get("hash", "")[:12],
    )


def run_project(
    project: Path,
    cfg: CuratorConfig,
    llm_fn: Callable[[str, CuratorConfig], LlmResult] = call_llm,
    ignore_message_state: bool = False,
    integrity_check: Callable[[Path], list] = integrity.check_project,
) -> RunReport:
    from . import prompts

    logger = setup_logging(project)
    report = RunReport(project=project.name)

    with _project_lock(project) as acquired:
        if not acquired:
            report.aborted_reason = "bereits ein Lauf aktiv (Lock)"
            logger.warning("Curator-Lauf uebersprungen: bereits aktiv (%s)", project)
            return report

        layout.ensure_inbox_dirs(project)

        st = state_mod.load_state(project)
        if not st.coverage_migrated:
            # Einmalig, VOR dem Fortsetzen eines Journals (Review Runde 5, F2).
            revisions, fed = chatgpt_source.legacy_migration(project, st)
            st.processed_revisions.update(revisions)
            for mid, keys in fed.items():
                st.fed_paragraphs.setdefault(mid, keys)
            st.coverage_migrated = True
            state_mod.save_state(project, st)
        # Unterbrochenen Schritt eines frueheren Laufs vollenden (journal.py).
        if not _finish_interrupted_step(project, st, cfg, report, logger):
            return report

        # Letzte Sperre vor dem kuratierten Wissen (2026-09-29): liegen im
        # Rohbestand Nachrichten, die zu einem anderen Projekt/Chat gehoeren,
        # wird dieses Projekt nicht kuratiert, bis das bereinigt ist.
        blocking = [f for f in integrity_check(project) if f.blocking]
        if blocking:
            report.integrity_findings = [f.summary() for f in blocking]
            report.aborted_reason = "integrity"
            msg = (
                f"Projekttrennung verletzt ({len(blocking)} Befund(e)), Projekt nicht kuratiert: "
                + "; ".join(report.integrity_findings[:3])
            )
            logger.error(msg)
            report.errors.append(msg)
            return report

        # Vorhandene Eintraege ohne Nummer einmalig nummerieren (K/D/L, seit
        # 2026-09-29) - ueber das Journal, also absturzsicher; mit Backup.
        # Erst nach der Integritaetspruefung: ein gesperrtes Projekt bleibt
        # unangetastet.
        _number_existing_entries(project, logger, report)

        st = state_mod.load_state(project)
        absorbed: list = []
        unmapped: list = []
        items, n_msg, n_docs = _build_work_items(
            project, cfg, ignore_message_state=ignore_message_state, skipped=report.warnings, absorbed=absorbed,
            unmapped=unmapped,
        )
        if unmapped:
            msg = (f"{len(unmapped)} Nachricht(en) mit einer project.id, die diesem Ordner nicht zugeordnet ist, "
                   "nicht kuratiert (Zuordnung pruefen: polaris projects / polaris map)")
            logger.warning(msg)
            report.warnings.append(msg)
        if absorbed:
            # Inhalt schon abgedeckt (anderer Erfassungsweg, andere Formatierung,
            # Dublette): ohne Modellaufruf als verarbeitet vermerken.
            for a in absorbed:
                st.mark_messages_processed(a.local_date, [a.message_id])
                st.processed_revisions[a.message_id] = max(a.revision, int(st.processed_revisions.get(a.message_id, 1)))
                st.fed_paragraphs.setdefault(a.message_id, [])  # nichts selbst beigetragen
            state_mod.save_state(project, st)
            logger.info("%d Nachricht(en) bereits abgedeckt, ohne Modellaufruf vermerkt", len(absorbed))
        for it in items:
            f = st.failures.get(it.key)
            if f and f.get("count", 0) >= 2:
                report.warnings.append(
                    f"{it.label} scheitert wiederholt ({f['count']}x, zuletzt: {f.get('last_error', '')[:120]})"
                )
        report.new_messages = n_msg
        report.new_documents = n_docs
        for item in items:
            (report.day_batches_planned if item.kind == "chat" else report.documents_planned).append(
                item.label
            )

        logger.info(
            "Lauf gestartet project=%s neue_messages=%d neue_dokumente=%d work_items=%d",
            project.name,
            n_msg,
            n_docs,
            len(items),
        )

        review_off = False  # nach Ausfall des Pruefers fuer diesen Lauf aus
        for item in items:
            if report.llm_calls_made >= cfg.max_batches_per_run:
                logger.info("Budget max_batches_per_run erreicht, Lauf endet hier")
                break
            if report.llm_calls_made >= cfg.max_llm_calls_per_run:
                logger.info("Budget max_llm_calls_per_run erreicht, Lauf endet hier")
                break
            if not _fits_budget(report.total_input_chars, report.llm_calls_made, item, cfg):
                logger.info(
                    "Budget max_total_input_chars_per_run erreicht bei %s, Lauf endet hier", item.label
                )
                break
            if item.char_count > cfg.max_total_input_chars_per_run:
                msg = f"{item.label} ist allein groesser als das Laufbudget ({item.char_count} Zeichen) - laeuft trotzdem allein"
                logger.warning(msg)
                report.warnings.append(msg)

            knowledge_text = (
                layout.knowledge_path(project).read_text(encoding="utf-8")
                if layout.knowledge_path(project).is_file()
                else ""
            )
            decisions_text = (
                layout.decisions_path(project).read_text(encoding="utf-8")
                if layout.decisions_path(project).is_file()
                else ""
            )
            open_loops_text = (
                layout.open_loops_path(project).read_text(encoding="utf-8")
                if layout.open_loops_path(project).is_file()
                else ""
            )
            # Kontext fuer diesen Aufruf (context.py, seit 2026-09-30): ohne
            # Herkunftskommentare; grosse Dateien als Verzeichnis + Volltext
            # des Relevanten. Die Dateien selbst bleiben unveraendert.
            if item.kind == "chat":
                query = "\n".join(f"{m.conversation_title or ''}\n{m.content}" for m in item.chat_batch.messages)
            else:
                query = f"{item.document.path.name}\n{item.document.text}"
            ctx = context_mod.build(knowledge_text, decisions_text, open_loops_text, query,
                                    full_chars=getattr(cfg, "full_context_chars", 80000),
                                    max_chars=cfg.max_curated_context_chars,
                                    index_cap=getattr(cfg, "index_cap", context_mod.INDEX_CAP))
            knowledge_text, decisions_text, open_loops_text = ctx.knowledge, ctx.decisions, ctx.open_loops
            for note in ctx.notes:
                logger.info("Kontext %s: %s", item.label, note)
            # Letzte Sicherung (Abschnitt 21): von hinten kuerzen, falls der
            # Kontext trotzdem die Grenze sprengt.
            max_ctx = cfg.max_curated_context_chars
            for fname, ftext in (("knowledge.md", knowledge_text), ("decisions.md", decisions_text),
                                 ("open-loops.md", open_loops_text)):
                note = (f"Kontextgrenze: {fname} hat {len(ftext)} Zeichen, das Modell sieht nur die letzten "
                        f"{max_ctx} - aeltere Eintraege kann es weder abgleichen noch ersetzen (Dubletten-"
                        "Risiko). Verdichten (polaris-curator compact) oder max_curated_context_chars erhoehen.")
                if max_ctx and len(ftext) > max_ctx and note not in report.warnings:
                    logger.warning(note)
                    report.warnings.append(note)
            knowledge_text = _tail_at_boundary(knowledge_text, max_ctx)
            decisions_text = _tail_at_boundary(decisions_text, max_ctx)
            open_loops_text = _tail_at_boundary(open_loops_text, max_ctx)

            if item.kind == "chat":
                prompt = prompts.build_chat_batch_prompt(
                    item.chat_batch, knowledge_text, decisions_text, open_loops_text
                )
            else:
                prompt = prompts.build_document_prompt(
                    item.document, knowledge_text, decisions_text, open_loops_text
                )

            images = item.document.images if item.kind == "document" else []
            try:
                # images nur uebergeben, wenn vorhanden - haelt injizierte
                # llm_fn-Varianten mit (prompt, cfg)-Signatur kompatibel.
                result = llm_fn(prompt, cfg, images=images) if images else llm_fn(prompt, cfg)
            except LlmUnavailableError as exc:
                msg = f"LLM nicht erreichbar bei {item.label}: {exc}"
                logger.error(msg)
                report.errors.append(msg)
                report.aborted_reason = "llm_unavailable"
                break  # systemisch - weitere Versuche waeren sinnlos
            except LlmOutputError as exc:
                msg = f"Ungueltige LLM-Ausgabe bei {item.label}: {exc}"
                logger.error(msg)
                report.errors.append(msg)
                # Der Aufruf hat stattgefunden und zaehlt gegen das Budget
                # (vorher nicht: 8 echte Aufrufe bei llm_calls_made=0).
                report.llm_calls_made += 1
                report.total_input_chars += item.char_count
                _record_failure(st, item.key, msg)
                state_mod.save_state(project, st)
                continue  # nur dieser Batch betroffen, naechster Batch versucht es

            report.llm_calls_made += 1
            report.total_input_chars += item.char_count

            try:
                change = change_schema.validate_change_object(result.data)
            except change_schema.ChangeValidationError as exc:
                msg = f"Aenderungsobjekt ungueltig bei {item.label}: {exc}"
                logger.error(msg)
                report.errors.append(msg)
                _record_failure(st, item.key, msg)
                state_mod.save_state(project, st)
                continue

            failed_checks = change_schema.failed_self_review_checks(change)
            if failed_checks:
                note = (change.get("self_review") or {}).get("notes") or ""
                msg = (
                    f"Self-Review nicht bestanden bei {item.label}: {', '.join(failed_checks)}"
                    + (f" ({note})" if note else "")
                )
                logger.error(msg)
                report.errors.append(msg)
                _record_failure(st, item.key, msg)
                state_mod.save_state(project, st)
                continue  # nur dieser Batch betroffen, wird beim naechsten Lauf erneut versucht

            # Unabhaengige Pruefung (review.py): markiert, verwirft nichts. Nicht,
            # wenn Claude schon kuratiert hat (dann waere es keine zweite Meinung).
            findings: list[dict] = []
            reviewer = getattr(cfg, "reviewer_model", None)
            has_images = item.kind == "document" and bool(item.document.images)
            if reviewer and not review_off and not has_images and not str(result.model).startswith("claude:"):
                if item.kind == "chat":
                    review_source = prompts._format_chat_messages(item.chat_batch)
                else:
                    review_source = f"Dokument {item.document.path.name}:\n{item.document.text}"
                try:
                    report.review_calls_made += 1
                    findings = review_mod.review(change, review_source, project, cfg,
                                                 lambda p, m, c, s: llm_mod._run_claude(p, m, c, s))
                except Exception as exc:  # noqa: BLE001 - die Pruefung darf das Kuratieren nie aufhalten
                    findings = []
                    for el in (change.get("knowledge_updates") or []) + (change.get("decisions_add") or []) + (
                            change.get("open_loops_add") or []):
                        el.pop("review_note", None)
                    if isinstance(exc, llm_mod.LlmUnavailableError):
                        review_off = True  # Limit/Ausfall: fuer den Rest des Laufs nicht erneut versuchen
                    msg = (f"Prüfung ausgefallen bei {item.label} ({type(exc).__name__}: {str(exc)[:120]}) - "
                           "ungeprüft geschrieben" + ("; für den Rest des Laufs ausgesetzt" if review_off else ""))
                    logger.warning(msg)
                    report.warnings.append(msg)

            planned_archive = (
                documents_source.planned_archive_path(project, item.document)
                if item.kind == "document"
                else None
            )
            if item.kind == "chat":
                def provenance(cited, _batch=item.chat_batch, _warn=report.warnings):
                    return _provenance_for_chat(_batch, cited, _warn)
            else:
                provenance = _provenance_for_document(project, item.document, planned_archive)

            if item.kind == "chat":
                state_update = {"kind": "chat", "day": item.chat_batch.day,
                                "message_ids": list(item.chat_batch.message_ids), "failure_key": item.key,
                                "revisions": {m.message_id: getattr(m, "revision", 1)
                                              for m in item.chat_batch.messages},
                                "fed": {m.message_id: getattr(m, "fed_keys", None) or []
                                        for m in item.chat_batch.messages}}
            elif not item.document.is_last_part:
                state_update = {"kind": "document_part", "state_key": item.document.state_key,
                                "hash": item.document.signature["hash"], "done": item.document.part,
                                "parts": item.document.parts, "failure_key": item.key}
            else:
                state_update = {"kind": "document", "state_key": item.document.state_key,
                                "signature": dict(item.document.signature),
                                "planned_archive": str(planned_archive.relative_to(project)),
                                "failure_key": item.key}

            def _journaled_write(files, _update=state_update):
                if files:  # leerer Schritt: nichts zu schuetzen, kein Journal auf den Mount
                    journal.begin(project, files, _update)
                    journal.write_files(project, files)

            # Nur aus dem Verzeichnis bekannte Eintraege nicht abloesen: deren
            # Details hat das Modell nie gesehen (context.py). Stattdessen
            # anhaengen - eine Dublette raeumt die Verdichtung auf.
            for upd in change.get("knowledge_updates") or []:
                if isinstance(upd.get("content"), str):
                    upd["content"] = upd["content"].replace(context_mod.INDEX_MARK, "").strip() or upd["content"]
                rid = str(upd.get("replaces_id") or "").strip().upper()
                if upd.get("mode") == "replace" and rid in ctx.index_only:
                    upd["mode"] = "append"
                    upd["replaces_id"] = None
                    report.warnings.append(f"{item.label}: {rid} nur im Verzeichnis gesehen - neuer Eintrag statt Abloesung")

            try:
                merge_result = merge.apply_change_object(project, change, provenance, writer=_journaled_write)
            except Exception as exc:  # noqa: BLE001 - Merge darf den Lauf nicht crashen
                msg = f"Merge fehlgeschlagen bei {item.label}: {exc}"
                logger.error(msg)
                report.errors.append(msg)
                _record_failure(st, item.key, msg)
                state_mod.save_state(project, st)
                if journal.pending(project) is not None:
                    # Mitten im Schreiben abgebrochen: naechster Lauf vollendet es.
                    report.aborted_reason = "journal_pending"
                    break
                continue

            report.warnings.extend(merge_result.warnings)
            if item.kind == "document" and getattr(item.document, "truncation_note", None):
                report.warnings.append(item.document.truncation_note)
            if findings:
                summary = {}
                for key, el, _t in review_mod._items(change, lambda _r: None):
                    summary[key] = str(el.get("content") or el.get("decision") or el.get("text") or "")
                try:
                    review_mod.record(project, item.label, reviewer, findings, summary)
                except OSError as exc:
                    logger.warning("Pruefprotokoll nicht gespeichert: %s", exc)
                majors = [f for f in findings if f.get("verdict") == "major"]
                minors = sum(1 for f in findings if f.get("verdict") == "minor")
                logger.info("Pruefung %s: %d Befund(e), davon %d schwer", item.label, len(majors) + minors, len(majors))
                if majors:
                    # Ohne Inhaltsauszug (Benachrichtigungen): Details stehen am
                    # Eintrag und in .polaris/review-findings.json.
                    report.warnings.append(f"Prüfhinweis {item.label}: {len(majors)} Eintrag/Einträge mit schwerer "
                                           "Beanstandung markiert (siehe 'Prüfhinweis' in den Dateien)")
            report.knowledge_changed = report.knowledge_changed or merge_result.knowledge_changed
            report.decisions_changed = report.decisions_changed or merge_result.decisions_changed
            report.open_loops_changed = report.open_loops_changed or merge_result.open_loops_changed

            if item.kind == "chat":
                st.mark_messages_processed(item.chat_batch.day, item.chat_batch.message_ids)
                for m in item.chat_batch.messages:
                    st.processed_revisions[m.message_id] = max(
                        getattr(m, "revision", 1), int(st.processed_revisions.get(m.message_id, 1)))
                    _note_fed(st, m)
                report.day_batches_processed.append(item.label)
            elif not item.document.is_last_part:
                st.document_parts[item.document.state_key] = {
                    "hash": item.document.signature["hash"], "done": item.document.part,
                    "parts": item.document.parts}
                report.documents_processed.append(item.label)
            else:
                st.document_parts.pop(item.document.state_key, None)
                try:
                    archived_path = documents_source.archive_processed_document(
                        project, item.document, planned_archive
                    )
                except Exception as exc:  # noqa: BLE001 - Wissen ist geschrieben; Datei bleibt sichtbar liegen
                    # Frueher: kein State -> naechster Lauf kuratierte das Dokument
                    # doppelt. Jetzt gilt es als verarbeitet; die Datei bleibt in der
                    # Inbox und wird als "identisch bereits verarbeitet" gemeldet.
                    msg = f"Archivierung fehlgeschlagen bei {item.label}: {exc} - Datei bleibt in der Inbox"
                    logger.warning(msg)
                    report.warnings.append(msg)
                    archived_path = None

                archived_signature = dict(item.document.signature)
                if archived_path is not None:
                    archived_signature["archive_path"] = str(archived_path.relative_to(project))
                if item.document.attachments:
                    archived_signature["attachments"] = [
                        {"file": a.filename, "handling": a.handling, **({"note": a.note} if a.note else {})}
                        for a in item.document.attachments
                    ]
                st.mark_file_processed(item.document.state_key, archived_signature)
                report.documents_processed.append(item.label)

            st.failures.pop(item.key, None)
            state_mod.save_state(project, st)
            journal.finish(project)

            logger.info(
                "Batch verarbeitet label=%s model=%s tokens=%s chars=%d "
                "knowledge_changed=%s decisions_changed=%s open_loops_changed=%s",
                item.label,
                result.model,
                result.tokens_used,
                item.char_count,
                merge_result.knowledge_changed,
                merge_result.decisions_changed,
                merge_result.open_loops_changed,
            )

        if report.documents_processed:
            from . import docs_index
            try:
                if docs_index.write_index(project):
                    logger.info("dokumente-index.md aktualisiert")
            except Exception as exc:  # noqa: BLE001 - Index ist Komfort, darf den Lauf nicht scheitern lassen
                logger.warning("dokumente-index.md konnte nicht geschrieben werden: %s", exc)

        # Tage, fuer die in diesem Lauf alle geplanten Batches erfolgreich
        # verarbeitet wurden, informativ als "complete" markieren.
        processed_days = {label.split("#")[0] for label in report.day_batches_processed}
        planned_days = {label.split("#")[0] for label in report.day_batches_planned}
        for day in planned_days:
            day_batches_for_day = [l for l in report.day_batches_planned if l.split("#")[0] == day]
            if all(l in report.day_batches_processed for l in day_batches_for_day):
                st.mark_day_complete_if_no_gap(day)
        if processed_days:
            state_mod.save_state(project, st)

        logger.info(
            "Lauf beendet project=%s llm_calls=%d input_chars=%d fehler=%d",
            project.name,
            report.llm_calls_made,
            report.total_input_chars,
            len(report.errors),
        )

    return report


LAST_RUN_NAME = "last-run.json"


def record_last_run(project: Path, cfg: CuratorConfig, report: RunReport, important) -> None:
    """Ergebnis eines Laufs fuer den Health Check (systemhealth.check_curation,
    seit 2026-09-29): Fehler, wichtige Warnungen und was danach noch offen ist.
    Vorher sah das Widget nur, ob der Timer-Dienst sauber endete - wiederholte
    Fehlschlaege, liegengebliebene Dokumente oder wachsender Rueckstand blieben
    gruen (Architektur-Review)."""
    try:
        items, _n_msg, _n_docs = _build_work_items(project, cfg)
    except Exception:  # noqa: BLE001 - nur Anzeige
        items = []
    days = sorted(it.chat_batch.day for it in items if it.kind == "chat" and it.chat_batch is not None)
    ok = not report.errors and report.aborted_reason is None
    record = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ok": ok,
        "aborted_reason": report.aborted_reason,
        "errors": report.errors[:5],
        "warnings": [w for w in report.warnings if important(w)][:10],
        "pending_chat_batches": len(days),
        "pending_documents": sum(1 for it in items if it.kind == "document"),
        "oldest_pending_day": days[0] if days else None,
    }
    storage.atomic_write_json(layout.polaris_dir(project) / LAST_RUN_NAME, record)
    if ok:
        # Unter dem Projekt-Lock: sonst ueberschriebe dieses Speichern den State
        # eines parallel laufenden Befehls (Review 2026-09-29, L2).
        with locking.project_lock(project) as acquired:
            if acquired:
                st = state_mod.load_state(project)
                st.last_successful_run = record["at"]
                state_mod.save_state(project, st)

