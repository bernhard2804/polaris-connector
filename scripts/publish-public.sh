#!/usr/bin/env bash
# Ueberfuehrt einen freigegebenen Stand in den oeffentlichen Spiegel (seit 2026-10-21).
#   scripts/publish-public.sh [<ref>] [--push]     <ref> Standard: HEAD
# Das private Repo behaelt die volle Historie; der Spiegel bekommt je Freigabe
# genau einen Commit mit dem Dateistand von <ref>, ohne dessen Historie und
# ohne Binaerpakete (dist/*.xpi, *.tar.gz, *.zip). Vorher laeuft
# scripts/privacy-check.py; bei einem Treffer wird nichts uebernommen.
# Ohne --push wird nur lokal committet; pruefen, dann erneut mit --push.
# Spiegel-Arbeitskopie: $PUBLIC_DIR (Standard ~/Projects/polaris-public).
#
# Vorschlaege aus dem Spiegel zurueckholen (Dateistand ist identisch, nur die
# Historie nicht):
#   git fetch public pull/<Nr>/head:pr-<Nr>
#   git format-patch --stdout public/main..pr-<Nr> | git am -3
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
PUBLIC_DIR="${PUBLIC_DIR:-$HOME/Projects/polaris-public}"
REF=HEAD PUSH=0
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    -*) echo "Unbekannte Option: $arg" >&2; exit 2 ;;
    *) REF="$arg" ;;
  esac
done

cd "$REPO"
SHA="$(git rev-parse --verify "$REF^{commit}")"
[ -d "$PUBLIC_DIR/.git" ] || { echo "FEHLER: kein Spiegel unter $PUBLIC_DIR (git clone des oeffentlichen Repos)." >&2; exit 1; }
if [ -n "$(git -C "$PUBLIC_DIR" status --porcelain)" ]; then
  echo "FEHLER: $PUBLIC_DIR hat uncommittete Aenderungen." >&2; exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
git archive "$SHA" | tar -C "$work" -xf -
find "$work" -type f \( -name '*.xpi' -o -name '*.zip' -o -name '*.tar.gz' \) -delete

python3 "$REPO/scripts/privacy-check.py" "$work" || {
  echo "FEHLER: private Inhalte im Stand $REF (siehe oben) - nichts uebernommen." >&2
  exit 1
}

rsync -a --delete --exclude=/.git "$work/" "$PUBLIC_DIR/"
cd "$PUBLIC_DIR"
git add -A
if git diff --cached --quiet; then
  echo "Spiegel ist bereits auf dem Stand von $REF."
else
  VERSION="$(python3 -c 'import json;print(json.load(open("extension/manifest.json"))["version"])')"
  git commit -q -m "Polaris $VERSION (Stand $(date +%F))"
  git show --stat --format='Neuer Spiegel-Commit %h: %s' HEAD | tail -n +1 | head -40
fi
if [ "$PUSH" = 1 ]; then
  git push -u origin HEAD
else
  echo "Noch nicht veroeffentlicht. Pruefen, dann: $0 $REF --push"
fi
