---
name: loesungsqualitaet
description: Vorgehen für Aufgaben, bei denen der naheliegende Weg nicht automatisch der beste ist – Einrichtungs- oder Architekturentscheidungen mit mehreren Wegen („wie richte ich X am besten ein“, „was sollen wir tun“), Fehler oder Leistungsprobleme mit unklarer Ursache, schwer umkehrbare oder mehrere Komponenten berührende Änderungen, mehrdeutige Ziele, wiederkehrende Probleme. Liefert Zielklärung, Befund aus Rohdaten, echte Alternativen samt Bordmittel-Lösung, eine Empfehlung, unabhängige Prüfung und Evidenzstatus. Nicht für Routineaufgaben mit klarem, kleinem, umkehrbarem Weg.
---

# Lösungsqualität: Ziel, Tiefe, Breite, Prüfung

Anbieterneutrale Arbeitsanweisung für alle lokalen Agenten (Quelle: `~/.config/ai-agents/skills/loesungsqualitaet/SKILL.md`; Agenten ohne Skill-Unterstützung lesen diese Datei direkt). Grundlage: Felix Rieseberg, „How to be more ambitious: A tutorial for humans with models“ (13.10.2026) – Altitude, Depth, Range; Speed bleibt der Normalfall. Die Regeln in `AGENTS.md` (Freigaben, Ressourcen, Dokumentation) gelten unverändert und haben Vorrang.

## 0. Einstufen – Speed schützen

Direkt erledigen, wenn alles zutrifft: Ziel eindeutig, Weg bekannt, Änderung klein und umkehrbar, keine anderen Komponenten berührt. Sonst dieses Vorgehen, im Umfang passend zur Tragweite: Bei einer kleinen Entscheidung genügen die Schritte 1–4 in wenigen Zeilen. Keine zusätzlichen Agenten nur „für mehr Tiefe“. Ein zweiter Blick lohnt nur, wenn er andere Informationen oder Werkzeuge hat (Schritt 5).

## 1. Altitude – was soll danach anders sein?

- Den Auftrag in Verben statt Substantiven umformulieren: nicht „eine zweite Version installieren“, sondern „neue Funktionen ausprobieren, ohne den laufenden Arbeitsablauf zu gefährden und ohne Daten doppelt zu pflegen“.
- Fragen: Was soll nach der Erledigung passieren? Was kommt als Nächstes? Was müsste sich ändern, damit das Problem dauerhaft nicht wiederkommt?
- Das vermutete Ziel ausdrücklich als Vermutung kennzeichnen. Ein höheres Ziel ist ein Vorschlag, kein Auftrag. Ziele, Prioritäten und Risikogrenzen legt der Nutzer fest.
- Gedächtnis zuerst: `grep -rn 'type=pattern' ~/.config/ai-agents/knowledge/` (geprüfte Lösungsmuster) und die passende Themendatei laut Index. Gibt es dort schon eine Entscheidung, ein Muster oder einen Fallstrick?

## 2. Depth – arbeiten wie Fachleute, nicht reden wie sie

Vor dem Lösen knapp für dich beantworten:
1. Was würde eine Spitzenfachkraft über den wörtlichen Auftrag hinaus tun?
2. Was ist das roheste Ausgangsmaterial? Zum Beispiel Logs, Quelltext, Messwerte, die tatsächliche Konfiguration, Datenformate auf der Platte, Primärdoku, Release-Notes oder Issues.
3. Welche Methoden und Werkzeuge des Fachs passen? Zum Beispiel Messung vor Hypothese, Reproduktion, Bisektion, Profiler oder Tracing, Schema- bzw. Format-Diff.
4. Was außer mir selbst zeigt, dass das Ergebnis stimmt?

Dann genau das tun:
- Annahmen an Rohdaten prüfen, nicht an Erinnerung. Versionen, Pfade, Formate und laufende Prozesse am System nachsehen.
- Hypothese → Messung → Änderung → Nachmessung. Ohne Nachmessung gibt es kein „behoben“.
- Die Ursache bis zum Hebel verfolgen. Nicht beim Prozess- oder Komponentennamen stehen bleiben, sondern bis zur konkreten Einstellung, Codezeile, Abfragefrequenz oder Datei, die man ändern kann. Beispiel: nicht „Dienst X braucht CPU“, sondern „wer ruft ihn wie oft womit auf, und wo ist das eingestellt“. Die Methode heißt „Fünf Warum“ (Qualitätswesen).
- Ressourcenschonend messen: sysfs/proc lesen, kurze Messfenster, eine schlafende GPU nicht wecken, Cloud-Laufwerke nicht im Minutentakt lesen.
- Kein Rollenspiel („Du bist ein Experte …“). Es zählt die Methode, nicht der Tonfall.

## 3. Range – Methoden aus anderen Feldern

- Das Problem als Funktionen beschreiben und je Funktion fragen, welches Fach sie am besten gelöst hat. Beispiele:
  - Gemeinsame Daten mehrerer Programmversionen → Dateisystem und Datenhaltung. Zuerst die Daten nach ihrem Änderungsverhalten einteilen:
    - Unveränderliche oder nur gelesene Bestände (z. B. importierte Vorlagen, Bibliotheken, Medien) werden geteilt, per Symlink oder gemeinsamem Ordner.
    - Versionsabhängige oder vom Programm beschriebene Dateien (Einstellungen, Datenbanken, Formate mit Schema) werden getrennt bzw. kopiert, über XDG- oder Profilvariablen.
    So entsteht keine doppelte Pflege und kein Risiko für die stabile Version. Keine eigene Synchronisation bauen.
  - Wachsender Wissens- oder Datenbestand → Datenbanktechnik (stufenweise Kompaktierung, nur geänderte Teile neu verarbeiten) und Archivwesen (Aufbewahrungsregeln).
  - Unsichere Ursache → Versuchsplanung: eine Variable je Lauf, Vorher/Nachher unter gleichen Bedingungen.
  - Betrieb einer Änderung → Reliability Engineering: zuerst im Kleinen einführen, Rückbaupfad, beobachtbares Erfolgskriterium.
- Bewährte Verfahren übernehmen, keine neuen Abhängigkeiten (Konzepte, nicht Technik).

## 4. Alternativen und eine Empfehlung

- Mindestens drei ernsthafte Wege:
  - (a) minimalinvasiv mit Bordmitteln oder vorhandener Infrastruktur,
  - (b) der erbetene oder naheliegende Weg,
  - (c) ein grundsätzlich anderer oder unkonventioneller Weg.
  Unkonventionelles zulassen, dann hart prüfen.
- Bewerten nach: Nutzen für das Ziel aus Schritt 1, tatsächlichem Aufwand, neuen Abhängigkeiten, Wartung und Verhalten bei Updates, Rückbaubarkeit, Wirkung auf andere Teile (Backup, Gedächtnis, Polaris, Omarchy-Updates), Sicherheit und Ressourcenverbrauch.
- Genau eine Empfehlung mit Begründung. Dazu sagen, was die Empfehlung kippen würde. Keine Sammlung gleichwertiger Optionen.

## 5. Unabhängig prüfen

Prüfer in dieser Rangfolge, der erste passende wiegt am meisten:
1. Werkzeuge, die das Ergebnis selbst beurteilen: Tests, Schema- und Konsistenzprüfungen (`memlint.py`, `polaris-curator health`, `omarchy-backup doctor`), Compiler, Linter, `--dry-run`.
2. Eine reale Messung oder ein kontrollierter Testlauf (Vorher/Nachher unter gleichen Bedingungen, in einer Kopie oder einem leeren `HOME`).
3. Die Primärquelle (Quelltext, offizielle Doku, Changelog) statt einer Zusammenfassung.
4. Ein zweites Modell nur, wenn es von einem anderen Anbieter kommt oder andere Informationen hat, die eigene Begründung nicht kennt und nach einem festen Raster bewertet.

Dieselbe KI, die ihre Lösung „noch einmal prüft“, ist kein Prüfer. Was sich nicht prüfen lässt, als offen benennen.

## 6. Evidenzstatus

Tragende Aussagen bekommen einen Status:
- **Nachgewiesen**: geprüft, Beleg genannt.
- **Plausibel**: begründet, aber nicht geprüft.
- **Experimentell**: bewusst ausprobiert, Ausgang offen.
- **Unbekannt**.

Erfolg nur für „Nachgewiesen“ melden.

## 7. Antwortform

Die Antwort enthält knapp und in dieser Reihenfolge, was zutrifft:
- **Ziel:** Auftrag und vermutetes Ziel (als Vermutung).
- **Befund:** was gelesen oder gemessen wurde, mit Quelle.
- **Wege:** mindestens drei mit Bewertung, dann die Empfehlung mit Begründung.
- **Prüfung:** wie geprüft bzw. wie prüfbar, mit Evidenzstatus.
- **Folgen:** Wartung, Updates, Rückbau.

Vor riskanten, irreversiblen oder nach außen wirkenden Schritten die Freigabe einholen (AGENTS.md).

## 8. Gelerntes festhalten

- Hat sich ein Weg nachweislich bewährt und nützt er über das Thema hinaus, kommt er als Abschnitt mit `type=pattern` in `~/.config/ai-agents/knowledge/loesungsmuster.md`. Inhalt: Problemform, Muster, Grenzen und Beleg (`ref=history:<id>`, `commit:` oder `url:`). memlint lehnt Muster ohne solchen Beleg ab.
- Hypothesen und unbestätigte Ideen sind keine Muster, höchstens ein offener Punkt im Handoff.
- Projekt- und Kundenwissen kuratiert weiterhin der Polaris-Curator (`knowledge.md`, `decisions.md` je Projekt). Das Musterverzeichnis gilt für Arbeitsweise und Technik.
