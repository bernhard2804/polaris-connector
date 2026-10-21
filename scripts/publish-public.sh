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
# Release mit Auto-Update: Liegt in <ref> die signierte
# dist/polaris-chatgpt-connector-<Version>-signed.xpi zur Manifest-Version und
# gibt es im Spiegel noch kein Release v<Version>, wird die .xpi entpackt und
# ebenfalls geprueft. Dann schreibt das Skript updates.json in den Spiegel
# (nur dort, nicht im privaten Repo) und erzeugt mit --push das GitHub-Release
# v<Version> mit der .xpi. Firefox liest updates.json ueber die update_url im
# Manifest (raw.githubusercontent.com/.../main/updates.json), etwa einmal am
# Tag, und aktualisiert selbst.
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
PUBLIC_REPO="$(cd "$PUBLIC_DIR" && gh repo view --json nameWithOwner -q .nameWithOwner)"

work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
mkdir "$work/tree" "$work/xpi" "$work/release"
git archive "$SHA" | tar -C "$work/tree" -xf -
VERSION="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["version"])' "$work/tree/extension/manifest.json")"
XPI_NAME="polaris-chatgpt-connector-$VERSION-signed.xpi"
find "$work/tree" -type f \( -name '*.xpi' -o -name '*.zip' -o -name '*.tar.gz' \) -delete

python3 "$REPO/scripts/privacy-check.py" "$work/tree" || {
  echo "FEHLER: private Inhalte im Stand $REF (siehe oben) - nichts uebernommen." >&2
  exit 1
}

RELEASE=0
if ! git cat-file -e "$SHA:dist/$XPI_NAME" 2>/dev/null; then
  echo "Kein Release: dist/$XPI_NAME fehlt in $REF."
elif gh release view "v$VERSION" --repo "$PUBLIC_REPO" >/dev/null 2>&1; then
  echo "Kein Release: v$VERSION gibt es im Spiegel schon."
else
  XPI="$work/release/$XPI_NAME"
  git show "$SHA:dist/$XPI_NAME" > "$XPI"
  (cd "$work/xpi" && bsdtar -xf "$XPI")
  RELEASE=1
  python3 - "$work/xpi" "$VERSION" "$(python3 -c 'import json,sys;m=json.load(open(sys.argv[1]));g=m["browser_specific_settings"]["gecko"];print(g["id"])' "$work/tree/extension/manifest.json")" <<'PY' || RELEASE=0
import json, os, sys
root, version, addon_id = sys.argv[1:4]
m = json.load(open(os.path.join(root, "manifest.json")))
gecko = m.get("browser_specific_settings", {}).get("gecko", {})
errors = []
if m.get("version") != version:
    errors.append(f"Version der .xpi {m.get('version')} != Manifest {version}")
if gecko.get("id") != addon_id:
    errors.append(f"ID der .xpi {gecko.get('id')} != {addon_id}")
meta = os.path.join(root, "META-INF")
if not (os.path.isdir(meta) and any(n.startswith("mozilla") for n in os.listdir(meta))):
    errors.append(".xpi ist nicht von Mozilla signiert (META-INF/mozilla.* fehlt)")
if not gecko.get("update_url"):
    print("WARNUNG: .xpi ohne update_url - Firefox aktualisiert diese Version nicht selbst weiter.", file=sys.stderr)
for e in errors:
    print("FEHLER:", e, file=sys.stderr)
sys.exit(1 if errors else 0)
PY
  python3 "$REPO/scripts/privacy-check.py" "$work/xpi" || RELEASE=0
  [ "$RELEASE" = 1 ] || echo "KEIN RELEASE: $XPI_NAME ist nicht freigabefaehig (siehe oben); der Code-Stand wird trotzdem gespiegelt." >&2
fi
if [ "$RELEASE" = 1 ]; then
  python3 - "$XPI" "$VERSION" "$PUBLIC_REPO" "$work/xpi/manifest.json" > "$work/release/updates.json" <<'PY'
import hashlib, json, os, sys
xpi, version, repo, manifest = sys.argv[1:5]
addon_id = json.load(open(manifest))["browser_specific_settings"]["gecko"]["id"]
digest = hashlib.sha256(open(xpi, "rb").read()).hexdigest()
link = f"https://github.com/{repo}/releases/download/v{version}/{os.path.basename(xpi)}"
print(json.dumps({"addons": {addon_id: {"updates": [
    {"version": version, "update_link": link, "update_hash": f"sha256:{digest}"}]}}}, indent=2))
PY
fi

rsync -a --delete --exclude=/.git --exclude=/updates.json "$work/tree/" "$PUBLIC_DIR/"
[ "$RELEASE" = 1 ] && cp "$work/release/updates.json" "$PUBLIC_DIR/updates.json"
cd "$PUBLIC_DIR"
git add -A
if git diff --cached --quiet; then
  echo "Spiegel ist bereits auf dem Stand von $REF."
else
  git commit -q -m "Polaris $VERSION (Stand $(date +%F))"
  git show --stat --format='Neuer Spiegel-Commit %h: %s' HEAD | head -40 || true
fi
if [ "$PUSH" = 1 ]; then
  git push -u origin HEAD
  if [ "$RELEASE" = 1 ]; then
    gh release create "v$VERSION" --repo "$PUBLIC_REPO" --target "$(git rev-parse HEAD)" --latest \
      --title "Polaris $VERSION" \
      --notes "Signierte Firefox-Erweiterung $VERSION. Installieren: .xpi in ein Firefox-Fenster ziehen; danach aktualisiert Firefox selbst." \
      "$work/release/$XPI_NAME"
  fi
else
  [ "$RELEASE" = 1 ] && echo "Release v$VERSION (.xpi geprueft) entsteht beim Push."
  echo "Noch nicht veroeffentlicht. Pruefen, dann: $0 $REF --push"
fi
