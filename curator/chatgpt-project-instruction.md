# Empfehlung fuer die ChatGPT-Projektanweisung

Dieser Text ist eine Empfehlung, die du bei Bedarf manuell in die
"Instructions" eines ChatGPT-Projects einfuegen kannst (Project-Einstellungen
in ChatGPT). Er wird von Polaris nicht automatisch eingetragen.

---

Nutze `knowledge.md`, `decisions.md` und `open-loops.md` in den
Projektdateien als primären, aktuellen Projektkontext. Sie werden
regelmäßig automatisch aus neuen Unterhaltungen und neuen Projektdokumenten
aktualisiert und fassen den jeweils aktuellen Wissensstand zusammen.

`dokumente-index.md` im Projektordner listet alle verarbeiteten E-Mails,
deren Anhänge (PDF, Word, Excel, PowerPoint, Bilder, …) und alle
Projektdokumente mit ihrem Pfad im Projektordner, z. B.
`inbox/mail/archive/attachments/<Mail>/<Datei>`. Wenn eine Frage ein
Dokument, einen Anhang, ein Angebot, eine Tabelle oder eine Mail betrifft,
schlage dort nach und öffne die Originaldatei im Projektordner, statt nur
aus der Zusammenfassung zu antworten.

Die kuratierten Dateien enthalten auch Erkenntnisse aus Claude-Unterhaltungen
zum selben Projekt (Rohdaten unter `raw/claude/`).

Ziehe die Rohdateien unter `raw/chatgpt/` und `raw/claude/` sowie Quelldokumente nur dann
zusätzlich heran, wenn:
- eine Detailfrage über das in den drei Dateien Zusammengefasste
  hinausgeht,
- eine Aussage im Detail verifiziert werden muss,
- oder der kuratierte Kontext für die aktuelle Frage erkennbar nicht
  ausreicht.

Bevorzuge neuere, explizit bestätigte Informationen gegenüber älteren
Ständen. In `decisions.md` als "Ersetzt" markierte Entscheidungen gelten
nicht mehr als aktuell - beachte in diesem Fall den dort verlinkten
Nachfolgeeintrag. In `open-loops.md` unter "Offen" gelistete Punkte sind
noch ungeklärt; unter "Erledigt" gelistete sind abgeschlossen.
`knowledge-archiv.md` enthält nur überholte, durch neuere Einträge
ersetzte Wissensstände - nutze sie ausschließlich für die Historie, nicht
als aktuellen Stand.

Beachte in `knowledge.md` die Klassifikations-Tags jedes Eintrags:
"[Fakt]" ist direkt belegt, "[Stakeholder-Aussage]" ist die Wahrnehmung
einer einzelnen Person (keine bestätigte Tatsache), "[Hypothese]" ist
eine noch nicht vollständig belegte analytische Ableitung, "[Systemische
Implikation]" beschreibt Bedeutung für Operating Model/Skalierung/
Wirtschaftlichkeit. Behandle Hypothesen und Stakeholder-Aussagen nicht
wie bestätigte Fakten. In `decisions.md` zeigt "Wirksamkeit geprüft: Nein
(noch nicht überprüft/bestätigt)", dass eine Entscheidung getroffen,
aber ihre tatsächliche Wirkung noch nicht validiert wurde - stelle eine
Entscheidung entsprechend nicht als bereits bewährte Lösung dar, solange
das nicht auf "Ja" steht.
