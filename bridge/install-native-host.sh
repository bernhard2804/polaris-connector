#!/usr/bin/env bash
# Registriert den Polaris-Native-Messaging-Host bei Firefox (idempotent).
#   ./install-native-host.sh            installieren/aktualisieren
#   ./install-native-host.sh --remove   Registrierung entfernen
#
# Ziel ist ~/.mozilla/native-messaging-hosts/ - dort sucht Firefox auch dann,
# wenn das Profil (wie auf dem Entwicklungsrechner) unter ~/.config/mozilla/ liegt; auf
# dem Entwicklungsrechner liegt dort bereits die KeePassXC-Registrierung. Ein aktuelles
# Firefox mit Profil unter ~/.config/mozilla legt ~/.mozilla selbst nie an
# (frisches Omarchy, 2026-10-15). Dann wird nur native-messaging-hosts/
# angelegt - genau der Zustand des Entwicklungsrechners, wo Firefox damit beim neuen
# Profilort bleibt. Ein ~/.mozilla/firefox wird nie angelegt.
set -euo pipefail

NAME="polaris_bridge"
EXTENSION_ID="polaris-chatgpt-connector@mstarchy.local"
HOST_BIN="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)/bin/polaris-native-host"
TARGET_DIR="$HOME/.mozilla/native-messaging-hosts"
TARGET="$TARGET_DIR/$NAME.json"

if [ "${1:-}" = "--remove" ]; then
  rm -f -- "$TARGET"
  echo "Entfernt: $TARGET"
  exit 0
fi

if [ ! -d "$HOME/.mozilla" ] && [ ! -d "$HOME/.config/mozilla/firefox" ]; then
  echo "FEHLER: kein Firefox-Profil (weder ~/.mozilla noch ~/.config/mozilla/firefox)." >&2
  echo "Firefox einmal starten, dann erneut ausfuehren." >&2
  exit 1
fi
[ -x "$HOST_BIN" ] || { echo "FEHLER: $HOST_BIN fehlt oder ist nicht ausfuehrbar" >&2; exit 1; }

mkdir -p "$TARGET_DIR"
tmp="$(mktemp "$TARGET_DIR/.${NAME}.XXXXXX")"
python3 - "$tmp" "$NAME" "$HOST_BIN" "$EXTENSION_ID" <<'PY'
import json, sys
out, name, path, ext = sys.argv[1:5]
json.dump({
    "name": name,
    "description": "Polaris Bridge: legt ChatGPT-Nachrichten im lokalen Projektordner ab",
    "path": path,
    "type": "stdio",
    "allowed_extensions": [ext],
}, open(out, "w"), indent=2)
PY
chmod 644 "$tmp"
mv -f -- "$tmp" "$TARGET"
echo "Registriert: $TARGET -> $HOST_BIN"
