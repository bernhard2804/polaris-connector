"""Baut <Project>/dokumente-index.md: eine Uebersicht aller archivierten
Quelldokumente und Mail-Anhaenge mit ihren Pfaden im Projektordner.

Anlass (2026-09-25): ChatGPT fand abgelegte Mail-Anhaenge im Google-Drive-
Projektordner erst nach ausdruecklichem Hinweis - nichts im kuratierten
Wissen verwies auf sie. Der Index wird bei jedem Aufruf vollstaendig aus den
Archivordnern neu erzeugt (Dateisystem = einzige Quelle der Wahrheit), deckt
damit auch vor seiner Einfuehrung archivierte Dateien ab und wird nur
geschrieben, wenn sich sein Inhalt aendert (vermeidet unnoetige Uploads).
"""

from __future__ import annotations

import email
import email.header
import os
from pathlib import Path

from . import layout, state as state_mod

INDEX_NAME = "dokumente-index.md"
_HEADER_READ_BYTES = 64 * 1024
_HANDLING_LABEL = {
    "text": "ausgewertet",
    "image": "als Bild ausgewertet",
    "stored": "nur abgelegt (nicht ausgewertet)",
}


def index_path(project: Path) -> Path:
    return project / INDEX_NAME


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        text = str(email.header.make_header(email.header.decode_header(value)))
    except (LookupError, ValueError, UnicodeDecodeError):
        text = value
    return " ".join(text.split()).replace("|", "/")


def _mail_headers(path: Path) -> dict[str, str]:
    # Nur den Kopf lesen - die Mails liegen auf dem rclone-Mount und koennen
    # mehrere MB gross sein.
    try:
        with path.open("rb") as f:
            head = f.read(_HEADER_READ_BYTES)
    except OSError:
        return {}
    msg = email.message_from_bytes(head.split(b"\r\n\r\n", 1)[0].split(b"\n\n", 1)[0] + b"\n\n")
    return {key: _decode(msg.get(key)) for key in ("Subject", "From", "Date")}


def _handling_by_file(st: state_mod.CuratorState, mail_name: str) -> dict[str, str]:
    sig = st.file_signature(f"mail:{mail_name}") or {}
    out = {}
    for a in sig.get("attachments", []) or []:
        h = a.get("handling", "")
        if h == "duplicate":
            h = f"identisch mit `{a.get('note', '')}`, dort ausgewertet"
        out[a.get("file", "")] = h
    return out


def _rel(project: Path, path: Path) -> str:
    return str(path.relative_to(project))


def build_index(project: Path) -> str:
    st = state_mod.load_state(project)
    lines = [
        "# Dokumente und Mail-Anhänge",
        "",
        "Automatisch erzeugt von Polaris Curator – nicht von Hand bearbeiten.",
        "Übersicht aller verarbeiteten Quelldokumente mit ihrem Pfad in diesem",
        "Projektordner. Die Inhalte sind in `knowledge.md`, `decisions.md` und",
        "`open-loops.md` eingeflossen; für Details die Originaldatei öffnen.",
        "",
    ]

    mail_dir = layout.inbox_processed_dir(project, "mail")
    att_root = layout.inbox_attachments_dir(project, "mail")
    mails = sorted(p for p in mail_dir.glob("*.eml") if p.is_file()) if mail_dir.is_dir() else []
    lines += ["## E-Mails", ""]
    if not mails:
        lines += ["_Keine._", ""]
    for mail in mails:
        h = _mail_headers(mail)
        title = h.get("Subject") or mail.stem
        meta = ", ".join(x for x in (h.get("From"), h.get("Date")) if x)
        lines.append(f"### {title}")
        if meta:
            lines.append(meta)
        lines.append(f"- Mail: `{_rel(project, mail)}`")
        att_dir = att_root / mail.stem
        handling = _handling_by_file(st, mail.name)
        files = sorted(p for p in att_dir.iterdir() if p.is_file()) if att_dir.is_dir() else []
        for att in files:
            h = handling.get(att.name, "")
            label = _HANDLING_LABEL.get(h, h if h.startswith("identisch mit") else "abgelegt")
            lines.append(f"- Anhang: `{_rel(project, att)}` – {label}")
        lines.append("")

    lines += ["## Dokumente und Notizen", ""]
    docs = []
    for kind in ("documents", "notes"):
        d = layout.inbox_processed_dir(project, kind)
        if d.is_dir():
            docs += sorted(p for p in d.iterdir() if p.is_file())
    if not docs:
        lines += ["_Keine._", ""]
    for doc in docs:
        lines.append(f"- `{_rel(project, doc)}`")
    return "\n".join(lines).rstrip() + "\n"


def write_index(project: Path) -> bool:
    """Schreibt den Index, falls sich der Inhalt geaendert hat. Liefert True,
    wenn geschrieben wurde."""
    content = build_index(project)
    target = index_path(project)
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return False
    except OSError:
        pass
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)
    return True
