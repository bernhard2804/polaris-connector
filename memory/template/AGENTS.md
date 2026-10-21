# Gemeinsamer Kontext für lokale AI-Agenten

<!-- Vorlage aus dem Polaris-Installationspaket. An den eigenen Rechner anpassen. -->

## Struktur dieses Wissensbestands
- Diese Datei (`~/.config/ai-agents/AGENTS.md`) ist die alleinige Source of Truth für den gemeinsamen Arbeitsweise-Kontext aller lokalen Agenten, unabhängig vom Anbieter.
- Die Zugriffspfade der Agenten stehen in `~/.config/ai-agents/entrypoints.conf`, eine Zeile je Agent. Jeder Pfad ist ein Symlink auf diese Datei und hat keinen eigenen Inhalt. `python3 ~/.config/ai-agents/tools/memlint.py --link` legt fehlende Einstiege für eingerichtete Agenten an und ersetzt nie eine vorhandene Datei. Kommt ein neuer Agent hinzu, zuerst belegen, welche globale Anweisungsdatei er liest, und sie dann dort eintragen.
- Änderungen immer über einen dieser Pfade oder direkt an dieser Datei vornehmen. Alle Agenten sehen die Änderung dadurch sofort.
- Ergänzend dokumentiert das **Maschinengedächtnis** den laufenden, konkreten Systemzustand dieses Rechners: Hardware, aktive Konfigurationsentscheidungen, offene Punkte. Der Index ist `~/.config/ai-agents/AI_HANDOFF.md`, die Inhalte stehen je Thema unter `~/.config/ai-agents/handoff/*.md`. Die Regeln dazu stehen unten im Abschnitt „Maschinengedächtnis“.
- Tiefe, sicherheitsrelevante Low-Level-Incidents (Boot, Treiber, Suspend/Hibernate usw.) stehen unter `~/.config/ai-agents/system-history/`. Vor Änderungen in diesen Bereichen dort nachlesen.

## Polaris auf diesem Rechner
- Ist Polaris installiert, steht in `handoff/polaris.md` (vom Installer geschrieben), wo Programmordner, Installationsprotokoll und offene Punkte liegen.
- **Vor jeder Reparatur, Neuinstallation oder Änderung an Polaris, omarchy-backup oder dem Gedächtnis-Spiegel** zuerst `docs/AGENTEN-LEITFADEN.md` im Programmordner lesen (Standard: `~/.local/share/polaris/app/docs/AGENTEN-LEITFADEN.md`). Er beschreibt die Prinzipien, den Aufbau und die bekannten Fallstricke.
- Ist eine Installation abgebrochen, ist die Ursache im neuesten `~/.local/state/polaris/install-*.log` zu suchen und zu beheben. Danach `install.sh` erneut ausführen; er ist idempotent.

## System
- Arch Linux mit Omarchy und Hyprland.
- Änderungen sollen sich möglichst an der Omarchy-Architektur orientieren.
- Bestehende Konfiguration vor Änderungen immer zuerst prüfen.

## Arbeitsweise
- Keine Konfigurationsdateien blind ersetzen.
- Änderungen möglichst klein, nachvollziehbar und reversibel halten.
- Vor systemweiten oder sicherheitsrelevanten Änderungen zuerst Ursache und Auswirkungen prüfen.
- Keine unnötigen Abhängigkeiten installieren.
- Vorhandene Backups und bestehende Anpassungen berücksichtigen.
- Nach Änderungen die Funktion praktisch prüfen.
- Bei Unsicherheit zuerst Zustand, Dokumentation und bestehende Konfiguration untersuchen.

## Lösungsqualität: Ziel, Tiefe, Breite
- **Einstufen:** Routine wird direkt erledigt; dazu zählt, was ein eindeutiges Ziel, einen bekannten Weg und eine kleine, umkehrbare Änderung hat. **Systematisch** wird vorgegangen bei Einrichtungs- oder Architekturentscheidungen mit mehreren Wegen, bei Fehlern oder Leistungsproblemen mit unklarer Ursache, bei schwer umkehrbaren oder komponentenübergreifenden Änderungen, bei mehrdeutigen Zielen und bei wiederkehrenden Problemen. Dann gilt das Vorgehen in `~/.config/ai-agents/skills/loesungsqualitaet/SKILL.md`. Es ist als Skill `loesungsqualitaet` verlinkt; Agenten ohne Skills lesen die Datei direkt.
- **Mindestinhalt der Antwort in diesen Fällen:**
  - das vermutete Ziel hinter dem Auftrag, ausdrücklich als Vermutung;
  - ein Befund aus Rohdaten des Systems statt aus Annahmen;
  - mindestens drei ernsthafte Wege, darunter einer mit Bordmitteln oder vorhandener Infrastruktur (z. B. Symlink, XDG-Variable, systemd, vorhandenes Polaris-Werkzeug);
  - genau eine begründete Empfehlung;
  - eine unabhängige Prüfung, also Werkzeug, Messung oder Primärquelle, nicht die eigene Einschätzung;
  - für tragende Aussagen der Status Nachgewiesen, Plausibel, Experimentell oder Unbekannt.
- **Gelerntes wiederverwenden:** Vorher `grep -rn 'type=pattern' ~/.config/ai-agents/knowledge/` nach geprüften Lösungsmustern durchsuchen. Nachweislich bewährte, übertragbare Wege dort ergänzen; nur mit Beleg, memlint prüft das.
- **Grenzen:** Ein höheres Ziel ist ein Vorschlag, kein Auftrag. Ziele, Prioritäten und Risikogrenzen legt der Nutzer fest. Mehr Tiefe heißt nicht mehr Agenten, Dienste oder Abhängigkeiten.

## Verbindliche Dokumentation persistenter Eingriffe
- Jeder Agent muss persistente Änderungen am Rechner noch im selben Arbeitsgang dokumentieren. Eine solche Aufgabe gilt erst dann als abgeschlossen, wenn die Dokumentation geschrieben und gegen den tatsächlichen Zustand geprüft wurde.
- Das gilt insbesondere für:
  - Berechtigungen, Eigentümer und ACLs
  - `sudoers`, Polkit, Gruppen und Benutzerkonten
  - Authentifizierung und die Ablage von Zugangsdaten
  - Firewall und Netzwerkfreigaben
  - systemd-Units und Timer, Mounts, Paketquellen
  - Boot-, Kernel-, Treiber-, Energie- und Geräte-Konfiguration
  - Agentenrechte und andere sicherheitsrelevante Einstellungen
- Der aktuelle, konkrete Maschinenzustand gehört in die passende Themendatei unter `~/.config/ai-agents/handoff/`. Ändert sich ein Status oder entsteht eine neue kritische Regel, gehört das zusätzlich in den Index `AI_HANDOFF.md`. Dauerhafte Regeln für alle Agenten gehören in diese `AGENTS.md`.
- Jeder Eintrag nennt mindestens:
  - Datum, Anlass und Ziel
  - die genau betroffenen Pfade oder Einstellungen
  - den relevanten Zustand vorher und nachher
  - die durchgeführte Prüfung
  - bekannte Risiken oder offene Punkte
  - wie sich der Eingriff zurückbauen oder wiederherstellen lässt

  Geheimnisse, Tokens und Passwörter werden nie dokumentiert.
- Rein lesende Prüfungen und eindeutig flüchtige Tests brauchen keinen Eintrag.
- Kann die Dokumentation nicht geschrieben oder geprüft werden, muss der Agent das dem Nutzer ausdrücklich als offenen Punkt melden.

## Maschinengedächtnis (gemeinsam für alle Agenten)
- Das Maschinengedächtnis besteht aus Markdown-Dateien unter `~/.config/ai-agents/`: diese `AGENTS.md`, der Index `AI_HANDOFF.md` und die Ordner `handoff/`, `knowledge/`, `history/` und `system-history/`. Dazu kommt das Prüfwerkzeug `tools/memlint.py`. Alle lokalen Agenten lesen und schreiben es gleichermaßen.
- **Menschenlesbarkeit:** Die Markdown-Dateien sind die einzige kanonische Quelle. Sie müssen mit einem normalen Editor vollständig lesbar bleiben. Datenbanken, Indizes oder MCP-Server sind höchstens wegwerfbare Zugriffsschichten darüber.
- **Drei Schichten pro Thema** (gleicher Dateiname):
  - `handoff/<thema>.md`: nur der **aktuelle** Stand, Leitplanken, offene Punkte und Verweise (Richtgröße 3–8 KB)
  - `knowledge/<thema>.md`: dauerhaftes Wissen mit Status
  - `history/<thema>.md`: abgeschlossene Episoden, wörtlich

  **Der Handoff ist kein Archiv.**
- **Kennzeichnung (mem-Tag):** Jeder `##`-Abschnitt in `knowledge/`, `history/` und `handoff/` trägt direkt unter der Überschrift genau eine Zeile `<!-- mem: id=<eindeutige-id> type=<current_state|architecture|decision|gotcha|procedure|open_item|incident|history|pattern> status=<current|superseded|deprecated|historical|done> date=YYYY-MM-DD source=<user|agent-name> ref=<…> [supersedes=<id>] [superseded_by=<id>] -->`.
- **Überholtes nicht überschreiben, sondern ablösen:**
  - Der alte Abschnitt bleibt stehen. Er bekommt `status=superseded superseded_by=<neue-id>`, eine Überschrift mit „Veraltet: …“ und direkt darunter die Zeile „**Ersetzt durch:** …“.
  - Der neue Abschnitt trägt `supersedes=<alte-id>`.
- **Prüfen:** Nach jeder Änderung am Gedächtnis `python3 ~/.config/ai-agents/tools/memlint.py` ausführen. Exit 0 bedeutet: keine Fehler.
- **Lesen:** Vor jeder Aufgabe, die den Rechner verändert, zuerst den Index `AI_HANDOFF.md` vollständig lesen und seine kritischen Regeln befolgen. Danach die passende Themendatei lesen.
- **Schreiben:**
  - Den aktuellen Stand im Handoff aktualisieren.
  - Dauerhaftes Wissen nach `knowledge/` schreiben.
  - Die Episode nach `history/` schreiben.
  - Passt kein Thema, eine neue Themendatei anlegen und im Index eintragen.
- **Zeitpunkt:** Direkt nach jedem abgeschlossenen persistenten Einzeleingriff dokumentieren, nicht gesammelt am Ende einer längeren Serie.
- **Nachweis gegenüber dem Nutzer:** Jede Abschlussmeldung zu einem persistenten Eingriff nennt die Dateien des Maschinengedächtnisses, in die dokumentiert wurde.
- **Sicherung:** Ist omarchy-backup eingerichtet, sichert es `~/.config/ai-agents/`.
  - Gemeinsames Wissen deshalb nie außerhalb dieses Verzeichnisses ablegen.
  - Dateien direkt dort bearbeiten, nicht über Symlinks mit `sed -i`.
  - Keine Dateien mit Endung `.sqlite` oder `.log` anlegen und keine mit `cache`, `token`, `secret` oder `credentials` im Namen. Solche Dateien schließt das Backup aus.

## Projektarbeit
- Vor größeren Änderungen vorhandene README-, AGENTS.md- und Projektdokumentation lesen.
- Vor Änderungen `git status` und vorhandene Diffs prüfen.
- Bestehende Benutzeränderungen, die nicht zur aktuellen Aufgabe gehören, nicht überschreiben.

## Kritische Infrastruktur: omarchy-backup
- omarchy-backup (CLI `omarchy-backup`) friert einen funktionierenden Omarchy-Zustand ein und stellt ihn auf einer frischen Installation wieder her.
- Nach größeren Omarchy-Updates prüfen, ob Snapshot, Baseline-Erkennung (`omarchy-backup status`) und Remote-Sicherung (`omarchy-backup doctor`) weiter funktionieren. Auffälligkeiten dem Nutzer melden.
