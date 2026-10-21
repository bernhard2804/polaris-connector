#!/usr/bin/env python3
"""Sperre gegen private Inhalte im Installationspaket (seit 2026-10-08).

Durchsucht die Dateien eines entpackten Pakets nach Angaben, die nur zu
diesem Rechner bzw. Nutzer gehoeren:
  - der eigene Home-Pfad (z. B. /home/<name>/)
  - die git-E-Mail-Adresse (git config user.email)
  - die Namen der Projektordner unter den Polaris-Wurzeln (polaris roots)
  - weitere Begriffe aus ~/.config/polaris/bundle-deny.txt (eine Zeile je Begriff)
Ausnahmen (z. B. allgemeine Ordnernamen wie "Templates") stehen in
~/.config/polaris/bundle-allow.txt. Beide Listen bleiben lokal und kommen nie
ins Paket. Zeilen mit Urheberangabe (Copyright, "author") sind erlaubt.

Aufruf:  privacy-check.py <Ordner>      Exit 0 = sauber, 1 = Treffer
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

CFG = Path.home() / ".config" / "polaris"
ATTRIBUTION = re.compile(r'Copyright|"author"\s*:')


def _lines(path: Path) -> list[str]:
    try:
        return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")]
    except OSError:
        return []


def _project_folders() -> list[str]:
    try:
        import yaml
        roots = (yaml.safe_load((CFG / "config.yaml").read_text()) or {}).get("project_roots") or []
    except Exception:  # noqa: BLE001 - ohne Polaris-Konfiguration gibt es keine Projektnamen
        return []
    names = []
    for root in roots:
        try:
            names += [p.name for p in Path(os.path.expanduser(str(root))).iterdir()
                      if p.is_dir() and not p.name.startswith(".")]
        except OSError:
            pass
    return names


def terms() -> list[str]:
    allow = {t.casefold() for t in _lines(CFG / "bundle-allow.txt")}
    found = [str(Path.home()) + "/"]
    try:
        email = subprocess.run(["git", "config", "--global", "user.email"],
                               capture_output=True, text=True, timeout=5).stdout.strip()
        if email:
            found.append(email)
    except (OSError, subprocess.SubprocessError):
        pass
    found += [n for n in _project_folders() if len(n) >= 3]
    found += _lines(CFG / "bundle-deny.txt")
    return sorted({t for t in found if t.casefold() not in allow}, key=str.casefold)


def scan(root: Path, words: list[str]) -> list[str]:
    if not words:
        return []
    pattern = re.compile("|".join(
        (re.escape(w) if not w[0].isalnum() else rf"(?<!\w){re.escape(w)}(?!\w)") for w in words), re.I)
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # Binaerdateien (.xpi, Bilder)
        for no, line in enumerate(text.splitlines(), 1):
            if ATTRIBUTION.search(line):
                continue
            m = pattern.search(line)
            if m:
                hits.append(f"{path.relative_to(root)}:{no}: {m.group(0)!r}: {line.strip()[:120]}")
    return hits


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    words = terms()
    hits = scan(Path(sys.argv[1]), words)
    for h in hits:
        print(h)
    print(f"Privatsphaere-Pruefung: {len(words)} Begriffe, {len(hits)} Treffer", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
