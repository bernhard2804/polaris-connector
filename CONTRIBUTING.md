# Beitragen zu Polaris – Anleitung für KI-Agenten (und ihre Menschen)

Diese Datei ist dafür gedacht, sie einem KI-Agenten (Claude, Codex o. Ä.)
zu geben, der auf einem anderen Rechner eine Kopie von Polaris betreut.
Danach soll er wissen, wie das Zusammenspiel mit diesem Repo funktioniert und
wie er Änderungen als Pull Request vorschlägt.

## Das Wichtigste in Kürze

- **Repo:** https://github.com/mstio/polaris-connector, Branch `main`.
  Öffentlich, Lizenz MIT. Betreuer: GitHub-Nutzer `mstio`.
- Das Repo ist ein **Spiegel**: Entwickelt wird in einem privaten Repo.
  Freigegebene Stände landen hier als je **ein** Commit („Polaris <Version>
  (Stand <Datum>)“). Es gibt keine weitere Historie.
- Deine lokale Polaris-Installation gehört dir und darf sich von diesem
  Repo entfernen. **Ausnahme ist die Firefox-Erweiterung (`extension/`).**
  Sie wird nur hier weiterentwickelt und vom Betreuer signiert. Deine
  Installation aktualisiert sie automatisch von hier (siehe unten).
- Verbesserungen, die auch anderen nützen, schickst du als **Pull Request**
  gegen `main`. Bei der Erweiterung ist das der einzige Weg.
- **Aktualisieren:** `polaris update` (ab dem Stand vom 2026-10-23). Es holt
  den neuen Stand per Fast-Forward, führt `install.sh` aus, prüft mit
  `polaris-curator health` und rollt bei Fehlern zurück. Lokale Commits auf
  `main` verhindern das Update: Eigene Änderungen gehören in einen Branch.

## Für den Menschen: Was ist ein Pull Request?

Ein Pull Request (kurz PR) ist ein Änderungsvorschlag auf GitHub. Du
kopierst das Repo in dein eigenes GitHub-Konto („Fork“), änderst dort etwas
und bittest den Betreuer, die Änderung zu übernehmen. Er sieht genau, welche
Zeilen sich ändern, kann nachfragen und entscheidet, ob er übernimmt.
Kaputtmachen kannst du dabei nichts: Am Original ändert sich erst etwas,
wenn der Betreuer zustimmt.

Voraussetzung ist ein kostenloses GitHub-Konto. Der Agent erledigt den Rest
mit der GitHub-CLI `gh`, nachdem du dich einmal mit `gh auth login`
angemeldet hast.

## Die Firefox-Erweiterung (.xpi) – warum sie besonders ist

- Die Erweiterung („Polaris ChatGPT Connector“) hat die feste ID
  `polaris-chatgpt-connector@mstarchy.local`. Signierte, dauerhaft
  installierbare Fassungen kann nur der Betreuer bei Mozilla (AMO, unlisted)
  erzeugen.
- Ab Version 0.4.2 steht im Manifest eine `update_url`
  (`https://raw.githubusercontent.com/mstio/polaris-connector/main/updates.json`).
  Firefox fragt dort etwa einmal am Tag nach und installiert neue Versionen
  selbst. Die `.xpi` hängt jeweils am GitHub-Release `v<Version>`.
- Eine einmalige Handinstallation ist nötig, wenn die installierte Version
  älter als 0.4.2 ist. Einstellungen und Rechte bleiben dabei erhalten
  (gleiche ID). Die neueste Version liegt immer unter
  https://github.com/mstio/polaris-connector/releases/latest. Herunterladen
  und an Firefox übergeben:

  ```bash
  gh release download --repo mstio/polaris-connector --pattern '*.xpi' --dir ~/Downloads --clobber
  firefox ~/Downloads/polaris-chatgpt-connector-*-signed.xpi   # Installation im Firefox-Fenster bestätigen
  ```

  Ohne `gh`: die `.xpi` von der Release-Seite herunterladen und in ein
  Firefox-Fenster ziehen. Den Klick auf „Hinzufügen“ muss der Mensch selbst
  machen. Danach in `about:addons` → Polaris prüfen: Version und unter
  „Berechtigungen“ den Zugriff auf chatgpt.com und claude.ai.
- Lokale Änderungen an `extension/` laufen nur **vorübergehend**
  (`about:debugging#/runtime/this-firefox` → „Temporäres Add-on laden…“ →
  `extension/manifest.json`). Nach einem Firefox-Neustart gilt wieder die
  signierte Version. Dauerhaft wird eine Änderung nur über einen PR und die
  nächste signierte Version.
- In PRs **nie** ändern: `browser_specific_settings.gecko.id`, `update_url`
  und `version` in `extension/manifest.json`. Die Version setzt der Betreuer
  beim Signieren.
- Muss eine neue Version vorerst ferngehalten werden (z. B. weil die eigene
  Bridge noch nicht passt): `about:addons` → Polaris → „Automatische
  Aktualisierung: Aus“.

## Schnittstelle zwischen Erweiterung und deiner Bridge

Weil sich deine Bridge vom Original entfernen darf, die Erweiterung aber von
hier kommt, muss diese Schnittstelle stabil bleiben:

- Native-Messaging-Host `polaris_bridge`, registriert von
  `bridge/install-native-host.sh`.
- Protokoll (`ping`, `export-events`, `export-snapshot`, `list-folders`,
  `map-project`, `capture-status`): Grundzüge im Kopf von
  `bridge/polaris/native_host.py`, alle Nachrichtentypen in `handle()`
  darunter, Gegenstück in `extension/background.js`.
- Event-Format: `bridge/polaris/schema.py` (`schema_version` 1).

Erscheint ein neues Release, prüfe vor allem diese Dateien:
`git diff <alter-Spiegel-Commit> <neuer> -- extension/ bridge/polaris/native_host.py bridge/polaris/schema.py`.
Ändert sich dort etwas, passe die eigene Bridge an. Änderungen an dieser
Schnittstelle selbst nur per PR, und in der Beschreibung ausdrücklich
erwähnen.

## Ablauf eines PRs (für den Agenten)

Einmalig einen Fork mit Klon anlegen. Getrennt von der laufenden Installation
arbeiten: Aus einem Installationspaket liegt diese meist unter
`~/.local/share/polaris/app`.

```bash
gh repo fork mstio/polaris-connector --clone ~/Projects/polaris-connector
cd ~/Projects/polaris-connector
```

Für jeden Vorschlag:

```bash
gh repo sync <dein-github-name>/polaris-connector   # Fork-main auf GitHub vom Original nachziehen
git switch main && git pull                         # lokal holen
git switch -c fix/<kurzes-thema>    # ein Branch je Thema
# ... aendern, testen (siehe unten) ...
git add -A && git commit -m "<Was und warum, kurz, auf Deutsch>"
git push -u origin HEAD
gh pr create --repo mstio/polaris-connector --base main \
  --title "<kurzer Titel>" --body "<Was, warum, wie getestet>"
```

Regeln:

- **Klein und fokussiert:** ein Thema je PR, keine Umformatierung nebenbei.
- **Nie direkt auf `main` des Forks committen.** So bleibt `gh repo sync`
  ein einfaches Vorspulen. Weicht ein offener Branch nach einem neuen
  Spiegel-Commit ab: `git rebase main` und `git push --force-with-lease`.
- In der PR-Beschreibung angeben, was geändert wurde, warum, und wie es
  getestet wurde. Bei `extension/` angeben, ob es temporär in Firefox geladen
  und an echten ChatGPT-/Claude-Seiten geprüft wurde.
- Neue Fälle mit Tests absichern. Die Tests laufen ohne Netz und ohne
  zusätzliche Pakete:

```bash
(cd tests && POLARIS_NO_NOTIFY=1 python3 -m unittest discover)    # Bridge, startet auch die Node-Tests
node --test tests/extension/                                        # nur Erweiterung
(cd curator/tests && POLARIS_NO_NOTIFY=1 python3 -m unittest discover)
```

- Stil wie im umgebenden Code: Kommentare und Meldungen auf Deutsch, im Code
  meist mit ae/oe/ue statt Umlauten. Keine neuen Abhängigkeiten.

## Was nach dem PR passiert

Der Betreuer übernimmt den PR in sein privates Repo (`git am`). Er erscheint
mit dem nächsten Spiegel-Commit in `main`. Auf GitHub wird der PR deshalb
nicht als „Merged“ angezeigt, sondern mit einem Kommentar **geschlossen**.
Das ist normal. Danach den Fork wie oben nachziehen und den Branch löschen.
Betrifft er die Erweiterung, kommt die Änderung mit der nächsten signierten
Version per Auto-Update zurück.

## Öffentliches Repo: keine privaten Daten

Alles hier ist öffentlich. In Code, Tests, Kommentaren, Commit-Nachrichten
und PR-Texten deshalb **keine** echten Namen von Kunden, Projekten oder
Personen, keine Chat-Inhalte, E-Mail-Adressen, Home-Pfade (`/home/<name>`),
Zugangsdaten oder Tokens. In Tests erfundene Namen nutzen (Kunde-A,
Musterfirma …). Vor jedem Push den eigenen Diff darauf durchsehen.

## Typische Anlässe für einen PR

- ChatGPT oder Claude ändern ihre Web-Schnittstelle oder Seite, und die
  Erfassung bricht ab. Siehe README, Abschnitt „Extension nach
  ChatGPT-/Claude-Aenderungen reparieren“ (`extension/chatgpt_api.js`,
  `extension/claude.js`, `extension/extractor.js`).
- Fehler in Bridge oder Curator, die nicht nur die eigene Einrichtung
  betreffen.

Fragen oder Ideen ohne fertigen Code: ein Issue im Repo eröffnen
(`gh issue create --repo mstio/polaris-connector`).
