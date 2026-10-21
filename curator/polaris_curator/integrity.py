"""Projekttrennung der Rohdaten pruefen (seit 2026-09-29).

Anlass: Am 2026-09-29 landeten nach einem Chatwechsel Nachrichten eines
Kundenprojekts im Ordner eines anderen und umgekehrt (Extension exportierte veralteten DOM-Inhalt
unter der neuen Adresse). Extension und Bridge sind inzwischen abgesichert;
diese Pruefung ist die letzte Sperre vor dem kuratierten Wissen und
unabhaengig davon, auf welchem Weg eine Vermischung entstanden ist.

Geprueft wird ausschliesslich lesend ueber raw/<quelle>/messages/:
- duplicate_id:    dieselbe Nachrichten-ID liegt in mehreren Projektordnern
                   oder unter mehreren Conversations (Revisionsdateien
                   derselben Conversation zaehlen nicht).
- foreign_project: die project.id der Nachricht ist laut projects.yaml einem
                   anderen Ordner zugeordnet als dem, in dem sie liegt.
- url_mismatch:    Projekt- oder Chat-ID in der gespeicherten ChatGPT-URL
                   weicht von project.id/conversation.id ab.
- index_mismatch:  raw/<quelle>/conversations/<id>.json fuehrt eine ID, deren
                   Nachrichtendatei zu einem anderen Chat gehoert (entstand,
                   wenn die Bridge ein inhaltsgleiches Doppel verwarf, die ID
                   aber trotzdem eintrug). Betrifft keinen Inhalt, blockiert
                   daher nicht (BLOCKING_KINDS).
- bridge_conflict: die Bridge hat eine Nachricht abgewehrt, deren ID schon in
                   einem anderen Projekt/Chat liegt (~/.local/share/polaris/
                   conflicts/). Welche Kopie richtig ist, ist offen - die zuerst
                   archivierte kann die falsche sein. Sperrt BEIDE Projekte, bis
                   die Datei geklaert (geloescht/verschoben) ist.
- unreadable:      Nachrichtendatei nicht lesbar - der Curator wuerde sie still
                   uebergehen (nicht blockierend, aber sichtbar).
- unknown_project: project.id fehlt oder ist keinem Ordner zugeordnet. Sperrt
                   das Projekt nicht, aber genau diese Nachrichten werden
                   nicht kuratiert (chatgpt_source.load_new_messages).

Findet sich etwas, das einen Projektordner betrifft, laesst der Curator
diesen Ordner aus (pipeline.run_project), und der Health Check meldet rot.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import config, layout

BLOCKING_KINDS = frozenset({"duplicate_id", "foreign_project", "url_mismatch", "bridge_conflict"})
# Von der Bridge abgewehrte Vermischungen (bridge/polaris/config.CONFLICTS_DIR).
CONFLICTS_DIR = Path.home() / ".local" / "share" / "polaris" / "conflicts"

_URL_PROJECT_RE = re.compile(r"/g/(g-p-[A-Za-z0-9]+)")
_URL_CONV_RE = re.compile(r"/c/([^/?#]+)")
_CLAUDE_URL_CONV_RE = re.compile(r"/chat/([^/?#]+)")


@dataclass
class Finding:
    kind: str  # "duplicate_id" | "foreign_project" | "url_mismatch" | "index_mismatch"
    message_id: str
    projects: list[str]  # betroffene Ordnernamen (Anzeige)
    detail: str
    files: list[str] = field(default_factory=list)
    # aufgeloeste Ordnerpfade - Zuordnung in findings_for (zwei Wurzeln mit
    # gleichem Ordnernamen sperrten sich sonst gegenseitig; Review 2026-09-29)
    paths: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.kind in BLOCKING_KINDS

    def summary(self) -> str:
        return f"{self.kind} {self.message_id[:8]} ({', '.join(self.projects)}): {self.detail}"


def load_mappings() -> dict[str, Path]:
    """Alle Zuordnungen mit status "ok" als {project_id: Ordner}, anders als
    config.list_known_projects() ohne Zusammenfassen pro Ordner - fuer die
    Pruefung von project.id wird jede ID gebraucht."""
    raw = config._read_yaml(config.PROJECTS_FILE)
    result: dict[str, Path] = {}
    for pid, entry in (raw.get("projects") or {}).items():
        if entry.get("status") != "ok" or not entry.get("local_path"):
            continue
        result[pid] = Path(entry["local_path"])
    return result


def _project_folders(mappings: dict[str, Path], extra: list[Path]) -> list[Path]:
    folders: dict[Path, Path] = {}
    for p in [*mappings.values(), *extra]:
        if p.is_dir():
            folders.setdefault(p.resolve(), p)
    return sorted(folders.values(), key=lambda p: p.name.casefold())


def scan(mappings: dict[str, Path] | None = None, extra_projects: list[Path] | None = None) -> list[Finding]:
    """Prueft alle bekannten Projektordner. extra_projects erlaubt, einen
    Ordner mitzupruefen, der (noch) nicht in projects.yaml steht."""
    if mappings is None:
        mappings = load_mappings()
    owner_of_pid = {pid: p.resolve() for pid, p in mappings.items()}
    findings: list[Finding] = []
    # message_id -> {(ordner, conversation_id): [dateien]}
    locations: dict[str, dict[tuple[str, str], list[str]]] = defaultdict(lambda: defaultdict(list))

    name_of_path: dict[str, str] = {}
    for folder in _project_folders(mappings, extra_projects or []):
        resolved = folder.resolve()
        name_of_path[str(resolved)] = folder.name
        first_new = len(findings)
        for source, directory in layout.chat_messages_dirs(folder):
            if not directory.is_dir():
                continue
            conv_of_message: dict[str, str] = {}
            for path in sorted(directory.glob("*.json")):
                rel = f"{folder.name}/{path.relative_to(folder)}"
                try:
                    event = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
                    findings.append(Finding("unreadable", path.stem, [folder.name], str(exc)[:120], [rel]))
                    continue
                msg_id = (event.get("message") or {}).get("id") if isinstance(event, dict) else None
                conv = (event.get("conversation") or {}) if isinstance(event, dict) else {}
                conv_id = conv.get("id")
                pid = (event.get("project") or {}).get("id") if isinstance(event, dict) else None
                if not msg_id or not conv_id:
                    findings.append(Finding("unreadable", path.stem, [folder.name], "message.id oder conversation.id fehlt", [rel]))
                    continue
                if not pid or pid not in owner_of_pid:
                    findings.append(
                        Finding("unknown_project", msg_id, [folder.name], f"project.id {pid!r} keinem Ordner zugeordnet", [rel])
                    )
                locations[msg_id][(str(resolved), conv_id)].append(rel)
                if ".rev" not in path.name:
                    conv_of_message[msg_id] = conv_id

                owner = owner_of_pid.get(pid)
                if owner is not None and owner != resolved:
                    findings.append(
                        Finding(
                            "foreign_project",
                            msg_id,
                            [folder.name],
                            f"project.id {pid} gehoert zu {mappings[pid].name}",
                            [rel],
                        )
                    )

                url = conv.get("url") or ""
                if source == "claude" and url:
                    m_conv = _CLAUDE_URL_CONV_RE.search(url)
                    if m_conv and m_conv.group(1) != conv_id:
                        findings.append(
                            Finding("url_mismatch", msg_id, [folder.name],
                                    f"URL-Chat {m_conv.group(1)} != conversation.id {conv_id}", [rel])
                        )
                if source == "chatgpt" and url:
                    m_proj = _URL_PROJECT_RE.search(url)
                    m_conv = _URL_CONV_RE.search(url)
                    problems = []
                    if m_proj and pid and m_proj.group(1) != pid:
                        problems.append(f"URL-Projekt {m_proj.group(1)} != project.id {pid}")
                    if m_conv and m_conv.group(1) != conv_id:
                        problems.append(f"URL-Chat {m_conv.group(1)} != conversation.id {conv_id}")
                    if problems:
                        findings.append(
                            Finding("url_mismatch", msg_id, [folder.name], "; ".join(problems), [rel])
                        )

            conv_dir = directory.parent / "conversations"
            for path in sorted(conv_dir.glob("*.json")) if conv_dir.is_dir() else []:
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                cid = data.get("conversation_id")
                foreign = [
                    m for m in data.get("known_message_ids") or []
                    if m in conv_of_message and conv_of_message[m] != cid
                ]
                if foreign:
                    findings.append(
                        Finding(
                            "index_mismatch",
                            foreign[0],
                            [folder.name],
                            f"{len(foreign)} ID(s) in {path.name} gehoeren zu anderen Chats",
                            [f"{folder.name}/{path.relative_to(folder)}"],
                        )
                    )
        for f in findings[first_new:]:
            f.paths = [str(resolved)]

    findings.extend(_bridge_conflicts())

    for msg_id, places in locations.items():
        if len(places) < 2:
            continue
        findings.append(
            Finding(
                "duplicate_id",
                msg_id,
                sorted({name_of_path[p] for p, _conv in places}),
                "liegt an mehreren Orten: "
                + ", ".join(f"{name_of_path[p]}/{conv}" for p, conv in sorted(places)),
                sorted(f for files in places.values() for f in files),
                sorted({p for p, _conv in places}),
            )
        )
    return findings


def _bridge_conflicts() -> list[Finding]:
    result: list[Finding] = []
    if not CONFLICTS_DIR.is_dir():
        return result
    for path in sorted(CONFLICTS_DIR.glob("*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            info = record.get("_polaris_conflict") or {}
            msg_id = (record.get("message") or {}).get("id") or path.stem
        except (json.JSONDecodeError, OSError, UnicodeDecodeError, AttributeError):
            info, msg_id = {}, path.stem
        folders = sorted({f for f in (info.get("existing_folder"), info.get("rejected_folder")) if f})
        result.append(
            Finding(
                "bridge_conflict",
                msg_id,
                folders or ["?"],
                f"von der Bridge abgewehrt: liegt in {info.get('existing_folder')}/"
                f"{info.get('existing_conversation_id')}, kam fuer {info.get('rejected_folder')}/"
                f"{info.get('rejected_conversation_id')} - klaeren, dann {path} entfernen",
                [str(path)],
            )
        )
    return result


def findings_for(project: Path, findings: list[Finding]) -> list[Finding]:
    own = str(project.resolve())
    # Mit Pfad eindeutig; Bridge-Konflikte kennen nur Ordnernamen.
    return [f for f in findings if (own in f.paths if f.paths else project.name in f.projects)]


def check_project(project: Path, mappings: dict[str, Path] | None = None) -> list[Finding]:
    """Befunde, die diesen Projektordner betreffen (Vergleich mit allen
    anderen bekannten Ordnern)."""
    return findings_for(project, scan(mappings, extra_projects=[project]))
