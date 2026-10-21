"""Lesbares Archiv aus dem offiziellen ChatGPT-Datenexport (seit 2026-10-02).

Anders als `import-export` landet nichts in raw/chatgpt/ - der Curator wertet
das Archiv also NICHT aus (keine Modellaufrufe, keine Doppelgewichtung). Ziel
ist eine durchsuchbare Ablage aelterer Chats im Projektordner:

    <Projekt>/ChatGPT-Archiv/<JJJJ-MM-TT> <Titel>.md
    <Projekt>/ChatGPT-Archiv/Dateien/<JJJJ-MM-TT> <Titel>/<Originalname>

Regeln:
- Turn-Bildung wie beim Import (aktueller Strang, sichtbarer Text), siehe
  export_import.events_from_conversation.
- Abzweigungen ("Teilgespraech") uebernehmen den Verlauf ihres Ausgangs-Chats:
  der fuehrende, textgleich schon abgelegte Teil wird nicht wiederholt,
  sondern auf die Datei verwiesen, in der er steht.
- Dateien aus dem Export werden unter ihrem Originalnamen kopiert, grosse
  Archive (.pst/.mbox/.zip ...) nur vermerkt. Identische Dateien je Projekt
  nur einmal. Hochgeladene Dateien fehlen im Export oft; liegt eine Datei
  gleichen Namens schon im Projektordner, wird auf sie verlinkt.
- Wiederholbar: Chats, deren ID schon in einer Archivdatei steht, werden
  uebersprungen; bestehende Dateien werden nie ueberschrieben.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import mapping, storage
from .export_import import events_from_conversation, iter_conversations

ARCHIVE_DIR = "ChatGPT-Archiv"
FILES_DIR = "Dateien"
SKIP_EXTENSIONS = {".pst", ".ost", ".mbox", ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz"}
MAX_TITLE_CHARS = 50
_ID_LINE = re.compile(r"^chatgpt_conversation_id:\s*(\S+)", re.M)
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f#^\[\]]+')
_POINTER = re.compile(r"^(?:file-service|sediment)://(.+)$")
_MAGIC = [(b"%PDF", ".pdf"), (b"\x89PNG", ".png"), (b"\xff\xd8\xff", ".jpg"), (b"GIF8", ".gif"),
          (b"RIFF", ".webp"), (b"PK\x03\x04", ".zip")]


@dataclass
class ArchiveReport:
    chats: dict[str, int] = field(default_factory=dict)
    files: dict[str, int] = field(default_factory=dict)
    skipped_existing: int = 0
    skipped_filter: int = 0
    deduplicated_turns: int = 0
    noted_only: int = 0
    missing_files: int = 0
    linked_local: int = 0
    written: list[str] = field(default_factory=list)


def short_title(title: str) -> str:
    title = re.sub(r"^Teilgespräch\s*·\s*", "", title or "").strip()
    title = _UNSAFE.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(" .") or "Ohne Titel"
    if len(title) > MAX_TITLE_CHARS:
        cut = title[:MAX_TITLE_CHARS].rsplit(" ", 1)[0]
        title = (cut if len(cut) >= 20 else title[:MAX_TITLE_CHARS]).rstrip(" .,-")
    return title


def _safe_filename(name: str) -> str:
    name = _UNSAFE.sub("_", name).strip(" .")
    return name[:120] or "datei"


def _sniff_extension(path: Path) -> str:
    with path.open("rb") as fh:
        head = fh.read(8)
    return next((ext for magic, ext in _MAGIC if head.startswith(magic)), "")


def _file_refs(message: dict) -> list[tuple[str, str | None]]:
    """(Datei-ID, Originalname) einer Nachricht: Anhaenge und Bild-Zeiger."""
    refs = []
    for a in (message.get("metadata") or {}).get("attachments") or []:
        if isinstance(a, dict) and a.get("id"):
            refs.append((a["id"], a.get("name")))
    for part in (message.get("content") or {}).get("parts") or []:
        if isinstance(part, dict):
            m = _POINTER.match(str(part.get("asset_pointer") or ""))
            if m:
                refs.append((m.group(1), None))
    return refs


def _existing_ids(archive: Path) -> dict[str, str]:
    ids = {}
    if archive.is_dir():
        for md in archive.glob("*.md"):
            m = _ID_LINE.search(md.read_text(encoding="utf-8", errors="replace")[:2000])
            if m:
                ids[m.group(1)] = md.stem
    return ids


def _fmt_time(iso: str | None) -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")


def _turn_key(event: dict) -> str:
    msg = event["message"]
    return hashlib.sha256(f"{msg['role']}\n{msg['content']}".encode()).hexdigest()


class _Project:
    def __init__(self, root: Path):
        self.root = root
        self.archive = root / ARCHIVE_DIR
        self.known = _existing_ids(self.archive)
        self.turns: dict[str, str] = {}      # Turn-Hash -> Archivdatei (stem)
        self.file_hashes: dict[str, str] = {}  # Inhalts-Hash -> relativer Pfad
        self.used_stems = {p.stem for p in self.archive.glob("*.md")} if self.archive.is_dir() else set()
        self._local: dict[str, Path] | None = None

    def local_file(self, name: str) -> str | None:
        """Pfad (relativ zum Archivordner) einer gleichnamigen Datei im Projekt."""
        if self._local is None:
            self._local = {}
            for p in sorted(self.root.rglob("*"), key=lambda p: (len(p.parts), str(p))):
                rel = p.relative_to(self.root)
                if rel.parts[0] in (ARCHIVE_DIR, "raw", ".polaris", ".obsidian") or not p.is_file():
                    continue
                self._local.setdefault(p.name, rel)
        rel = self._local.get(name)
        return f"../{rel.as_posix()}" if rel else None

    def stem_for(self, date: str, title: str) -> str:
        base = f"{date} {short_title(title)}"
        if (title or "").startswith("Teilgespräch"):
            base += " (Abzweigung)"
        stem, n = base, 2
        while stem in self.used_stems:
            stem, n = f"{base} ({n})", n + 1
        self.used_stems.add(stem)
        return stem


def archive_export(source: Path, projects: set[str] | None = None, since: str | None = None,
                   min_turns: int = 1, dry_run: bool = False, export_label: str | None = None) -> ArchiveReport:
    source = Path(source)
    if not source.is_dir():
        raise NotADirectoryError(f"{source}: bitte den entpackten Export-Ordner angeben")
    names = {}
    names_file = source / "conversation_asset_file_names.json"
    if names_file.is_file():
        names = json.loads(names_file.read_text(encoding="utf-8"))
    label = export_label or source.name
    store = mapping.load_store()
    report = ArchiveReport()
    state: dict[Path, _Project] = {}

    # Aelteste zuerst, damit Ausgangs-Chats vor ihren Abzweigungen abgelegt werden.
    convs = sorted(iter_conversations(source), key=lambda c: float(c.get("create_time") or 0))
    for conv in convs:
        events = events_from_conversation(conv, "")
        if not events:
            continue
        project_id = events[0]["project"]["id"]
        entry = store.get(project_id)
        if not entry or entry.status != "ok" or not entry.local_path:
            continue
        root = Path(entry.local_path)
        if projects and root.name not in projects:
            continue
        times = [e["message"]["source_timestamp"] for e in events if e["message"]["source_timestamp"]]
        if not times:
            continue
        first, last = min(times), max(times)
        if (since and last[:10] < since) or len(events) < min_turns:
            report.skipped_filter += 1
            continue
        proj = state.setdefault(root, _Project(root))
        conv_id = events[0]["conversation"]["id"]
        if conv_id in proj.known:
            report.skipped_existing += 1
            for e in events:
                proj.turns.setdefault(_turn_key(e), proj.known[conv_id])
            continue

        stem = proj.stem_for(first[:10], conv.get("title") or "")
        # Fuehrenden, schon abgelegten Teil (Abzweigung) nicht wiederholen.
        lead = 0
        while lead < len(events) and _turn_key(events[lead]) in proj.turns:
            lead += 1
        parent_stem = proj.turns[_turn_key(events[0])] if lead else None
        report.deduplicated_turns += lead
        if lead == len(events):  # nichts Neues gegenueber dem Ausgangs-Chat
            proj.used_stems.discard(stem)
            proj.known[conv_id] = parent_stem
            continue

        by_id = {n["message"]["id"]: n["message"] for n in (conv.get("mapping") or {}).values()
                 if n.get("message") and n["message"].get("id")}
        title = conv.get("title") or "Ohne Titel"
        lines = [
            "---",
            f"quelle: ChatGPT-Datenexport ({label})",
            f"chatgpt_conversation_id: {conv_id}",
            f"chatgpt_projekt: {project_id}",
            f"zeitraum: {first[:10]} bis {last[:10]}",
            f"nachrichten: {len(events)}",
            f"url: {events[0]['conversation']['url']}",
            "kuratiert: nein",
            "---",
            "",
            f"# {title}",
            "",
        ]
        if lead:
            lines += [f"> Abzweigung: Die ersten {lead} Nachrichten stehen in [[{parent_stem}]].", ""]
        copied = 0
        for event in events[lead:]:
            msg = event["message"]
            who = "Ich" if msg["role"] == "user" else "ChatGPT"
            lines += [f"## {who} · {_fmt_time(msg['source_timestamp'])}", "", msg["content"].strip(), ""]
            links = []
            for member_id in msg.get("member_ids") or [msg["id"]]:
                for file_id, orig in _file_refs(by_id.get(member_id) or {}):
                    src = next(iter(sorted(source.glob(f"{file_id}*.dat"))), None)
                    name = orig or names.get(f"{file_id}.dat") or file_id
                    if src is None:
                        local = proj.local_file(name) if orig else None
                        if local:
                            report.linked_local += 1
                            links.append(f"[{name}](<{local}>) (im Projektordner)")
                        else:
                            report.missing_files += 1
                            links.append(f"{name} (nicht im Export)")
                        continue
                    if not Path(name).suffix:
                        name += _sniff_extension(src)
                    if Path(name).suffix.lower() in SKIP_EXTENSIONS:
                        report.noted_only += 1
                        links.append(f"{name} (nicht abgelegt)")
                        continue
                    digest = hashlib.sha256(src.read_bytes()).hexdigest()
                    rel = proj.file_hashes.get(digest)
                    if rel is None:
                        rel = f"{FILES_DIR}/{stem}/{_safe_filename(name)}"
                        target = proj.archive / rel
                        n = 2
                        while target.exists():
                            p = Path(_safe_filename(name))
                            rel = f"{FILES_DIR}/{stem}/{p.stem} ({n}){p.suffix}"
                            target, n = proj.archive / rel, n + 1
                        if not dry_run:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(src, target)
                        proj.file_hashes[digest] = rel
                        copied += 1
                    links.append(f"[{name}](<{rel}>)")
            if links:
                lines += ["Dateien: " + " · ".join(dict.fromkeys(links)), ""]
        for e in events:
            proj.turns.setdefault(_turn_key(e), stem)
        proj.known[conv_id] = stem
        if not dry_run:
            proj.archive.mkdir(parents=True, exist_ok=True)
            storage.atomic_write_text(proj.archive / f"{stem}.md", "\n".join(lines).rstrip() + "\n")
        report.written.append(f"{root.name}/{ARCHIVE_DIR}/{stem}.md")
        report.chats[root.name] = report.chats.get(root.name, 0) + 1
        report.files[root.name] = report.files.get(root.name, 0) + copied
    return report
