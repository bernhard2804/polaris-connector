"""Konfiguration des Curators: global (~/.config/polaris/curator.yaml) plus
optionaler projektspezifischer Override (<Project>/.polaris/curator-config.yaml).

Modell-IDs sind bewusst nirgends im Code hart codiert - nur hier als
Default-Werte, die der Nutzer jederzeit ueberschreiben kann.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

HOME = Path.home()
GLOBAL_CONFIG_DIR = HOME / ".config" / "polaris"
GLOBAL_CONFIG_FILE = GLOBAL_CONFIG_DIR / "curator.yaml"
PROJECTS_FILE = GLOBAL_CONFIG_DIR / "projects.yaml"

# Am 2026-09-12 aus `codex exec` / ~/.codex/models_cache.json ermittelt:
# gpt-5.6-luna ist dort explizit als "Fast and affordable agentic coding
# model" gelistet - passend fuer eine reine Extraktions-/Merge-Aufgabe
# ohne tiefes Reasoning. gpt-5.6-sol war zum selben Zeitpunkt das globale
# Codex-Standardmodell des Nutzers (~/.codex/config.toml) und dient hier
# als Fallback. Bei Bedarf mit `codex features` / dem aktuellen
# Modellkatalog neu abgleichen - diese IDs veralten mit der Zeit.
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_FALLBACK_MODEL = "gpt-5.6-sol"

DEFAULTS: dict[str, Any] = {
    "provider": "openai",
    "runtime": "codex",
    "model": DEFAULT_MODEL,
    "fallback_model": DEFAULT_FALLBACK_MODEL,
    "permit_more_expensive_fallback": False,
    "llm_timeout_seconds": 180,
    "reasoning_effort": None,
    "batching": {
        "min_messages_for_early_run": 10,
        "min_chars_for_early_run": 8000,
    },
    "budget": {
        "max_batches_per_run": 3,
        "max_llm_calls_per_run": 3,
        "max_messages_per_batch": 40,
        "max_input_chars_per_batch": 50000,
        "max_total_input_chars_per_run": 120000,
    },
    "context": {
        "max_curated_context_chars": 50000,
        # Bis zu dieser Groesse (ohne Herkunftskommentare) geht eine Datei
        # vollstaendig mit, darueber Verzeichnis + Volltext des Relevanten
        # (context.py, seit 2026-09-30). 0 = immer vollstaendig.
        "full_context_chars": 80000,
        # Hoechstens dieser Anteil von full_context_chars fuer das Verzeichnis
        # (seit 2026-10-15); 0 = alle Eintraege im Verzeichnis.
        "index_cap": 0.25,
    },
    "monitoring": {
        "max_days_without_chatgpt": 3,
        "max_days_without_claude": 7,
    },
    "compaction": {
        "threshold_chars": 100000,
        "min_interval_days": 7,
    },
    "attachments": {
        "min_image_bytes": 300000,
        "max_images_per_document": 5,
        "max_chars_per_attachment": 20000,
    },
}


@dataclass
class CuratorConfig:
    provider: str = "openai"
    runtime: str = "codex"
    model: str = DEFAULT_MODEL
    fallback_model: str | None = DEFAULT_FALLBACK_MODEL
    # Letzte Stufe, wenn Codex nicht erreichbar ist (Nutzungslimit, Ausfall):
    # Claude ueber die installierte Claude-Code-CLI (seit 2026-09-30, vom
    # Nutzer freigegeben). None = aus. fallback_model laeuft ueber dasselbe
    # Codex-Konto und hilft bei einem Nutzungslimit nicht.
    claude_fallback_model: str | None = None
    # Unabhaengige Pruefung neuer Eintraege durch Claude (review.py, seit
    # 2026-09-30). None = aus.
    reviewer_model: str | None = None
    permit_more_expensive_fallback: bool = False
    llm_timeout_seconds: int = 180
    # Reasoning-Level fuer codex exec (-c model_reasoning_effort=...), z.B.
    # "low"/"medium"/"high". None = nicht setzen. Achtung: wegen
    # --ignore-user-config greift sonst weder die Nutzer-Konfiguration noch
    # ein Katalog-Default - Codex meldet dann "reasoning effort: none".
    reasoning_effort: str | None = None
    min_messages_for_early_run: int = 10
    min_chars_for_early_run: int = 8000
    max_batches_per_run: int = 3
    max_llm_calls_per_run: int = 3
    max_messages_per_batch: int = 40
    max_input_chars_per_batch: int = 50000
    max_total_input_chars_per_run: int = 120000
    # Groessere Dokumente werden in Teile dieser Groesse zerlegt und Teil fuer
    # Teil kuratiert (je Teil ein normaler Aufruf, Budget pro Lauf gilt
    # weiter). Darueber hinaus wird gekuerzt und gemeldet. 1 = nie teilen.
    max_parts_per_document: int = 5
    # Bis zu diesem Vielfachen des Limits geht ein Dokument ausnahmsweise in
    # EINEM Aufruf durch (vorübergehend erhoehtes Limit): billiger als Teile,
    # weil das Projektwissen nur einmal mitgeschickt wird. Darueber: Teile.
    document_oversize_factor: float = 2.0
    max_curated_context_chars: int = 50000
    full_context_chars: int = 80000
    index_cap: float = 0.25
    # Mail-Anhaenge: kleinere Bilder gelten als Signatur/Logo und werden
    # ignoriert; groessere gehen als Bild an das Modell (codex exec --image).
    min_image_bytes: int = 300000
    max_images_per_document: int = 5
    max_chars_per_attachment: int = 20000
    # Warnung, wenn projektuebergreifend so viele Tage keine ChatGPT-
    # Nachricht archiviert wurde (siehe health.py).
    max_days_without_chatgpt: int = 3
    max_days_without_claude: int = 7
    # Verdichtung von knowledge.md (compact.py): automatisch ab dieser Groesse,
    # hoechstens alle min_interval_days Tage. 0 = automatisch nie.
    compact_threshold_chars: int = 100000
    compact_min_interval_days: int = 7
    loops_review_interval_days: int = 7  # 0 = offene Punkte nie automatisch pruefen
    # Ergebnislose Laeufe verlaengern den Abstand von Verdichtung und Pruefung
    # offener Punkte (7 -> 14 -> 28 Tage); 1 = immer der Grundabstand (seit 2026-10-17).
    idle_backoff_max: int = 4
    # runtime "command" (seit 2026-10-07): beliebige Agenten-CLI als Argument-
    # liste mit Platzhaltern {model} {prompt_file} {schema_file} {schema_json};
    # ohne {prompt_file} kommt der Prompt ueber stdin. command_output_key: Feld
    # einer JSON-Huelle mit der eigentlichen Antwort (z. B. "response").
    command: list[str] | None = None
    command_output_key: str | None = None

    def merged_with(self, override: dict[str, Any]) -> "CuratorConfig":
        cfg = replace(self)
        if "provider" in override:
            cfg.provider = override["provider"]
        if "runtime" in override:
            cfg.runtime = override["runtime"]
        if "model" in override:
            cfg.model = override["model"]
        if "fallback_model" in override:
            cfg.fallback_model = override["fallback_model"]
        if "claude_fallback_model" in override:
            cfg.claude_fallback_model = override["claude_fallback_model"] or None
        if "reviewer_model" in override:
            cfg.reviewer_model = override["reviewer_model"] or None
        if "permit_more_expensive_fallback" in override:
            cfg.permit_more_expensive_fallback = bool(override["permit_more_expensive_fallback"])
        if "command" in override:
            value = override["command"]
            if value is not None and not (isinstance(value, list) and value
                                          and all(isinstance(a, str) for a in value)):
                raise ValueError("curator.yaml: command muss eine Liste von Strings sein")
            cfg.command = list(value) if value else None
        if "command_output_key" in override:
            cfg.command_output_key = override["command_output_key"] or None
        if "llm_timeout_seconds" in override:
            cfg.llm_timeout_seconds = int(override["llm_timeout_seconds"])
        if "reasoning_effort" in override:
            value = override["reasoning_effort"]
            cfg.reasoning_effort = str(value) if value else None
        b = override.get("batching", {}) or {}
        if "min_messages_for_early_run" in b:
            cfg.min_messages_for_early_run = int(b["min_messages_for_early_run"])
        if "min_chars_for_early_run" in b:
            cfg.min_chars_for_early_run = int(b["min_chars_for_early_run"])
        bud = override.get("budget", {}) or {}
        if "max_batches_per_run" in bud:
            cfg.max_batches_per_run = int(bud["max_batches_per_run"])
        if "max_llm_calls_per_run" in bud:
            cfg.max_llm_calls_per_run = int(bud["max_llm_calls_per_run"])
        if "max_messages_per_batch" in bud:
            cfg.max_messages_per_batch = int(bud["max_messages_per_batch"])
        if "max_input_chars_per_batch" in bud:
            cfg.max_input_chars_per_batch = int(bud["max_input_chars_per_batch"])
        if "max_total_input_chars_per_run" in bud:
            cfg.max_total_input_chars_per_run = int(bud["max_total_input_chars_per_run"])
        if "document_oversize_factor" in bud:
            cfg.document_oversize_factor = max(1.0, float(bud["document_oversize_factor"]))
        if "max_parts_per_document" in bud:
            cfg.max_parts_per_document = max(1, int(bud["max_parts_per_document"]))
        ctx = override.get("context", {}) or {}
        if "max_curated_context_chars" in ctx:
            cfg.max_curated_context_chars = int(ctx["max_curated_context_chars"])
        if "full_context_chars" in ctx:
            cfg.full_context_chars = int(ctx["full_context_chars"])
        if "index_cap" in ctx:
            cfg.index_cap = max(0.0, min(0.9, float(ctx["index_cap"])))
        mon = override.get("monitoring", {}) or {}
        if "max_days_without_chatgpt" in mon:
            cfg.max_days_without_chatgpt = int(mon["max_days_without_chatgpt"])
        if "max_days_without_claude" in mon:
            cfg.max_days_without_claude = int(mon["max_days_without_claude"])
        comp = override.get("compaction", {}) or {}
        if "threshold_chars" in comp:
            cfg.compact_threshold_chars = int(comp["threshold_chars"])
        if "min_interval_days" in comp:
            cfg.compact_min_interval_days = int(comp["min_interval_days"])
        if "loops_review_interval_days" in comp:
            cfg.loops_review_interval_days = int(comp["loops_review_interval_days"])
        if "idle_backoff_max" in comp:
            cfg.idle_backoff_max = max(1, int(comp["idle_backoff_max"]))
        att = override.get("attachments", {}) or {}
        if "min_image_bytes" in att:
            cfg.min_image_bytes = int(att["min_image_bytes"])
        if "max_images_per_document" in att:
            cfg.max_images_per_document = int(att["max_images_per_document"])
        if "max_chars_per_attachment" in att:
            cfg.max_chars_per_attachment = int(att["max_chars_per_attachment"])
        return cfg


def _default_config() -> CuratorConfig:
    return CuratorConfig()


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def ensure_global_config() -> None:
    GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if GLOBAL_CONFIG_FILE.exists():
        return
    tmp = GLOBAL_CONFIG_FILE.with_suffix(".yaml.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        yaml.safe_dump(DEFAULTS, f, allow_unicode=True, sort_keys=False)
    tmp.replace(GLOBAL_CONFIG_FILE)


def load_config(project_path: Path | None = None) -> CuratorConfig:
    """Laedt die globale Konfiguration und wendet, falls vorhanden, einen
    projektspezifischen Override aus <Project>/.polaris/curator-config.yaml
    an."""
    ensure_global_config()
    cfg = _default_config().merged_with(_read_yaml(GLOBAL_CONFIG_FILE))
    if project_path is not None:
        project_override = project_path / ".polaris" / "curator-config.yaml"
        cfg = cfg.merged_with(_read_yaml(project_override))
    return cfg


def list_known_projects() -> dict[str, Path]:
    """Liest die Bridge-Projekt-Mappings (~/.config/polaris/projects.yaml)
    direkt, ohne das Bridge-Paket zu importieren - haelt Curator und
    Bridge lose gekoppelt. Liefert {project_id: local_path} nur fuer
    Mappings mit status "ok" und tatsaechlich existierendem Ordner."""
    raw = _read_yaml(PROJECTS_FILE)
    result: dict[str, Path] = {}
    seen: set[Path] = set()
    # Mehrere Zuordnungen koennen auf denselben Ordner zeigen (z. B. ChatGPT
    # ChatGPT "Firma" + Claude "COO" + Claude "Verwaltungen" -> Firma). Der
    # Curator arbeitet pro ORDNER: jeder Ordner genau einmal, ChatGPT-ID
    # bevorzugt als Vertreter (2026-09-25).
    items = sorted((raw.get("projects") or {}).items(), key=lambda kv: (kv[0].startswith("claude-p-"), kv[0]))
    for pid, entry in items:
        if entry.get("status") != "ok":
            continue
        local_path = Path(entry.get("local_path", ""))
        if not local_path.is_dir():
            continue
        key = local_path.resolve()
        if key in seen:
            continue
        seen.add(key)
        result[pid] = local_path
    return result


def count_mapped_projects() -> int:
    """Anzahl der Bridge-Mappings mit status "ok" - unabhaengig davon, ob der
    Ordner gerade erreichbar ist. Liefert list_known_projects() nichts, obwohl
    Mappings existieren, ist typischerweise das Projekt-Laufwerk (rclone-Mount)
    noch nicht eingehaengt."""
    raw = _read_yaml(PROJECTS_FILE)
    return sum(1 for entry in (raw.get("projects") or {}).values() if entry.get("status") == "ok")


def find_project_path(name_or_path: str) -> Path | None:
    """Sucht einen bekannten lokalen Projektpfad ueber seinen Ordnernamen
    oder nimmt einen direkt angegebenen, existierenden Pfad."""
    direct = Path(name_or_path).expanduser()
    if direct.is_dir():
        return direct
    for path in list_known_projects().values():
        if path.name == name_or_path:
            return path
    return None
