"""Erkennt neue oder geaenderte Dateien in inbox/{mail,documents,notes} und
extrahiert ihren Textinhalt. Unterstuetzt .eml, .pdf, .docx, .xlsx/.xlsm,
.pptx, .csv, .md, .txt.

Bei .eml werden auch die Anhaenge ausgewertet: Text aus unterstuetzten
Formaten wird an den Mailtext angehaengt, grosse Bilder (Screenshots, Scans)
gehen als Bild an das Modell, kleine Bilder (Signaturen, Logos) werden
ignoriert. Nach erfolgreicher Kuratierung werden die Anhaenge als eigene
Dateien neben dem Mail-Archiv abgelegt (siehe archive_processed_document).

Aenderungserkennung ueber Pfad + Groesse + mtime + Hash (siehe
_file_signature) - eine unveraenderte Datei wird nie erneut analysiert.
"""

from __future__ import annotations

import email
import email.header
import hashlib
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Any

from . import layout, office_extract, state as state_mod, storage

logger = logging.getLogger("polaris_curator")

# Formate, deren Text extrahiert werden kann - fuer Inbox-Dateien und
# Mail-Anhaenge gleichermassen (plus .eml fuer Inbox-Dateien).
TEXT_SUFFIXES = {".pdf", ".docx", ".xlsx", ".xlsm", ".pptx", ".csv", ".md", ".txt"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".eml"}
# Bildformate, die als Bild an das Modell gehen koennen (codex exec --image).
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

# Defaults, per Config ueberschreibbar (siehe config.py, Abschnitt attachments).
DEFAULT_MIN_IMAGE_BYTES = 300_000
DEFAULT_MAX_IMAGES_PER_DOCUMENT = 5
DEFAULT_MAX_CHARS_PER_ATTACHMENT = 20_000
DEFAULT_MAX_CHARS_PER_DOCUMENT = 50_000


@dataclass
class Attachment:
    filename: str
    content_type: str
    data: bytes
    # "text": Text extrahiert, "image": geht als Bild ans Modell,
    # "stored": nur abgelegt (Format nicht auswertbar oder Bildlimit erreicht)
    # "duplicate": identisch mit einem eigenen Dokument (note = dessen Pfad),
    # dort ausgewertet, hier nur abgelegt
    handling: str
    note: str = ""


@dataclass
class DocumentSource:
    kind: str  # "mail" | "documents" | "notes"
    path: Path
    text: str
    signature: dict[str, Any]
    # Nur bei Mails: abzulegende Anhaenge (kleine Bilder sind hier bereits
    # herausgefiltert).
    attachments: list[Attachment] = field(default_factory=list)
    # Hinweis, falls der Text gekuerzt wurde. Der Lauf meldet ihn erst, wenn
    # das Dokument wirklich kuratiert wird - nicht schon beim Einlesen, sonst
    # erschienen budgetbedingt wartende Dokumente als "gekuerzt kuratiert"
    # (2026-10-07).
    truncation_note: str | None = None
    # Teil eines grossen Dokuments (seit 2026-10-07): part von parts; 1/1 = ganz.
    part: int = 1
    parts: int = 1

    @property
    def is_last_part(self) -> bool:
        return self.part >= self.parts

    @property
    def images(self) -> list[tuple[str, bytes]]:
        return [(a.filename, a.data) for a in self.attachments if a.handling == "image"]

    @property
    def state_key(self) -> str:
        return f"{self.kind}:{self.path.name}"


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "hash": _hash_file(path),
    }


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?(</\1>)", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _decode_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _extract_pdf_bytes(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
        tmp.write(data)
        tmp.flush()
        result = subprocess.run(
            ["pdftotext", "-layout", tmp.name, "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    if result.returncode != 0:
        raise RuntimeError(f"pdftotext fehlgeschlagen: {result.stderr.strip()}")
    return result.stdout


def text_from_bytes(filename: str, data: bytes) -> str:
    """Extrahiert Text aus einer Datei anhand ihrer Endung (ohne .eml)."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_bytes(data)
    if suffix == ".docx":
        return office_extract.extract_docx(data)
    if suffix in (".xlsx", ".xlsm"):
        return office_extract.extract_xlsx(data)
    if suffix == ".pptx":
        return office_extract.extract_pptx(data)
    if suffix in (".md", ".txt", ".csv"):
        return _decode_text(data)
    raise ValueError(f"Nicht unterstuetzter Dateityp: {suffix}")


def _truncate(text: str, limit: int, what: str) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"\n[... {what} nach {limit} Zeichen abgeschnitten]"


def _attachment_filename(part: Message, index: int) -> str:
    raw = part.get_filename() or ""
    try:
        raw = str(email.header.make_header(email.header.decode_header(raw)))
    except (LookupError, ValueError, UnicodeDecodeError):
        pass
    # Nur den Dateinamen behalten - nie Pfadbestandteile aus der Mail uebernehmen.
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", raw).strip(" .")
    if name:
        return name
    ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "application/pdf": ".pdf"}
    return f"anhang-{index}{ext.get(part.get_content_type(), '.bin')}"


def _is_attachment_part(part: Message) -> bool:
    if part.is_multipart() or part.get_content_type() == "message/rfc822":
        return False  # Container - walk() steigt selbst in eingebettete Mails ab
    if part.get_content_disposition() == "attachment" or part.get_filename():
        return True
    # Inline-Bilder ohne Dateinamen (typisch fuer Signaturen)
    return part.get_content_maintype() == "image"


def _classify_attachments(
    parts: list[tuple[str, str, bytes]], limits: dict[str, int],
    known: dict[str, str] | None = None,
) -> tuple[list[Attachment], list[str]]:
    """Entscheidet pro Anhang: Text extrahieren, als Bild ans Modell, nur
    ablegen oder ignorieren. Liefert (abzulegende Anhaenge, Textbloecke).
    known: SHA-256 -> Ort von Dateien, die als eigenes Dokument in der Inbox
    liegen oder schon kuratiert sind. Solche Anhaenge werden nur abgelegt und
    verwiesen, sonst landete ihr Inhalt doppelt im Wissen (2026-10-07: eine
    Wochenstatistik-Mail trug genau die sechs Dateien, die auch einzeln in
    inbox/documents lagen)."""
    attachments: list[Attachment] = []
    text_blocks: list[str] = []
    images_used = 0
    for filename, content_type, data in parts:
        same = (known or {}).get(hashlib.sha256(data).hexdigest())
        if same:
            attachments.append(Attachment(filename, content_type, data, "duplicate", same))
            text_blocks.append(f"--- Anhang: {filename} (identisch mit {same}, dort separat kuratiert) ---")
            continue
        suffix = Path(filename).suffix.lower()
        is_image = content_type.startswith("image/") or suffix in IMAGE_SUFFIXES
        if is_image:
            if len(data) < limits["min_image_bytes"]:
                continue  # Signatur, Logo, Icon - weder auswerten noch ablegen
            if suffix in IMAGE_SUFFIXES and images_used < limits["max_images_per_document"]:
                images_used += 1
                attachments.append(Attachment(filename, content_type, data, "image"))
                text_blocks.append(f"--- Anhang (Bild, dem Modell beigefuegt): {filename} ---")
            else:
                attachments.append(
                    Attachment(filename, content_type, data, "stored", "Bild nicht ausgewertet")
                )
                text_blocks.append(f"--- Anhang (Bild, nicht ausgewertet): {filename} ---")
            continue

        if suffix in TEXT_SUFFIXES:
            try:
                text = text_from_bytes(filename, data).strip()
            except Exception as exc:  # noqa: BLE001 - ein kaputter Anhang darf die Mail nicht blockieren
                logger.warning("Anhang %s nicht lesbar: %s", filename, exc)
                attachments.append(Attachment(filename, content_type, data, "stored", "nicht lesbar"))
                text_blocks.append(f"--- Anhang (nicht lesbar): {filename} ---")
                continue
            attachments.append(Attachment(filename, content_type, data, "text"))
            text = _truncate(text, limits["max_chars_per_attachment"], "Anhang")
            text_blocks.append(f"--- Anhang: {filename} ---\n{text or '[kein Text enthalten]'}")
        else:
            attachments.append(
                Attachment(filename, content_type, data, "stored", "Format nicht auswertbar")
            )
            text_blocks.append(f"--- Anhang (Format nicht auswertbar, nur abgelegt): {filename} ---")
    return attachments, text_blocks


def _extract_eml(path: Path, limits: dict[str, int],
                 known: dict[str, str] | None = None) -> tuple[str, list[Attachment]]:
    with path.open("rb") as f:
        msg: Message = email.message_from_binary_file(f)

    header_lines = [
        f"From: {msg.get('From', '')}",
        f"To: {msg.get('To', '')}",
        f"Date: {msg.get('Date', '')}",
        f"Subject: {msg.get('Subject', '')}",
    ]

    plain_parts: list[str] = []
    html_parts: list[str] = []
    raw_attachments: list[tuple[str, str, bytes]] = []

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if _is_attachment_part(part):
                payload = part.get_payload(decode=True)
                if payload:
                    raw_attachments.append(
                        (_attachment_filename(part, len(raw_attachments) + 1), content_type, payload)
                    )
                continue
            if content_type not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace")
            except (LookupError, ValueError):
                continue
            if content_type == "text/plain":
                plain_parts.append(text)
            else:
                html_parts.append(text)
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace") if payload else ""
        except (LookupError, ValueError):
            text = ""
        if msg.get_content_type() == "text/html":
            html_parts.append(text)
        else:
            plain_parts.append(text)

    body = "\n\n".join(plain_parts).strip() or _strip_html("\n\n".join(html_parts))
    attachments, attachment_blocks = _classify_attachments(raw_attachments, limits, known)
    text = "\n".join(header_lines) + "\n\n" + body
    if attachment_blocks:
        text += "\n\n=== ANHAENGE ===\n" + "\n\n".join(attachment_blocks)
    return text, attachments


def _limits(cfg) -> dict[str, int]:
    return {
        "min_image_bytes": getattr(cfg, "min_image_bytes", DEFAULT_MIN_IMAGE_BYTES),
        "max_images_per_document": getattr(
            cfg, "max_images_per_document", DEFAULT_MAX_IMAGES_PER_DOCUMENT
        ),
        "max_chars_per_attachment": getattr(
            cfg, "max_chars_per_attachment", DEFAULT_MAX_CHARS_PER_ATTACHMENT
        ),
        # Ein einzelnes Dokument darf nie groesser sein als das Budget pro
        # Einheit - sonst bliebe es dauerhaft vor der Budgetgrenze haengen
        # und wuerde alle nachfolgenden Einheiten blockieren.
        "max_chars_per_document": min(
            getattr(cfg, "max_input_chars_per_batch", DEFAULT_MAX_CHARS_PER_DOCUMENT),
            getattr(cfg, "max_total_input_chars_per_run", DEFAULT_MAX_CHARS_PER_DOCUMENT),
        ),
    }


def _known_document_hashes(project, state: state_mod.CuratorState) -> dict[str, str]:
    """SHA-256 -> Ort aller eigenstaendigen Dokumente/Notizen: schon kuratierte
    (Zustand) und in der Inbox wartende. Mails zaehlen nicht - deren Anhaenge
    sollen ja gerade gegen eigene Dokumente abgeglichen werden."""
    known: dict[str, str] = {}
    for key, sig in (state.processed_files or {}).items():
        kind, _, name = key.partition(":")
        if kind in ("documents", "notes") and isinstance(sig, dict) and sig.get("hash"):
            known[sig["hash"]] = f"inbox/{kind}/{name}"
    for kind in ("documents", "notes"):
        directory = layout.inbox_dir(project, kind)
        if directory.is_dir():
            for f in sorted(directory.iterdir()):
                if f.is_file() and f.suffix.lower() in SUPPORTED_SUFFIXES:
                    try:
                        known.setdefault(_hash_file(f), f"inbox/{kind}/{f.name}")
                    except OSError:
                        continue
    return known


def load_new_documents(
    project, state: state_mod.CuratorState, cfg=None, skipped: list[str] | None = None
) -> list[DocumentSource]:
    """skipped sammelt Hinweise zu Dateien, die liegen bleiben (nicht
    unterstuetzt, Extraktion gescheitert, kein Text). Frueher standen sie nur
    im Log - ein gescanntes PDF blieb so still fuer immer in der Inbox
    (Review 2026-09-29)."""
    note = skipped.append if skipped is not None else (lambda _msg: None)
    sources: list[DocumentSource] = []
    limits = _limits(cfg)
    known = _known_document_hashes(project, state)

    for kind in ("mail", "documents", "notes"):
        directory = layout.inbox_dir(project, kind)
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if not path.is_file():
                continue
            if path.suffix.lower() not in SUPPORTED_SUFFIXES:
                if not path.name.startswith("."):
                    logger.warning(
                        "Nicht unterstuetzter Dateityp in inbox/%s, wird nicht kuratiert: %s",
                        kind,
                        path.name,
                    )
                    note(f"inbox/{kind}/{path.name}: Dateityp nicht unterstuetzt - wird nicht kuratiert")
                continue

            state_key = f"{kind}:{path.name}"
            signature = _file_signature(path)
            existing = state.file_signature(state_key)
            if existing is not None and existing.get("hash") == signature["hash"]:
                note(f"inbox/{kind}/{path.name}: identisch bereits verarbeitet - kann entfernt werden")
                continue  # unveraendert - nicht erneut analysieren

            attachments: list[Attachment] = []
            try:
                if path.suffix.lower() == ".eml":
                    text, attachments = _extract_eml(path, limits, known)
                else:
                    text = text_from_bytes(path.name, path.read_bytes())
            except Exception as exc:  # noqa: BLE001 - defensiv, eine kaputte Datei darf den Lauf nicht abbrechen
                logger.warning("Extraktion fehlgeschlagen fuer %s: %s", path.name, exc)
                note(f"inbox/{kind}/{path.name}: Text nicht lesbar ({type(exc).__name__}) - bleibt liegen")
                continue

            if not text.strip() and not attachments:
                note(f"inbox/{kind}/{path.name}: kein Text gefunden (Scan ohne Texterkennung?) - bleibt liegen")
                continue

            limit = limits["max_chars_per_document"]
            max_parts = max(1, int(getattr(cfg, "max_parts_per_document", 5) or 1))
            factor = max(1.0, float(getattr(cfg, "document_oversize_factor", 2.0) or 1.0))
            single_cap = int(limit * factor) if limit > 0 else 0
            if limit > 0 and len(text) > single_cap and max_parts > 1:
                sources.extend(_document_parts(kind, path, text, signature, attachments, limit,
                                               max_parts, state, limits))
                continue
            if limit > 0 and limit < len(text) <= single_cap:
                logger.info("%s: %d Zeichen > Limit %d - einmalig in einem Aufruf (bis %d erlaubt)",
                            path.name, len(text), limit, single_cap)
            final_text = _truncate(text, single_cap, "Dokument")
            truncation_note = None
            if "Zeichen abgeschnitten]" in final_text:
                # Sichtbar machen statt still kuerzen (Architektur-Review
                # 2026-09-29): der Rest landet sonst nie im Wissen.
                truncation_note = (
                    f"inbox/{kind}/{path.name}: gekuerzt kuratiert ({len(text)} Zeichen, Limit "
                    f"{limits['max_chars_per_document']} je Dokument / {limits['max_chars_per_attachment']} je "
                    "Anhang) - der Rest fehlt im Wissen; Datei teilen oder Limit erhoehen")
            sources.append(
                DocumentSource(
                    kind=kind,
                    path=path,
                    text=final_text,
                    signature=signature,
                    attachments=attachments,
                    truncation_note=truncation_note,
                )
            )

    return sources


_PART_HEADER = 400  # Platz fuer den Kopf je Teil


def _split_points(text: str, size: int) -> list[tuple[int, int]]:
    """Teile <= size, nach Moeglichkeit an einem Zeilenende (letzte 5 %)."""
    bounds, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            cut = text.rfind("\n", start + int(size * 0.95), end)
            if cut > start:
                end = cut + 1
        bounds.append((start, end))
        start = end
    return bounds


def _document_parts(kind, path, text, signature, attachments, limit, max_parts, state, limits):
    """Zerlegt ein grosses Dokument; liefert nur die noch nicht kuratierten
    Teile. Jeder Teil ist hoechstens so gross wie eine normale Einheit."""
    bounds = _split_points(text, max(1000, limit - _PART_HEADER))
    truncated = len(bounds) > max_parts
    if truncated:
        bounds = bounds[:max_parts]
    total = len(bounds)
    key = f"{kind}:{path.name}"
    prog = (state.document_parts or {}).get(key) or {}
    done = int(prog.get("done", 0)) if prog.get("hash") == signature["hash"] and prog.get("parts") == total else 0
    out = []
    for i, (a, b) in enumerate(bounds, start=1):
        if i <= done:
            continue
        head = (f"[Teil {i} von {total} des Dokuments {path.name} (Zeichen {a}-{b} von {len(text)}). "
                + ("Die vorherigen Teile sind bereits kuratiert und im Wissen enthalten; "
                   "hier nur Neues aus diesem Teil erfassen." if i > 1 else
                   "Die weiteren Teile folgen als eigene Schritte.") + "]\n\n")
        note = None
        if truncated and i == total:
            note = (f"inbox/{kind}/{path.name}: gekuerzt kuratiert ({len(text)} Zeichen, {total} Teile zu "
                    f"{limit} Zeichen) - der Rest fehlt im Wissen; Datei teilen oder max_parts_per_document erhoehen")
        out.append(DocumentSource(
            kind=kind, path=path, text=head + text[a:b], signature=signature,
            # Anhaenge (und deren Bilder) nur am letzten Teil: dort wird archiviert.
            attachments=attachments if i == total else [],
            truncation_note=note, part=i, parts=total,
        ))
    return out


def _archive_destination(document: DocumentSource, processed_dir: Path) -> Path:
    destination = processed_dir / document.path.name
    if not destination.exists():
        return destination

    digest = str(document.signature.get("hash", ""))[:12] or "unknown"
    destination = processed_dir / f"{document.path.stem}--{digest}{document.path.suffix}"
    counter = 2
    while destination.exists():
        destination = processed_dir / (
            f"{document.path.stem}--{digest}-{counter}{document.path.suffix}"
        )
        counter += 1
    return destination


def planned_archive_path(project: Path, document: DocumentSource) -> Path:
    """Ziel, unter dem archive_processed_document die Datei ablegen wird -
    fuer Quellverweise, die schon vor dem Archivieren geschrieben werden."""
    return _archive_destination(document, layout.inbox_processed_dir(project, document.kind))


def archive_processed_document(
    project: Path, document: DocumentSource, destination: Path | None = None
) -> Path:
    """Verschiebt eine erfolgreich kuratierte Quelldatei ins Archiv.

    Ein vorhandenes Archivobjekt wird nie ueberschrieben. Hat sich die Quelle
    seit dem Einlesen veraendert, bleibt sie in der Inbox, damit die neue
    Version in einem spaeteren Lauf verarbeitet werden kann.
    """

    if not document.path.is_file():
        raise FileNotFoundError(f"Inbox-Datei nicht mehr vorhanden: {document.path}")

    current_signature = _file_signature(document.path)
    if current_signature["hash"] != document.signature.get("hash"):
        raise RuntimeError(
            f"Inbox-Datei wurde waehrend der Kuratierung veraendert: {document.path.name}"
        )

    processed_dir = layout.inbox_processed_dir(project, document.kind)
    storage.ensure_dir(processed_dir)
    if destination is None or destination.exists():
        destination = _archive_destination(document, processed_dir)
    # Anhaenge zuerst ablegen: schlaegt das fehl, bleibt die Mail in der Inbox.
    if document.attachments:
        store_attachments(
            document.attachments,
            layout.inbox_attachments_dir(project, document.kind) / destination.stem,
        )
    document.path.rename(destination)
    return destination


def store_attachments(attachments: list[Attachment], target_dir: Path) -> list[Path]:
    """Legt Anhaenge als eigene Dateien ab. Vorhandene Dateien werden nie
    ueberschrieben; identischer Inhalt (z.B. nach einem abgebrochenen Lauf)
    wird nicht doppelt abgelegt."""
    storage.ensure_dir(target_dir)
    stored: list[Path] = []
    for att in attachments:
        candidate = target_dir / att.filename
        stem, suffix = candidate.stem, candidate.suffix
        counter = 2
        while candidate.exists():
            if candidate.read_bytes() == att.data:
                break
            candidate = target_dir / f"{stem} ({counter}){suffix}"
            counter += 1
        else:
            tmp = candidate.with_name(candidate.name + ".tmp")
            tmp.write_bytes(att.data)
            tmp.replace(candidate)
        stored.append(candidate)
    return stored
