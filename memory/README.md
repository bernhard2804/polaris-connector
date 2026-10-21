# Maschinengedächtnis: Werkzeug und Vorlage

Das Maschinengedächtnis (`~/.config/ai-agents/`) ist das gemeinsame Gedächtnis aller lokalen Agenten eines Rechners. Dieser Ordner liefert nur das **Werkzeug** und eine **leere Vorlage**, nie Inhalte eines bestehenden Gedächtnisses.

- `tools/memlint.py`: Prüfung (Struktur, Verweise, mem-Tags, Geheimnisse, Agenten-Einstiege, Backup-Abdeckung). Die Quelle ist die Live-Fassung unter `~/.config/ai-agents/tools/memlint.py`. `make-bundle.sh` bricht ab, wenn die beiden voneinander abweichen.
- `template/`: `AGENTS.md` (allgemeine Regeln), `AI_HANDOFF.md` (leerer Index), `entrypoints.conf` (Agenten-Einstiege), `skills/` (gemeinsame Skills aller Agenten, z. B. `loesungsqualitaet`; Quelle ist `~/.config/ai-agents/skills/`, `make-bundle.sh` prüft den Gleichstand).
- `tests/`: Tests für `memlint.py` (`python3 -m unittest discover -s memory/tests`). Sie kommen nicht ins Paket.

`install.sh` legt das Gedächtnis aus der Vorlage an, falls `~/.config/ai-agents` noch nicht existiert. Ein vorhandenes Gedächtnis bleibt unangetastet, nur `tools/memlint.py` und die Skills unter `skills/` werden aktualisiert, alte Fassungen vorher gesichert; `memlint.py --link` verlinkt die Skills zu den eingerichteten Agenten. Ist omarchy-backup eingerichtet, trägt `install.sh` das Gedächtnis dort ein (`paths.d/ai-agents.conf`).
