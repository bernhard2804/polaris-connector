from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import config as config_mod
from . import documents_source, pipeline, state as state_mod, storage
from .config import CuratorConfig


def _resolve_project(name_or_path: str) -> Path:
    path = config_mod.find_project_path(name_or_path)
    if path is None:
        print(
            f"Fehler: Projekt '{name_or_path}' nicht gefunden (weder als bekanntes "
            "gemapptes Polaris-Projekt noch als existierender Pfad).",
            file=sys.stderr,
        )
        sys.exit(1)
    return path


def _report_payload(report: pipeline.RunReport) -> dict:
    return dataclasses.asdict(report)


def _status_payload(project_id: str | None, project: Path) -> dict:
    cfg = config_mod.load_config(project)
    st = state_mod.load_state(project)
    report = pipeline.dry_run_project(project, cfg)

    doc_counts = {"documents": 0, "mail": 0, "notes": 0}
    for label in report.documents_planned:
        kind = label.split("/", 1)[0]
        if kind in doc_counts:
            doc_counts[kind] += 1

    total_units = len(report.day_batches_planned) + len(report.documents_planned)
    return {
        "id": project_id,
        "name": project.name,
        "path": str(project),
        "new_messages": report.new_messages,
        "open_day_batches": len(report.day_batches_planned),
        "new_documents": doc_counts["documents"],
        "new_emails": doc_counts["mail"],
        "new_notes": doc_counts["notes"],
        "pending_units": total_units,
        "planned_llm_calls": report.llm_calls_made,
        "backlog_units": max(0, total_units - report.llm_calls_made),
        "estimated_input_chars": report.total_input_chars,
        "last_successful_run": st.last_successful_run,
        "curated_files": {
            "knowledge": str(project / "knowledge.md"),
            "decisions": str(project / "decisions.md"),
            "open_loops": str(project / "open-loops.md"),
        },
    }


def cmd_status(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    if args.json:
        print(json.dumps(_status_payload(None, project), ensure_ascii=False))
        return 0

    cfg = config_mod.load_config(project)
    st = state_mod.load_state(project)
    report = pipeline.dry_run_project(project, cfg)

    doc_counts = {"documents": 0, "mail": 0, "notes": 0}
    for label in report.documents_planned:
        kind = label.split("/", 1)[0]
        if kind in doc_counts:
            doc_counts[kind] += 1

    print("Polaris Curator")
    print()
    print(f"Project: {project.name}")
    print()
    print(f"New ChatGPT messages: {report.new_messages}")
    print(f"Open day batches: {len(report.day_batches_planned)}")
    print(f"New documents: {doc_counts['documents']}")
    print(f"New emails: {doc_counts['mail']}")
    print(f"New notes: {doc_counts['notes']}")
    print()
    print("Estimated input:")
    print(f"{report.total_input_chars:,} chars")
    print()
    print("Planned LLM calls:")
    print(f"{report.llm_calls_made} / max {cfg.max_batches_per_run}")
    print()
    print(f"Last successful run: {st.last_successful_run or 'noch nie'}")
    return 0


def cmd_status_all(args: argparse.Namespace) -> int:
    from . import status_cache

    projects = config_mod.list_known_projects()
    ordered = sorted(projects.items(), key=lambda kv: kv[1].name.casefold())
    if getattr(args, "no_cache", False):
        payloads = [_status_payload(project_id, path) for project_id, path in ordered]
    else:
        payloads = status_cache.payloads(ordered, _status_payload)
    summary = {
        "project_count": len(payloads),
        "pending_projects": sum(1 for item in payloads if item["pending_units"] > 0),
        "new_messages": sum(item["new_messages"] for item in payloads),
        "new_documents": sum(
            item["new_documents"] + item["new_emails"] + item["new_notes"]
            for item in payloads
        ),
        "pending_units": sum(item["pending_units"] for item in payloads),
        "planned_llm_calls": sum(item["planned_llm_calls"] for item in payloads),
        "backlog_units": sum(item["backlog_units"] for item in payloads),
        "estimated_input_chars": sum(item["estimated_input_chars"] for item in payloads),
    }
    from . import health as health_mod

    health = health_mod.collect(projects, getattr(config_mod.load_config(), "max_days_without_chatgpt", 3))
    result = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projects": payloads,
        "summary": summary,
        "health": health,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0

    print("Polaris Curator")
    print()
    for item in payloads:
        print(
            f"{item['name']}: {item['new_messages']} Messages, "
            f"{item['new_documents'] + item['new_emails'] + item['new_notes']} Dokumente, "
            f"{item['planned_llm_calls']} geplante LLM-Calls"
        )
    print()
    print(
        f"{summary['pending_projects']} von {summary['project_count']} Projekten offen, "
        f"{summary['planned_llm_calls']} geplante LLM-Calls"
    )
    print(f"Letzte ChatGPT-Nachricht: {health['last_chatgpt_message_at'] or 'keine'}")
    for warning in health["warnings"]:
        print(f"WARNUNG: {warning}")
    return 0


def _print_report(report: pipeline.RunReport) -> None:
    print(f"Project: {report.project}")
    if report.dry_run:
        print("(dry-run - es wurde nichts geschrieben)")
    print(f"Neue Messages: {report.new_messages} | Neue Dokumente: {report.new_documents}")
    print(
        f"Geplante Tagesbatches: {len(report.day_batches_planned)} "
        f"({', '.join(report.day_batches_planned) or '-'})"
    )
    print(
        f"Geplante Dokumente: {len(report.documents_planned)} "
        f"({', '.join(report.documents_planned) or '-'})"
    )
    print(f"LLM-Calls: {report.llm_calls_made} | Input-Zeichen: {report.total_input_chars:,}")
    if not report.dry_run:
        print(
            f"Verarbeitet: {len(report.day_batches_processed)} Tagesbatches, "
            f"{len(report.documents_processed)} Dokumente"
        )
        print(
            f"Geaendert: knowledge.md={report.knowledge_changed} "
            f"decisions.md={report.decisions_changed} open-loops.md={report.open_loops_changed}"
        )
    if report.warnings:
        print("Warnungen:")
        for w in report.warnings:
            print(f"  - {w}")
    if report.errors:
        print("Fehler:")
        for e in report.errors:
            print(f"  - {e}")
    if report.aborted_reason:
        print(f"Lauf abgebrochen: {report.aborted_reason}")


def cmd_run(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    cfg = config_mod.load_config(project)

    if args.dry_run:
        report = pipeline.dry_run_project(project, cfg)
    else:
        report = pipeline.run_project(project, cfg)
        _record(project, cfg, report)

    ok = not report.errors and report.aborted_reason is None
    if args.json:
        print(json.dumps({"ok": ok, "report": _report_payload(report)}, ensure_ascii=False))
    else:
        _print_report(report)
    return 0 if ok or report.dry_run else 1


# Exit-Code fuer voruebergehende Fehler (kein Netz/LLM nicht erreichbar,
# Projektordner nicht eingehaengt). Die systemd-Unit startet den Lauf nur
# bei diesem Code spaeter erneut (RestartForceExitStatus), nicht bei
# inhaltlichen Batch-Fehlern, die ohnehin der naechste regulaere Lauf
# wiederholt.
EXIT_TEMPFAIL = 75


def _auto_compact(path, cfg, args) -> None:
    """Verdichtet knowledge.md im Timer-Lauf, wenn gross genug und faellig.
    Fehler verhindern nie den restlichen Lauf."""
    from . import compact
    from .llm import call_llm

    try:
        if not compact.due(path, cfg):
            return
        result = compact.compact_project(path, cfg, call_llm)
        compact.record_run(path, result)
        if not args.json:
            _print_compaction(path.name, result, False)
    except Exception as exc:  # noqa: BLE001
        print(f"{path.name}: automatische Verdichtung übersprungen: {exc}", file=sys.stderr)


# Warnungen eines Laufs, die der Nutzer sehen muss (Benachrichtigung und
# Health-Widget ueber curator-last-run.json); der Rest ist Protokoll.
_IMPORTANT = ("scheitert wiederholt", "bleibt liegen", "nicht unterstuetzt", "gekuerzt kuratiert",
              "nicht zugeordnet", "Kontextgrenze", "nicht kuratiert", "Prüfhinweis", "Prüfung ausgefallen")


def _important_warning(w: str) -> bool:
    return any(k in w for k in _IMPORTANT)


def _record(path, cfg, report) -> None:
    if report.aborted_reason == "bereits ein Lauf aktiv (Lock)":
        return  # der andere Lauf schreibt sein eigenes Ergebnis
    try:
        pipeline.record_last_run(path, cfg, report, _important_warning)
    except OSError as exc:
        print(f"{path.name}: Laufergebnis nicht gespeichert: {exc}", file=sys.stderr)


def _auto_review_loops(path, cfg, args) -> None:
    """Offene Punkte woechentlich pruefen (loops_review.py). Fehler
    verhindern nie den restlichen Lauf."""
    from . import loops_review
    from .llm import call_llm

    try:
        if not loops_review.due(path, cfg):
            return
    except Exception as exc:  # noqa: BLE001
        print(f"{path.name}: Prüfung offener Punkte übersprungen: {exc}", file=sys.stderr)
        return
    try:
        result = loops_review.review(path, cfg, call_llm)
    except Exception as exc:  # noqa: BLE001
        # Auch ein Fehlschlag zaehlt als Versuch - sonst fragte jeder
        # taegliche Lauf erneut das Modell (Review 2026-09-30).
        result = loops_review.ReviewResult(rejected=[f"Pruefung fehlgeschlagen: {exc}"])
        print(f"{path.name}: Prüfung offener Punkte übersprungen: {exc}", file=sys.stderr)
    try:
        loops_review.record_run(path, result)
    except OSError as exc:
        print(f"{path.name}: Prüfungsstand nicht gespeichert: {exc}", file=sys.stderr)
    if not args.json:
        _print_loops_review(path.name, result, False)


def _print_loops_review(name: str, result, dry_run: bool) -> None:
    verb = "würde" if dry_run else "hat"
    print(f"{name}: Prüfung offener Punkte {verb} {len(result.closed)} von {result.open_before} geschlossen"
          + (f" ({', '.join(result.closed)})" if result.closed else ""))
    for lid, d in zip(result.closed, getattr(result, "details", []) or []):
        print(f"  {lid}: {d}")
    for r in result.rejected:
        print(f"  nicht übernommen: {r}")
    if result.notes:
        print(f"  Hinweis des Modells: {result.notes}")


def cmd_review_loops(args: argparse.Namespace) -> int:
    from . import loops_review
    from .llm import call_llm

    project = _resolve_project(args.project)
    cfg = config_mod.load_config(project)
    try:
        result = loops_review.review(project, cfg, call_llm, dry_run=args.dry_run)
    except loops_review.LoopsReviewError as exc:
        print(f"Prüfung verworfen: {exc}")
        return 1
    _print_loops_review(project.name, result, args.dry_run)
    if not args.dry_run:
        loops_review.record_run(project, result)
    return 0


def cmd_run_all(args: argparse.Namespace) -> int:
    projects = config_mod.list_known_projects()
    if not projects:
        mapped = config_mod.count_mapped_projects()
        if mapped:
            msg = (
                f"{mapped} Projekte gemappt, aber keiner der Projektordner ist erreichbar "
                "(Projekt-Laufwerk nicht eingehaengt?)."
            )
            if args.json:
                print(json.dumps({"ok": False, "reports": [], "error": msg}, ensure_ascii=False))
            else:
                print(msg)
            return EXIT_TEMPFAIL
        if args.json:
            print(json.dumps({"ok": True, "reports": []}))
        else:
            print("Keine bekannten Projekte (siehe ~/.config/polaris/projects.yaml).")
        return 0

    exit_code = 0
    reports = []
    for project_id, path in sorted(projects.items(), key=lambda kv: kv[1].name):
        cfg = config_mod.load_config(path)
        if not args.json:
            print(f"=== {path.name} ({project_id}) ===")
        try:
            report = pipeline.run_project(path, cfg)
            _record(path, cfg, report)
        except Exception as exc:  # noqa: BLE001 - ein Projekt darf die anderen nicht aufhalten
            # Review Runde 3: ein kaputter State oder ein Mount-Fehler brach sonst
            # run-all fuer alle folgenden Projekte ab, ohne Benachrichtigung.
            report = pipeline.RunReport(project=path.name)
            report.errors.append(f"Lauf abgebrochen: {type(exc).__name__}: {exc}")
            report.aborted_reason = "error:" + type(exc).__name__
            if isinstance(exc, OSError):
                report.aborted_reason = "error:OSError"  # z. B. Mount weg: spaeter erneut
        reports.append({"id": project_id, "path": str(path), "report": _report_payload(report)})
        if not args.json:
            _print_report(report)
            print()
        if not report.aborted_reason:
            _auto_compact(path, cfg, args)
            _auto_review_loops(path, cfg, args)
        if report.aborted_reason in ("llm_unavailable", "error:OSError"):
            exit_code = EXIT_TEMPFAIL
        elif (report.errors or report.aborted_reason) and exit_code == 0:
            exit_code = 1
    from . import health as health_mod

    health = health_mod.collect(projects, getattr(config_mod.load_config(), "max_days_without_chatgpt", 3))
    for r in reports:
        for w in r["report"].get("warnings") or []:
            if _important_warning(w):
                health["warnings"].append(f"{Path(r['path']).name}: {w}")
    for r in reports:
        reason = r["report"].get("aborted_reason") or ""
        if reason == "journal" or reason.startswith("error:"):
            first = (r["report"].get("errors") or [""])[0]
            health["warnings"].append(f"{Path(r['path']).name}: nicht kuratiert - {first[:200]}")
    blocked = [r["path"] for r in reports if r["report"].get("aborted_reason") == "integrity"]
    if blocked:
        health["warnings"].insert(
            0,
            "Projekttrennung verletzt, nicht kuratiert: "
            + ", ".join(Path(p).name for p in blocked)
            + " (polaris-curator health, Quarantäne/retract)",
        )
    if health["warnings"]:
        for warning in health["warnings"]:
            print(f"WARNUNG: {warning}", file=sys.stderr)
        if not args.json:  # Timer-Lauf; das Widget zeigt Warnungen selbst an
            health_mod.notify_once_per_day(health)
    if args.json:
        print(json.dumps({"ok": exit_code == 0, "reports": reports, "health": health}, ensure_ascii=False))
    return exit_code


def cmd_backfill(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    cfg = config_mod.load_config(project)
    print(
        "Achtung: backfill verarbeitet ChatGPT-Messages unabhaengig vom bisherigen "
        "State erneut. Das kann zu redundanten Eintraegen in knowledge.md fuehren, "
        "wenn diese Nachrichten bereits kuratiert wurden."
    )
    report = pipeline.run_project(project, cfg, ignore_message_state=True)
    _print_report(report)
    return 1 if report.errors else 0


def cmd_pending(args: argparse.Namespace) -> int:
    project = _resolve_project(args.project)
    cfg = config_mod.load_config(project)
    report = pipeline.dry_run_project(project, cfg)

    remaining_batches = report.day_batches_planned[report.llm_calls_made :]
    print(f"Project: {project.name}")
    print(f"Geplant fuer naechsten Lauf (innerhalb Budget): {report.llm_calls_made} Einheiten")
    print(f"Darueber hinaus wartend: {len(remaining_batches)} Tagesbatches")
    for b in remaining_batches:
        print(f"  - {b}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="polaris-curator")
    sub = parser.add_subparsers(dest="command", required=True)

    status_p = sub.add_parser("status", help="Status eines Projekts anzeigen")
    status_p.add_argument("project")
    status_p.add_argument("--json", action="store_true", help="Maschinenlesbares JSON ausgeben")
    status_p.set_defaults(func=cmd_status)

    status_all_p = sub.add_parser("status-all", help="Status aller bekannten Projekte anzeigen")
    status_all_p.add_argument("--json", action="store_true", help="Maschinenlesbares JSON ausgeben")
    status_all_p.add_argument("--no-cache", action="store_true",
                              help="Zwischenspeicher umgehen und alle Projekte neu berechnen")
    status_all_p.set_defaults(func=cmd_status_all)

    run_p = sub.add_parser("run", help="Curator-Lauf fuer ein Projekt")
    run_p.add_argument("project")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--json", action="store_true", help="Maschinenlesbares JSON ausgeben")
    run_p.set_defaults(func=cmd_run)

    run_all_p = sub.add_parser("run-all", help="Curator-Lauf fuer alle bekannten Projekte")
    run_all_p.add_argument("--json", action="store_true", help="Maschinenlesbares JSON ausgeben")
    run_all_p.set_defaults(func=cmd_run_all)

    backfill_p = sub.add_parser(
        "backfill", help="Historischen ChatGPT-Bestand erneut verarbeiten (kann Redundanz erzeugen)"
    )
    backfill_p.add_argument("project")
    backfill_p.set_defaults(func=cmd_backfill)

    pending_p = sub.add_parser("pending", help="Wartende Batches/Dokumente jenseits des Budgets anzeigen")
    pending_p.add_argument("project")
    pending_p.set_defaults(func=cmd_pending)

    compact_p = sub.add_parser("compact", help="knowledge.md verdichten (Dubletten/Überholtes)")
    compact_p.add_argument("project")
    compact_p.add_argument("--dry-run", action="store_true", help="nur Plan anzeigen")
    compact_p.add_argument("--show-plan", action="store_true", help="mit --dry-run: Plan als JSON")
    compact_p.set_defaults(func=cmd_compact)

    loops_p = sub.add_parser("review-loops", help="offene Punkte gegen den Wissensstand pruefen, Erledigtes schliessen")
    loops_p.add_argument("project")
    loops_p.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts schreiben")
    loops_p.set_defaults(func=cmd_review_loops)

    llm_p = sub.add_parser("llm-check", help="Modell-Einrichtung pruefen (Runtime, CLI, Anmeldung)")
    llm_p.add_argument("--call", action="store_true", help="winzigen echten Aufruf machen (Codex: ca. 20k Token Vorlauf)")
    llm_p.set_defaults(func=cmd_llm_check)

    health_p = sub.add_parser("health", help="Health-Check der eigenen Bausteine (Widget)")
    health_p.add_argument("--json", action="store_true")
    health_p.add_argument("--all", action="store_true", help="alle Einzelpruefungen statt der Gruppen zeigen")
    health_p.set_defaults(func=cmd_health)

    index_p = sub.add_parser(
        "index-documents", help="dokumente-index.md neu aufbauen (Projekt oder --all)"
    )
    index_p.add_argument("project", nargs="?")
    index_p.add_argument("--all", action="store_true", help="alle bekannten Projekte")
    index_p.set_defaults(func=cmd_index_documents)

    export_p = sub.add_parser(
        "export",
        help="kuratiertes Projektwissen als ZIP fuer einen anderen Anbieter (ohne Rohchats)",
    )
    export_p.add_argument("project", nargs="?")
    export_p.add_argument("--all", action="store_true", help="alle bekannten Projekte")
    export_p.add_argument("--output", "-o", help="Ziel-ZIP (Default: ~/Downloads/polaris-projektwissen-…zip)")
    export_p.add_argument(
        "--with-archive", action="store_true", help="auch knowledge-archiv.md (nur Historie)"
    )
    export_p.add_argument("--json", action="store_true", help="Maschinenlesbares JSON ausgeben")
    export_p.set_defaults(func=cmd_export)

    reclass_p = sub.add_parser(
        "reclassify",
        help="KI-Antworten in knowledge.md nach Regel 2a neu einstufen (erst Plan, dann --apply)",
    )
    reclass_p.add_argument("project")
    reclass_p.add_argument("--apply", action="store_true", help="gespeicherten Plan anwenden (mit Backup)")
    reclass_p.set_defaults(func=cmd_reclassify)

    journal_p = sub.add_parser(
        "journal", help="Offenen, unterbrochenen Kurationsschritt anzeigen oder bewusst verwerfen"
    )
    journal_p.add_argument("project")
    journal_p.add_argument("--discard", action="store_true",
                           help="Journal verwerfen (Backup unter .polaris/backups/; die Quellen werden neu kuratiert)")
    journal_p.set_defaults(func=cmd_journal)

    retract_p = sub.add_parser(
        "retract",
        help="Kuratierte Eintraege aus bestimmten Nachrichten zuruecknehmen (ohne --apply nur Anzeige)",
    )
    retract_p.add_argument("project")
    retract_p.add_argument("ids", nargs="*", help="Nachrichten-IDs (ganz oder Praefix ab 8 Zeichen)")
    retract_p.add_argument("--ids-file", help="Datei mit einer ID pro Zeile")
    retract_p.add_argument(
        "--from-dir", action="append", default=[], help="IDs aus allen Event-Dateien dieses Ordners (z. B. Quarantaene); mehrfach erlaubt"
    )
    retract_p.add_argument("--apply", action="store_true", help="aendern (mit Backup und Protokoll)")
    retract_p.add_argument(
        "--rebuild",
        action="store_true",
        help="alle Eintraege der betroffenen Batches entfernen und saubere Nachrichten neu kuratieren lassen",
    )
    retract_p.add_argument(
        "--force", action="store_true", help="auch IDs, die noch im Rohbestand dieses Projekts liegen"
    )
    retract_p.add_argument("--json", action="store_true", help="Maschinenlesbares JSON ausgeben")
    retract_p.set_defaults(func=cmd_retract)

    return parser


def _print_compaction(name: str, result, dry_run: bool) -> None:
    verb = "würde" if dry_run else "hat"
    print(
        f"{name}: {verb} {result.merged_entries} Einträge zu {result.merged_groups} zusammengeführt, "
        f"{result.superseded} als überholt archiviert; {result.chars_before} -> {result.chars_after} Zeichen"
    )
    if result.notes:
        print(f"  Hinweis des Modells: {result.notes}")
    for r in getattr(result, "rejected", []) or []:
        print(f"  verworfen (Zusammenführung verlöre Fakten): {r}")


def cmd_compact(args: argparse.Namespace) -> int:
    from . import compact
    from .llm import call_llm

    project = _resolve_project(args.project)
    cfg = config_mod.load_config(project)
    try:
        result = compact.compact_project(project, cfg, call_llm, dry_run=args.dry_run)
    except compact.CompactionError as exc:
        print(f"Verdichtung verworfen: {exc}")
        return 1
    _print_compaction(project.name, result, args.dry_run)
    if args.dry_run and args.show_plan:
        print(json.dumps(result.plan, ensure_ascii=False, indent=2))
    if not args.dry_run:
        compact.record_run(project, result)
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    from . import systemhealth

    cfg = config_mod.load_config()
    data = systemhealth.collect(
        config_mod.list_known_projects(),
        getattr(cfg, "max_days_without_chatgpt", 3),
        getattr(cfg, "max_days_without_claude", 7),
    )
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
        return 0
    marks = {"ok": "OK  ", "warn": "WARN", "error": "FEHL"}
    if getattr(args, "all", False):
        for c in (data.get("checks") or data["components"]) + data["system"]:
            print(f"{marks[c['level']]}  {c['label']}: {c['detail']}")
        return 0
    for c in data["components"]:
        print(f"{marks[c['level']]}  {c['label']}: {c['detail']}")
    system = data["system"]
    if system:
        worst = max(system, key=lambda c: {"ok": 0, "warn": 1, "error": 2}[c["level"]])["level"]
        shown = [c for c in system if c["level"] != "ok"] or system
        print(f"{marks[worst]}  System: " + " · ".join(f"{c['label']} {c['detail']}" for c in shown))
    return 0


def cmd_llm_check(args: argparse.Namespace) -> int:
    """Prueft die Modell-Einrichtung fuer einen neuen Rechner: Runtime, Modell,
    vorhandene CLIs; mit --call ein winziger echter Aufruf (Codex: ca. 20k Token Vorlauf)."""
    from . import llm

    cfg = config_mod.load_config()
    print(f"Konfiguration: {config_mod.GLOBAL_CONFIG_FILE}" + ("" if config_mod.GLOBAL_CONFIG_FILE.exists()
                                                              else " (fehlt - Standardwerte)"))
    print(f"Runtime:       {cfg.runtime}  Modell: {cfg.model}  Ersatz: {cfg.fallback_model or '-'}")
    if cfg.runtime not in llm.RUNTIMES:
        print(f"FEHLER: unbekannte runtime {cfg.runtime!r} (erlaubt: {', '.join(llm.RUNTIMES)})")
        return 1
    missing = False
    for name, path in llm.runtime_binaries(cfg).items():
        print(f"CLI {name:<10} {path or 'FEHLT'}")
        missing = missing or path is None
    if missing:
        print("Fehlende CLI installieren und anmelden (codex login bzw. claude, dann /login),")
        print("oder in curator.yaml eine vorhandene runtime eintragen (runtime: codex|claude|command).")
        return 1
    if not args.call:
        print("CLIs vorhanden. Anmeldung/Modell pruefen: polaris-curator llm-check --call")
        return 0
    try:
        res = llm.check_call(cfg)
    except (llm.LlmUnavailableError, llm.LlmOutputError) as e:
        print(f"FEHLER: {e}")
        return 1
    print(f"OK: {res.model} antwortet ({res.tokens_used or '?'} Token).")
    return 0


def cmd_index_documents(args: argparse.Namespace) -> int:
    from . import docs_index

    if args.all:
        paths = sorted(config_mod.list_known_projects().values(), key=lambda p: p.name)
    elif args.project:
        paths = [_resolve_project(args.project)]
    else:
        print("Projekt oder --all angeben.")
        return 2
    for path in paths:
        changed = docs_index.write_index(path)
        print(f"{path.name}: {docs_index.INDEX_NAME} {'aktualisiert' if changed else 'unveraendert'}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from . import transfer

    if args.all:
        paths = sorted(config_mod.list_known_projects().values(), key=lambda p: p.name.casefold())
        if not paths:
            print("Keine bekannten Projekte erreichbar (Projekt-Laufwerk eingehaengt?).", file=sys.stderr)
            return 1
    elif args.project:
        paths = [_resolve_project(args.project)]
    else:
        print("Projekt oder --all angeben.", file=sys.stderr)
        return 2
    output = Path(args.output).expanduser() if args.output else None
    result = transfer.export(paths, output=output, with_archive=args.with_archive)
    if args.json:
        print(json.dumps(dataclasses.asdict(result), ensure_ascii=False))
        return 0
    for exp in result.projects:
        print(f"{exp.name}: {', '.join(exp.files) or 'kein kuratiertes Wissen'}")
        for rel, counts in exp.sources.items():
            if counts:
                print(f"  {rel}: " + ", ".join(f"{k} {n}" for k, n in counts.items()))
    print(f"Geschrieben: {result.path} ({result.size_bytes / 1024:.0f} KB)")
    return 0


def cmd_reclassify(args: argparse.Namespace) -> int:
    from . import reclassify
    from .llm import call_llm

    project = _resolve_project(args.project)
    cfg = config_mod.load_config(project)
    try:
        result = reclassify.reclassify_project(project, cfg, call_llm, apply=args.apply)
    except reclassify.ReclassifyError as exc:
        print(f"Nachkuratierung abgebrochen: {exc}")
        return 1
    verb = "geändert" if result.written else "geplant"
    print(f"{project.name}: {result.changed} von {result.candidates} Kandidaten {verb} {result.by_type}")
    for r in result.plan.get("rejected", []):
        print(f"  verworfen {r['id']}: {r['reason']}")
    if result.notes:
        print(f"  Hinweis des Modells: {result.notes}")
    if result.backup:
        print(f"  Backup: {result.backup}")
    elif not args.apply and result.changed:
        print("  Plan: .polaris/reclassify-plan.json - anwenden mit --apply")
    return 0


def cmd_journal(args: argparse.Namespace) -> int:
    from . import journal, layout, storage

    project = _resolve_project(args.project)
    try:
        record = journal.pending(project)
    except journal.JournalConflict as exc:
        record, broken = None, str(exc)
    else:
        broken = None
    if record is None and broken is None:
        print(f"{project.name}: kein offener Schritt.")
        return 0
    if broken:
        print(f"{project.name}: Journal beschaedigt: {broken}")
    else:
        print(f"{project.name}: unterbrochener Schritt ({(record.get('state_update') or {}).get('kind')}), Dateien:")
        for name, spec in (record.get("files") or {}).items():
            current = (project / name).read_text(encoding="utf-8") if (project / name).is_file() else None
            if current == spec["new_text"]:
                state = "schon auf Zielstand"
            elif journal._sha(current) == spec["old_sha"]:
                state = "noch Ausgangsstand (wird beim naechsten Lauf ergaenzt)"
            else:
                state = "ANDERS GEAENDERT - Lauf ist gesperrt"
            print(f"  {name}: {state}")
    if not args.discard:
        print("Normalfall: `polaris-curator run " + project.name + "` vollendet den Schritt. "
              "Nur wenn eine Datei anders geaendert wurde: pruefen, dann --discard.")
        return 1
    backups = layout.polaris_dir(project) / "backups"
    storage.ensure_dir(backups)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    storage.atomic_write_text(backups / f"journal.json.{stamp}-discarded", journal.existing_path(project).read_text(encoding="utf-8", errors="replace"))
    journal.finish(project)
    print(f"Verworfen (Backup: {backups / f'journal.json.{stamp}-discarded'}). Die Quellen des Schritts werden beim naechsten Lauf erneut kuratiert.")
    print("ACHTUNG: Schon geschriebene Teile des Schritts bleiben in den Dateien - danach auf doppelte "
          "Eintraege pruefen (ggf. `polaris-curator compact <Projekt>`).")
    return 0


def cmd_retract(args: argparse.Namespace) -> int:
    from . import retract

    project = _resolve_project(args.project)
    ids = list(args.ids)
    if args.ids_file:
        ids += Path(args.ids_file).expanduser().read_text(encoding="utf-8").split()
    for directory in args.from_dir:
        ids += retract.ids_from_dir(Path(directory).expanduser())
    try:
        result = retract.retract(project, ids, apply=args.apply, rebuild=args.rebuild, force=args.force)
    except retract.RetractError as exc:
        print(f"Ruecknahme abgebrochen: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(dataclasses.asdict(result), ensure_ascii=False))
        return 0
    print(
        f"{project.name}: {result.retracted_ids} Nachricht(en) zurueckzunehmen, "
        f"davon {result.found_in_sources} in Herkunftsangaben des kuratierten Wissens"
    )
    if result.still_in_raw:
        print(
            f"  ausgelassen, liegen noch im Rohbestand von {project.name} ({len(result.still_in_raw)}): "
            + ", ".join(i[:8] for i in result.still_in_raw)
            + " - erst in Quarantaene verschieben oder --force"
        )
    print(f"  nur aus diesen Nachrichten: {len(result.full)}")
    for label in result.full:
        print(f"    - {label}")
    print(f"  gemischt (Batch betroffen): {len(result.partial)}")
    for label in result.partial:
        print(f"    - {label}")
    if result.cited_only and not args.rebuild:
        print(f"  davon laut Herkunftsangabe nur aus diesen Nachrichten (entfernt erst --rebuild): {len(result.cited_only)}")
        for label in result.cited_only:
            print(f"    - {label}")
    if args.rebuild:
        print(f"  bleibt stehen, bitte pruefen (Dokumentquelle oder Quellen nicht mehr im Rohbestand): {len(result.manual)}")
        for label in result.manual:
            print(f"    - {label}")
        if result.not_rebuilt:
            print(f"  entfernt (enthielt Zurueckgenommenes), sauberer Anteil wird NICHT neu aufgebaut: {len(result.not_rebuilt)}")
            for label in result.not_rebuilt:
                print(f"    - {label}  (Text im Protokoll .polaris/retracted/)")
    verb = "entfernt" if result.written else "wuerden entfernt"
    print(f"  {verb}: {len(result.removed)} Eintrag/Eintraege", end="")
    print(f", {result.requeued_ids} saubere Nachricht(en) neu eingestellt" if args.rebuild else "")
    if result.written:
        print(f"  Protokoll: {result.retracted_file}")
        for b in result.backups:
            print(f"  Backup: {b}")
    elif not args.apply and result.removed:
        print("  Anwenden mit --apply" + (" --rebuild" if args.rebuild else ""))
    # Nicht alles automatisch wiederherstellbar: Exit 2, damit es auffaellt.
    return 2 if (result.not_rebuilt or (args.rebuild and result.manual)) else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
