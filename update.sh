#!/usr/bin/env bash
# Aktualisiert einen git-Checkout von Polaris (seit 2026-10-23), nach dem
# Vorbild von `omarchy plugin update`:
#
#   polaris update            neuen Stand holen, Aenderungen zeigen, nachfragen
#   polaris update --yes      ohne Nachfrage (Agenten, Skripte)
#   polaris update --check    nur nachsehen: Exit 0 = Update da, 1 = aktuell,
#                             2 = Fehler; schreibt den Zustand fuer den Health-Check
#
# Ablauf: git fetch -> nur Fast-Forward (lokale Commits oder geaenderte Dateien
# brechen ab, eigene Aenderungen gehoeren in einen Branch/Pull Request) ->
# install.sh fuer das, was schon eingerichtet ist (Units, Widget, Dock,
# Gedaechtnis) -> polaris-curator health. Wird der Health-Check durch das
# Update rot, geht es zurueck auf den alten Commit und install.sh laeuft erneut.
# Aus einem Installationspaket (kein git) gibt es kein Update hier: neues Paket
# laden und dessen install.sh ausfuehren.
set -uo pipefail

export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND="${GIT_SSH_COMMAND:-ssh -oBatchMode=yes}"

REPO="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# Fester Pfad wie im Health-Check (Python: Path.home()), nicht XDG_STATE_HOME.
STATE="$HOME/.local/state/polaris/update-check.json"
# Fuer Tests austauschbar; Standard sind die echten Programme im Repo.
INSTALL="${POLARIS_UPDATE_INSTALL:-$REPO/install.sh}"
HEALTH="${POLARIS_UPDATE_HEALTH:-$REPO/curator/bin/polaris-curator health --json}"

YES=0 CHECK=0
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "polaris update: unbekannte Option $arg" >&2; exit 2 ;;
  esac
done

fail() { echo "polaris update: $*" >&2; exit 2; }
g() { git -C "$REPO" "$@"; }
# Version der Erweiterung plus Commit: zwischen zwei Erweiterungs-Releases
# aendert sich sonst nur der Code, nicht die Nummer.
version() {
  local v
  v="$(g show "$1:extension/manifest.json" 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])' 2>/dev/null || echo "?")"
  echo "$v ($(g rev-parse --short "$1" 2>/dev/null))"
}
health_level() { $HEALTH 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("level","error"))' 2>/dev/null || echo error; }

write_state() {  # behind local remote
  mkdir -p "${STATE%/*}" 2>/dev/null || return 0
  printf '{"checked_at":"%s","behind":%s,"local":"%s","remote":"%s"}\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" >"$STATE.tmp" && mv -f "$STATE.tmp" "$STATE"
}

[ -d "$REPO/.git" ] || fail "$REPO ist kein git-Checkout. Update: neues Paket laden und dessen install.sh ausfuehren."
upstream="$(g rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null)" ||
  fail "der Branch $(g branch --show-current) hat kein Upstream (git branch --set-upstream-to origin/main)"

timeout 60 git -C "$REPO" fetch --quiet "${upstream%%/*}" || fail "git fetch fehlgeschlagen (Netz?)"
old="$(g rev-parse HEAD)" new="$(g rev-parse "$upstream")"
behind="$(g rev-list --count "HEAD..$upstream")" ahead="$(g rev-list --count "$upstream..HEAD")"
write_state "$behind" "$(version HEAD)" "$(version "$upstream")"

if [ "$behind" = 0 ]; then
  echo "Polaris $(version HEAD) ist aktuell."
  exit $(( CHECK ? 1 : 0 ))
fi
if [ "$CHECK" = 1 ]; then
  echo "Update verfuegbar: Polaris $(version HEAD) -> $(version "$upstream") ($behind Commit(s))"
  exit 0
fi

[ "$ahead" = 0 ] ||
  fail "$ahead lokale(r) Commit(s), die nicht in $upstream sind - kein Fast-Forward moeglich. Eigene Aenderungen in einen Branch legen oder als Pull Request einreichen."
[ -z "$(g status --porcelain --untracked-files=no)" ] ||
  fail "geaenderte Dateien in $REPO (git status) - erst committen oder zuruecklegen (git stash)."

echo "Polaris $(version HEAD) -> $(version "$upstream")"
g log --oneline "HEAD..$upstream"
g diff --stat HEAD "$upstream" | tail -n 25
echo
if [ "$YES" != 1 ]; then
  [ -t 0 ] || fail "ohne Terminal nur mit --yes"
  read -r -p "Aktualisieren? [j/N] " answer
  case "$answer" in j|J|y|Y|ja|Ja) ;; *) echo "Abgebrochen, nichts geaendert."; exit 0 ;; esac
fi

# install.sh nur fuer das, was schon eingerichtet ist - ein Update richtet
# nichts Neues ein (z. B. kein Dock, wenn es bewusst fehlt).
install_args=(--yes)
ls "$HOME"/.config/systemd/user/polaris-*.service >/dev/null 2>&1 || install_args+=(--no-units)
[ -d "$HOME/.config/omarchy/plugins/mst.polaris-curator" ] || install_args+=(--no-widget)
[ -d "$HOME/.config/omarchy/plugins/omadock" ] || install_args+=(--no-dock)
[ -d "$HOME/.config/ai-agents" ] || install_args+=(--no-memory)
install_args+=(--no-backup)

before="$(health_level)"
g merge --ff-only --quiet "$upstream" || fail "Fast-Forward fehlgeschlagen; nichts geaendert."
if ! "$INSTALL" "${install_args[@]}"; then
  install_failed=1
else
  install_failed=0
fi
after="$(health_level)"

if [ "$install_failed" = 1 ] || { [ "$after" = error ] && [ "$before" != error ]; }; then
  echo "polaris update: install.sh oder Health-Check nach dem Update fehlgeschlagen (vorher $before, nachher $after) - zurueck auf $(g rev-parse --short "$old")." >&2
  g reset --hard --quiet "$old"
  "$INSTALL" "${install_args[@]}" >/dev/null 2>&1 || echo "polaris update: auch die Wiederherstellung mit install.sh meldet Fehler - bitte $INSTALL von Hand ausfuehren." >&2
  write_state "$behind" "$(version HEAD)" "$(version "$upstream")"
  exit 2
fi

write_state 0 "$(version HEAD)" "$(version HEAD)"
echo "Polaris auf $(version HEAD) aktualisiert (Health-Check: $after)."
exit 0
