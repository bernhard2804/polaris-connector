"""Projektwissens-Transfer: packt das kuratierte Wissen eines oder mehrerer
Projekte in ein ZIP, das man einem beliebigen Anbieter (ChatGPT, Claude, ...)
als Projektkontext geben kann.

Anlass (2026-09-25): Das Maschinengedaechtnis-ZIP (~/.config/ai-agents)
enthielt kein fachliches Projektwissen - zu Recht, denn der Curator schreibt
nur in die Projektordner und nie ins Maschinengedaechtnis. Der Rueckweg des
kuratierten Wissens zu einem anderen Anbieter brauchte einen eigenen Behaelter.

Transportiert werden nur die kuratierten Markdown-Dateien, unveraendert und
mit ihren polaris-source-Kommentaren (Provenance). Rohchats (raw/), Inbox,
Originaldokumente und .polaris/ bleiben bewusst draussen; sie sind Beleg und
Archiv im Projektordner, nicht Transfergut.
"""

from __future__ import annotations

import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import compact, docs_index, layout

README_NAME = "LIESMICH.md"
_SOURCE_RE = re.compile(r"<!--\s*polaris-source:\s*type=([a-z+]+)")
_SLUG_RE = re.compile(r"[^0-9A-Za-zÄÖÜäöüß_-]+")


@dataclass
class ProjectExport:
    name: str
    files: list[str] = field(default_factory=list)
    sources: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class TransferResult:
    path: str
    size_bytes: int
    projects: list[ProjectExport]


def curated_files(project: Path, with_archive: bool = False) -> list[Path]:
    candidates = [
        layout.knowledge_path(project),
        layout.decisions_path(project),
        layout.open_loops_path(project),
        docs_index.index_path(project),
    ]
    if with_archive:
        candidates.append(project / compact.ARCHIVE_NAME)
    return [p for p in candidates if p.is_file()]


def count_sources(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind in _SOURCE_RE.findall(text):
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


def default_output(project_names: list[str], now: datetime | None = None) -> Path:
    now = now or datetime.now()
    label = _SLUG_RE.sub("-", project_names[0]).strip("-") if len(project_names) == 1 else "alle"
    return Path.home() / "Downloads" / f"polaris-projektwissen-{label}-{now:%Y%m%d-%H%M}.zip"


def _fmt_sources(counts: dict[str, int]) -> str:
    if not counts:
        return "keine Herkunftsangaben"
    return ", ".join(f"{kind}: {n}" for kind, n in counts.items())


def build_readme(exports: list[ProjectExport], now: datetime) -> str:
    lines = [
        "# Polaris Projektwissen",
        "",
        f"Erzeugt am {now:%Y-%m-%d %H:%M} von `polaris-curator export`.",
        "",
        "Dieses Paket enthält das **kuratierte** Projektwissen, das der Polaris",
        "Curator aus ChatGPT- und Claude-Unterhaltungen sowie aus Projektdokumenten",
        "und E-Mails zusammengeführt hat. Es gehört keinem Anbieter: Die kanonische",
        "Fassung liegt als Markdown im jeweiligen Projektordner; dieses ZIP ist eine",
        "Momentaufnahme davon.",
        "",
        "Nicht enthalten sind Rohchats, Originaldokumente und Mail-Anhänge. Sie",
        "bleiben als Beleg im Projektordner (`raw/`, `inbox/`).",
        "",
        "## Dateien pro Projekt",
        "",
        "- `knowledge.md` – aktueller Wissensstand",
        "- `decisions.md` – Entscheidungen; „Ersetzt“ markierte gelten nicht mehr",
        "- `open-loops.md` – offene („Offen“) und erledigte („Erledigt“) Punkte",
        "- `dokumente-index.md` – Verzeichnis der verarbeiteten Dokumente und Mails",
        "  mit ihrem Pfad im Projektordner (die Dateien selbst sind nicht im Paket)",
        "- `knowledge-archiv.md` – nur falls enthalten: überholte Stände, nur Historie",
        "",
        "## So liest man die Einträge",
        "",
        "- Klassifikation: „[Fakt]“ ist direkt belegt, „[Stakeholder-Aussage]“ die",
        "  Wahrnehmung einer einzelnen Person oder Quelle, „[Hypothese]“ eine noch",
        "  nicht belegte Ableitung, „[Systemische Implikation]“ die Bedeutung für",
        "  Operating Model, Skalierung oder Wirtschaftlichkeit. Hypothesen und",
        "  Stakeholder-Aussagen nicht wie bestätigte Fakten behandeln.",
        "- Herkunft: Unter jedem Eintrag steht ein Kommentar",
        "  `<!-- polaris-source: type=… -->`. `type=chatgpt` bzw. `type=claude`",
        "  heißt, der Eintrag stammt aus einer Unterhaltung mit diesem Anbieter,",
        "  `type=chatgpt+claude` aus beiden, `type=document` aus einem Dokument oder",
        "  einer Mail (mit Pfad im Projektordner). Konversations- und Nachrichten-IDs",
        "  verweisen auf das Rohdatenarchiv.",
        "- Neuere, ausdrücklich bestätigte Informationen gehen älteren vor.",
        "",
        "## Inhalt",
        "",
    ]
    for exp in exports:
        lines.append(f"### {exp.name}")
        lines.append("")
        for rel in exp.files:
            lines.append(f"- `{exp.name}/{rel}` ({_fmt_sources(exp.sources.get(rel, {}))})")
        if not exp.files:
            lines.append("- (noch kein kuratiertes Wissen)")
        lines.append("")
    return "\n".join(lines)


def export(
    projects: list[Path],
    output: Path | None = None,
    with_archive: bool = False,
    now: datetime | None = None,
) -> TransferResult:
    now = now or datetime.now()
    names = [p.name for p in projects]
    if len(set(names)) != len(names):
        raise ValueError(f"Projektnamen nicht eindeutig: {names}")
    output = output or default_output(names, now)

    exports: list[ProjectExport] = []
    payload: list[tuple[str, bytes]] = []
    for project in projects:
        exp = ProjectExport(name=project.name)
        for path in curated_files(project, with_archive):
            data = path.read_bytes()
            exp.files.append(path.name)
            exp.sources[path.name] = count_sources(data.decode("utf-8", errors="replace"))
            payload.append((f"{project.name}/{path.name}", data))
        exports.append(exp)

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".polaris-export-", suffix=".zip", dir=output.parent)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(README_NAME, build_readme(exports, now))
            for arcname, data in payload:
                zf.writestr(arcname, data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, output)
    finally:
        tmp.unlink(missing_ok=True)
    return TransferResult(path=str(output), size_bytes=output.stat().st_size, projects=exports)
