#!/usr/bin/env python3
"""memlint - deterministische Pruefung des Maschinengedaechtnisses (seit 2026-09-25).

Kein LLM, keine Abhaengigkeiten ausser der Python-Standardbibliothek. Prueft,
dass das gemeinsame Gedaechtnis unter ~/.config/ai-agents klein,
nachvollziehbar und konsistent bleibt:

  Struktur   AI_HANDOFF.md/AGENTS.md vorhanden, jede handoff-Datei im Index,
             jede knowledge-/history-Datei von ihrem Handoff verlinkt
  Verweise   alle Pfadangaben auf Gedaechtnisdateien existieren
  Herkunft   mem-Tags syntaktisch/semantisch gueltig, IDs eindeutig,
             supersedes/superseded_by/ref=mem:<id> zeigen auf vorhandene IDs
  Lesbarkeit Ueberholtes ist auch ohne Kommentare sichtbar ("Veraltet: ..."
             in der Ueberschrift, Zeile "Ersetzt durch: ...")
  Aktualitaet  handoff/ enthaelt nichts Ueberholtes oder Historisches;
             ueberholtes Wissen nennt seinen Nachfolger
  Rueckkopie laengere Absaetze aus history/knowledge tauchen nicht woertlich
             im Handoff auf ("Handoff ist kein Archiv")
  Groesse    Handoff-Dateien ueber der Richtgroesse werden gemeldet (Warnung)
  Geheimnisse offensichtliche Passwort-/Token-Angaben im Klartext
  Muster     type=pattern (gepruefte Loesungsmuster) nur in knowledge/ und
             nur mit Beleg (ref=history:/commit:/url:)
  Skills     skills/<name>/SKILL.md mit Kopf name=<name> und description
  System     Agenten-Symlinks (Regeln und Skills), Backup-Abdeckung
             (--system, Standard an)

mem-Tag (eine Zeile, direkt unter einer ##-Ueberschrift):
  <!-- mem: id=<id> type=<typ> status=<status> date=YYYY-MM-DD
            [source=<wer>] [ref=<a>,<b>] [supersedes=<id>] [superseded_by=<id>] -->

Aufruf:  memlint.py [--root DIR] [--json] [--no-system]
Exit:    0 = keine Fehler (Warnungen moeglich), 1 = Fehler
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".config" / "ai-agents"
LAYERS = ("handoff", "knowledge", "history")
TYPES = {"current_state", "architecture", "decision", "gotcha", "procedure", "open_item", "incident", "history",
         "pattern"}
# Ein Loesungsmuster gilt nur mit Beleg einer geprueften Anwendung.
PATTERN_EVIDENCE_KINDS = ("history", "commit", "url")
STATUSES = {"current", "superseded", "deprecated", "historical", "done"}
HANDOFF_ALLOWED_STATUS = {"current"}
VISIBLE_PREFIX = {"superseded": "Veraltet:", "deprecated": "Abgekündigt:"}
HANDOFF_SOFT_LIMIT = 10_000   # Richtgroesse (Warnung), kein hartes Limit
DUPLICATE_MIN_CHARS = 240     # ab dieser Laenge gilt ein Absatz als "Inhalt"
TAG_RE = re.compile(r"<!--\s*mem:(.*?)-->", re.S)
KV_RE = re.compile(r"([a-z_]+)=(\S+)")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PATH_REF_RE = re.compile(r"(?<![\w/.~-])((?:handoff|knowledge|history|system-history)/[\w./-]+\.md)")
SECRET_RES = [
    re.compile(r"(?i)\b(pass(?:wort|word)|kennwort)\b[^\n`]{0,60}\b(auf|=|:|lautet|ist)\s*`[^`\s]{3,}`"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret)\s*[=:]\s*['\"`]?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
]
# Stand omarchy-backup lib/common.sh (OB_SECRET_GLOBS): solche Dateinamen
# wuerden nie gesichert.
BACKUP_SECRET_GLOBS = ["*.credentials.json", "credentials.json", "auth.json", "oauth_creds*.json",
                       "*token*", "*secret*", "*.key", "*.pem", "id_rsa*", "id_ed25519*", "id_ecdsa*",
                       ".netrc", "*.p12", "*.pfx", "shadow", "gshadow"]
# Einstiege der Agenten ins Gedaechtnis (Symlinks). Seit 2026-10-07 aus
# entrypoints.conf (anbieterneutral, eine Zeile je Agent); dies ist nur der
# Rueckfall, falls die Datei fehlt.
AGENT_LINKS = {
    "~/.claude/CLAUDE.md": "AGENTS.md",
    "~/.codex/AGENTS.md": "AGENTS.md",
    "~/.gemini/GEMINI.md": "AGENTS.md",
    "~/Work/AI_HANDOFF.md": "AI_HANDOFF.md",
}
# Skill-Ordner der Agenten. Eine Zeile `<Ordner>/  skills/` in entrypoints.conf
# verlinkt jeden Skill aus skills/<name>/ dorthin; ohne solche Zeilen gelten
# diese Ordner (seit 2026-10-16), wie Omarchy fuer seine Skills. Gemini CLI
# (0.63) und opencode lesen ~/.agents/skills mit; ~/.gemini/skills waere doppelt.
SKILL_DIRS = ["~/.agents/skills/", "~/.claude/skills/", "~/.codex/skills/"]
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def load_entrypoints(root: Path) -> dict[str, str]:
    """entrypoints.conf: `<Pfad> <Ziel relativ zu root> [Kommentar]` je Zeile."""
    conf = root / "entrypoints.conf"
    if not conf.is_file():
        return dict(AGENT_LINKS)
    links = {}
    for line in conf.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        parts = line.split()
        if len(parts) >= 2:
            links[parts[0]] = parts[1]
    return links


def skill_names(root: Path) -> list[str]:
    d = root / "skills"
    return sorted(p.name for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def expand_entrypoints(root: Path) -> dict[str, str]:
    """Einstiege als konkrete Links: Zeilen mit `/` am Ende (Skill-Ordner)
    werden je Skill aus skills/ aufgeloest."""
    raw = load_entrypoints(root)
    dirs = {k: v for k, v in raw.items() if k.endswith("/")}
    links = {k: v for k, v in raw.items() if not k.endswith("/")}
    for d, target in (dirs or {s: "skills/" for s in SKILL_DIRS}).items():
        for name in skill_names(root):
            links[d + name] = target.rstrip("/") + "/" + name
    return links


def agent_dir_present(p: Path) -> bool:
    """Ist der Agent eingerichtet? Bei Skill-Links reicht der Agentenordner
    (z. B. ~/.claude), der skills/-Ordner darf noch fehlen."""
    if p.parent.name == "skills":
        return p.parent.parent.is_dir()
    return p.parent.is_dir()


def link_entrypoints(root: Path) -> list[str]:
    """Legt fehlende Einstiege an, wenn der Ordner des Agenten existiert
    (Agent installiert/benutzt). Ersetzt nie eine vorhandene Datei."""
    done = []
    for link, target in expand_entrypoints(root).items():
        p = Path(link).expanduser()
        if p.is_symlink() or p.exists() or not agent_dir_present(p):
            continue
        p.parent.mkdir(exist_ok=True)
        p.symlink_to(root / target)
        done.append(f"{link} -> {root / target}")
    return done


@dataclass
class Finding:
    level: str  # error | warning
    check: str
    file: str
    message: str


@dataclass
class Tag:
    file: Path
    line: int
    section: str
    fields: dict
    following: str = ""


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    def add(self, level, check, file, message):
        self.findings.append(Finding(level, check, str(file), message))

    @property
    def errors(self):
        return [f for f in self.findings if f.level == "error"]


def parse_tags(path: Path) -> list[Tag]:
    tags, section = [], ""
    lines = path.read_text(encoding="utf-8").splitlines()
    text = "\n".join(lines)
    for m in TAG_RE.finditer(text):
        line_no = text.count("\n", 0, m.start()) + 1
        for back in range(line_no - 1, -1, -1):
            if lines[back].startswith("#"):
                section = lines[back].lstrip("#").strip()
                break
        following = "\n".join(lines[line_no:line_no + 3])
        tags.append(Tag(path, line_no, section, dict(KV_RE.findall(m.group(1))), following))
    return tags


def sections_without_tag(path: Path) -> list[str]:
    """##-Abschnitte ohne mem-Tag in den ersten 3 Zeilen nach der Ueberschrift."""
    lines = path.read_text(encoding="utf-8").splitlines()
    missing = []
    for i, line in enumerate(lines):
        if line.startswith("## "):
            window = "\n".join(lines[i + 1:i + 4])
            if "<!-- mem:" not in window:
                missing.append(line[3:].strip())
    return missing


def paragraphs(text: str) -> set[str]:
    text = TAG_RE.sub("", text)
    out = set()
    for block in re.split(r"\n\s*\n", text):
        # Aufzaehlungspunkte einzeln vergleichen: eine einzelne zurueckkopierte
        # Zeile aus einer langen Liste soll auffallen.
        for item in re.split(r"\n(?=\s*(?:[-*]|\d+\.)\s)", block):
            norm = " ".join(re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", item).split())
            if len(norm) >= DUPLICATE_MIN_CHARS:
                out.add(norm)
    return out


def lint(root: Path, system: bool = True) -> Report:
    r = Report()
    root = root.expanduser()
    index, agents = root / "AI_HANDOFF.md", root / "AGENTS.md"
    for f in (index, agents):
        if not f.is_file():
            r.add("error", "structure", f, "fehlt")
    md_files = sorted(p for p in root.rglob("*.md") if ".git" not in p.parts)
    layer_files = {layer: sorted((root / layer).glob("*.md")) for layer in LAYERS}
    index_text = index.read_text(encoding="utf-8") if index.is_file() else ""

    # Struktur: Index kennt jedes Handoff, Handoff verlinkt sein Wissen/seine Historie
    for h in layer_files["handoff"]:
        if f"handoff/{h.name}" not in index_text:
            r.add("error", "structure", h, "nicht im Index AI_HANDOFF.md verlinkt")
    for layer in ("knowledge", "history"):
        for f in layer_files[layer]:
            handoff = root / "handoff" / f.name
            referenced = handoff.is_file() and f"{layer}/{f.name}" in handoff.read_text(encoding="utf-8")
            if not referenced and f"{layer}/{f.name}" not in index_text:
                r.add("error", "structure", f, f"weder von handoff/{f.name} noch vom Index verlinkt")

    # Verweise
    for f in md_files:
        for ref in set(PATH_REF_RE.findall(f.read_text(encoding="utf-8"))):
            if "<" in ref or "*" in ref:
                continue
            if not (root / ref).is_file():
                r.add("error", "links", f, f"Verweis auf fehlende Datei {ref}")

    # Herkunft / mem-Tags
    # Tags zaehlen nur in den drei Schichten; AGENTS.md/Index duerfen das
    # Format als Beispiel zeigen.
    all_tags: list[Tag] = []
    for f in (f for layer in LAYERS for f in layer_files[layer]):
        all_tags.extend(parse_tags(f))
    ids: dict[str, Tag] = {}
    for t in all_tags:
        where = f"{t.file.relative_to(root)}:{t.line}"
        tid = t.fields.get("id")
        for key in ("id", "type", "status", "date"):
            if key not in t.fields:
                r.add("error", "provenance", where, f"mem-Tag ohne Pflichtfeld {key}")
        if tid and not ID_RE.match(tid):
            r.add("error", "provenance", where, f"ungueltige id {tid!r}")
        if tid in ids:
            r.add("error", "provenance", where, f"id {tid!r} doppelt (auch {ids[tid].file.name}:{ids[tid].line})")
        elif tid:
            ids[tid] = t
        if t.fields.get("type") and t.fields["type"] not in TYPES:
            r.add("error", "provenance", where, f"unbekannter type {t.fields['type']!r}")
        if t.fields.get("status") and t.fields["status"] not in STATUSES:
            r.add("error", "provenance", where, f"unbekannter status {t.fields['status']!r}")
        if t.fields.get("date") and not DATE_RE.match(t.fields["date"]):
            r.add("error", "provenance", where, f"date nicht im Format YYYY-MM-DD: {t.fields['date']!r}")
    for t in all_tags:
        where = f"{t.file.relative_to(root)}:{t.line}"
        for key in ("supersedes", "superseded_by"):
            for target in filter(None, t.fields.get(key, "").split(",")):
                if target not in ids:
                    r.add("error", "superseding", where, f"{key}={target} verweist auf unbekannte id")
        for ref in filter(None, t.fields.get("ref", "").split(",")):
            if ref.split(":", 1)[0] in ("mem", "history") and ref.split(":", 1)[1] not in ids:
                r.add("error", "provenance", where, f"ref={ref} verweist auf unbekannte id")
        status = t.fields.get("status")
        if status == "superseded" and not t.fields.get("superseded_by"):
            r.add("error", "superseding", where, "status=superseded ohne superseded_by")
        if t.fields.get("superseded_by") and status not in ("superseded", "deprecated"):
            r.add("error", "superseding", where, "superseded_by gesetzt, status aber nicht superseded/deprecated")
        # Menschenlesbarkeit: Status muss auch ohne Kommentare (gerendertes
        # Markdown) erkennbar sein.
        if status in VISIBLE_PREFIX and not t.section.startswith(VISIBLE_PREFIX[status]):
            r.add("error", "readability", where,
                  f"Ueberschrift eines {status}-Eintrags muss mit {VISIBLE_PREFIX[status]!r} beginnen")
        if status == "superseded" and "Ersetzt durch:" not in t.following:
            r.add("error", "readability", where, "superseded-Eintrag ohne sichtbare Zeile 'Ersetzt durch:'")
        layer = t.file.parent.name
        if t.fields.get("type") == "pattern":
            if layer != "knowledge":
                r.add("error", "pattern", where, "Loesungsmuster gehoeren nach knowledge/")
            kinds = {ref.split(":", 1)[0] for ref in t.fields.get("ref", "").split(",") if ":" in ref}
            if not kinds & set(PATTERN_EVIDENCE_KINDS):
                r.add("error", "pattern", where,
                      "Loesungsmuster ohne Beleg (ref=history:<id>, commit: oder url: noetig)")
        if layer == "handoff" and status not in HANDOFF_ALLOWED_STATUS:
            r.add("error", "current-state", where, f"Handoff enthaelt Eintrag mit status={status} (nur current erlaubt)")
        if layer == "handoff" and t.fields.get("type") == "history":
            r.add("error", "current-state", where, "Handoff enthaelt einen history-Eintrag")
        if layer == "history" and status not in ("historical", "superseded"):
            r.add("warning", "provenance", where, f"history-Eintrag mit status={status}")
    for f in layer_files["knowledge"] + layer_files["history"]:
        for sec in sections_without_tag(f):
            r.add("error", "provenance", f.relative_to(root), f"Abschnitt ohne mem-Tag: {sec!r}")

    # Skills: skills/<name>/SKILL.md mit name und description im Kopf
    for name in skill_names(root):
        f = root / "skills" / name / "SKILL.md"
        m = FRONTMATTER_RE.match(f.read_text(encoding="utf-8")) if f.is_file() else None
        head = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line) if m else {}
        if not f.is_file():
            r.add("error", "skills", f"skills/{name}", "SKILL.md fehlt")
        elif head.get("name", "").strip() != name or not head.get("description", "").strip():
            r.add("error", "skills", f.relative_to(root), f"Kopf braucht name: {name} und description:")

    # Rueckkopie: Absaetze aus history/knowledge woertlich im Handoff
    archive_pars: dict[str, str] = {}
    for f in layer_files["history"] + layer_files["knowledge"]:
        for p in paragraphs(f.read_text(encoding="utf-8")):
            archive_pars.setdefault(p, str(f.relative_to(root)))
    for h in layer_files["handoff"]:
        for p in paragraphs(h.read_text(encoding="utf-8")):
            if p in archive_pars:
                r.add("error", "duplicate", h.relative_to(root),
                      f"Absatz ist woertlich aus {archive_pars[p]} zurueckkopiert: {p[:70]}...")

    # Groesse
    sizes = {}
    for h in layer_files["handoff"]:
        size = h.stat().st_size
        sizes[h.name] = size
        if size > HANDOFF_SOFT_LIMIT:
            r.add("warning", "size", h.relative_to(root), f"{size} Bytes > Richtgroesse {HANDOFF_SOFT_LIMIT}")
    r.stats = {"handoff_bytes": sizes, "tags": len(all_tags), "files": len(md_files)}

    # Geheimnisse
    for f in md_files:
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if any(rx.search(line) for rx in SECRET_RES):
                r.add("error", "secrets", f"{f.relative_to(root)}:{n}", "moegliches Geheimnis im Klartext")

    if system:
        home = Path.home()
        for link, target in expand_entrypoints(root).items():
            p = Path(link).expanduser()
            if not agent_dir_present(p):
                continue  # Agent auf diesem Rechner nicht eingerichtet
            if not p.exists():
                r.add("error", "entrypoints", link, "fehlt (memlint.py --link legt ihn an)")
            elif p.resolve() != (root / target).resolve():
                r.add("error", "entrypoints", link, f"zeigt auf {p.resolve()} statt auf {root / target}")
        conf_dir = home / ".config" / "omarchy-backup"
        rules = []
        for cf in [conf_dir / "paths.conf", *sorted((conf_dir / "paths.d").glob("*"))]:
            if cf.is_file():
                for line in cf.read_text(encoding="utf-8").splitlines():
                    line = line.split("#", 1)[0].strip()
                    if " " in line:
                        kind, val = line.split(None, 1)
                        rules.append((kind, os.path.expanduser(val)))
        if not rules:
            r.add("warning", "backup", conf_dir, "keine omarchy-backup-Konfiguration gefunden")
        else:
            root_s = str(root)
            if not any(k == "include" and (root_s == v or root_s.startswith(v.rstrip("/") + "/")) for k, v in rules):
                r.add("error", "backup", root, "Gedaechtnis nicht per include in omarchy-backup erfasst")
            excludes = [v for k, v in rules if k == "exclude"]
            for f in sorted(p for p in root.rglob("*") if p.is_file() and not {".git", "__pycache__"} & set(p.parts)):
                s = str(f)
                if any(fnmatch.fnmatch(s, g) for g in excludes) or any(fnmatch.fnmatch(f.name, g) for g in BACKUP_SECRET_GLOBS):
                    r.add("error", "backup", f.relative_to(root), "Dateiname wird vom Backup ausgeschlossen")
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Prueft das Maschinengedaechtnis")
    ap.add_argument("--root", default=str(DEFAULT_ROOT))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-system", action="store_true", help="Symlinks/Backup nicht pruefen")
    ap.add_argument("--link", action="store_true",
                    help="fehlende Agenten-Einstiege aus entrypoints.conf anlegen (nie ersetzen)")
    args = ap.parse_args(argv)
    if args.link:
        for line in link_entrypoints(Path(args.root)):
            print(f"angelegt: {line}")
    report = lint(Path(args.root), system=not args.no_system)
    if args.json:
        print(json.dumps({"ok": not report.errors, "errors": len(report.errors),
                          "warnings": len(report.findings) - len(report.errors),
                          "findings": [f.__dict__ for f in report.findings], "stats": report.stats},
                         ensure_ascii=False))
    else:
        for f in report.findings:
            print(f"{f.level.upper():7} [{f.check}] {f.file}: {f.message}")
        print(f"{len(report.errors)} Fehler, {len(report.findings) - len(report.errors)} Warnungen, "
              f"{report.stats.get('tags', 0)} mem-Tags in {report.stats.get('files', 0)} Dateien")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
