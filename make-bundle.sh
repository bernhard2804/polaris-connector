#!/usr/bin/env bash
# Baut ein Installationspaket zur Weitergabe ohne git:
#   ./make-bundle.sh        -> dist/polaris-setup-<Version>.tar.gz und .zip
# Inhalt: Programmcode (Bridge, Curator, Widget, Units, Erweiterungs-
# Quellen), die neueste signierte .xpi und install.sh - ohne Tests,
# Experimente, alte AMO-ZIPs und ohne lokale Daten. Nur versionierte Dateien
# (git ls-files); der Empfaenger braucht kein git.
# Seit 2026-10-08 gehoert das ganze "Polaris" dazu (Nutzervorgabe):
#   extras/omarchy-backup/     CLI (committeter Stand von $OB_REPO)
#   extras/mst.omarchy-backup/ Backup-Widget (committeter Stand von $OB_PLUGIN)
#   extras/mst.agent-launchers/ Agenten-Starter (committeter Stand von $AL_PLUGIN, seit 2026-10-15)
#   memory/                    memlint + leere Vorlage des Maschinengedaechtnisses
# Vor dem Packen laeuft scripts/privacy-check.py; bei privaten Inhalten
# (Home-Pfad, E-Mail, Projektnamen) bricht der Bau ab.
# Entpacken und installieren:
#   tar -xzf polaris-setup-<Version>.tar.gz && polaris-setup-<Version>/install.sh --projects <Ordner>
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
cd "$REPO"
VERSION="$(python3 -c 'import json;print(json.load(open("extension/manifest.json"))["version"])')"
NAME="polaris-setup-$VERSION"
OUT="$REPO/dist/$NAME.tar.gz"
XPI="$(ls -1 dist/*-signed.xpi 2>/dev/null | sort -V | tail -n 1 || true)"
[ -n "$XPI" ] || echo "WARNUNG: keine signierte .xpi in dist/ - Paket ohne Erweiterung" >&2
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "WARNUNG: uncommittete Aenderungen - das Paket enthaelt den Stand der Arbeitskopie" >&2
fi
OB_REPO="${OB_REPO:-$HOME/Projects/omarchy-backup}"
OB_PLUGIN="${OB_PLUGIN:-$HOME/.config/omarchy/plugins/mst.omarchy-backup}"
AL_PLUGIN="${AL_PLUGIN:-$HOME/.config/omarchy/plugins/mst.agent-launchers}"
LIVE_MEMLINT="$HOME/.config/ai-agents/tools/memlint.py"
if [ -f "$LIVE_MEMLINT" ] && ! cmp -s "$LIVE_MEMLINT" memory/tools/memlint.py; then
  echo "FEHLER: memory/tools/memlint.py weicht von $LIVE_MEMLINT ab." >&2
  echo "  Uebernehmen: cp '$LIVE_MEMLINT' memory/tools/memlint.py, pruefen, committen." >&2
  exit 1
fi
for sk in memory/template/skills/*/; do
  live="$HOME/.config/ai-agents/skills/$(basename "$sk")/SKILL.md"
  if [ -f "$live" ] && ! cmp -s "$live" "${sk}SKILL.md"; then
    echo "FEHLER: ${sk}SKILL.md weicht von $live ab." >&2
    echo "  Uebernehmen: cp '$live' '${sk}SKILL.md', pruefen, committen." >&2
    exit 1
  fi
done

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
STAGE="$work/$NAME"
mkdir -p "$STAGE"
list="$work/list"
git ls-files -z | while IFS= read -r -d '' f; do
  case "$f" in
    tests/*|*/tests/*|experiments/*|*.zip|dist/*) continue ;;
  esac
  printf '%s\0' "$f"
done > "$list"
[ -n "$XPI" ] && printf '%s\0' "$XPI" >> "$list"
tar -C "$REPO" --null -T "$list" -cf - | tar -C "$STAGE" -xf -

# Weitere Polaris-Bausteine aus eigenen Repos: nur der committete Stand.
extra() {  # extra <git-Checkout> <Zielordner im Paket>
  local src="$1" dst="$STAGE/extras/$2"
  if [ ! -d "$src/.git" ]; then
    echo "FEHLER: $src ist kein git-Checkout - Baustein $2 fehlt (OB_REPO/OB_PLUGIN/AL_PLUGIN setzen)" >&2
    exit 1
  fi
  [ -z "$(git -C "$src" status --porcelain --untracked-files=no)" ] \
    || echo "WARNUNG: $src hat uncommittete Aenderungen - im Paket ist nur der committete Stand" >&2
  mkdir -p "$dst"
  git -C "$src" archive HEAD | tar -C "$dst" -xf -
  rm -rf -- "$dst/tests"
  echo "  $2: $(git -C "$src" rev-parse --short HEAD)"
}
echo "Bausteine:"
extra "$OB_REPO" omarchy-backup
extra "$OB_PLUGIN" mst.omarchy-backup
extra "$AL_PLUGIN" mst.agent-launchers

python3 "$REPO/scripts/privacy-check.py" "$STAGE" || {
  echo "FEHLER: private Inhalte im Paket (siehe oben) - nichts gebaut." >&2
  echo "  Fundstellen neutralisieren oder allgemeine Begriffe in ~/.config/polaris/bundle-allow.txt eintragen." >&2
  exit 1
}

tar -C "$work" --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -czf "$OUT" "$NAME"
echo "Gebaut: $OUT ($(du -h "$OUT" | cut -f1))"

# Dasselbe als ZIP zum Weiterreichen per Mail/Messenger. Ausfuehrungsrechte
# stehen in den Unix-Attributen (unzip stellt sie wieder her).
ZIP="${OUT%.tar.gz}.zip"
python3 - "$work" "$NAME" "$ZIP" <<'PY'
import os, sys, zipfile
work, name, out = sys.argv[1:4]
tmp = out + ".tmp"
with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(os.path.join(work, name)):
        dirs.sort()
        for f in sorted(files):
            full = os.path.join(root, f)
            info = zipfile.ZipInfo(os.path.relpath(full, work), date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = (os.stat(full).st_mode & 0o777 | 0o100000) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            with open(full, "rb") as fh:
                zf.writestr(info, fh.read())
os.replace(tmp, out)
PY
echo "Gebaut: $ZIP ($(du -h "$ZIP" | cut -f1))"
