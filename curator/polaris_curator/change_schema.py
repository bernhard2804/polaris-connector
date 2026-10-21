"""Strukturiertes Aenderungsobjekt, das Codex zurueckgeben muss (siehe
Aufgabenstellung Abschnitt 22). Codex entscheidet inhaltlich; diese lokale
Validierung entscheidet, ob dem Ergebnis vertraut und gemerged wird.

v0.2: Aus der Analyse eines echten Testlaufs (RPS/Schkeuditz, 2026-09-14)
hat sich gezeigt, dass ein einzelnes freies "is_assumption"-Flag zu grob
ist, um Beobachtung/Fakt, Stakeholder-Aussage, Hypothese und systemische
Implikation sauber zu trennen, und dass ohne Pflichtfelder fuer
Evidenzgrad/Scope/Confidence zu frueh abstrahiert und generalisiert wird.
Das Schema erzwingt jetzt genau diese Klassifikation PRO EINTRAG als
strukturierte Zwischenrepraesentation (Abschnitt 10 der Aufgabenstellung) -
die Formulierung der finalen Markdown-Zeile (merge.py) ist davon getrennt.

Zusaetzlich erzwingt "self_review" ein Pflichtfeld-Set, das das Modell
zwingt, die Kritik-Checkliste (Abschnitt 11) explizit vor der Antwort
durchzugehen, statt sie implizit zu ueberspringen. Das ist eine
Selbstauskunft, kein unabhaengiger zweiter Blick - siehe README
"Bekannte Grenzen".

Kein generisches JSON-Schema-Package als Abhaengigkeit - das Schema ist
klein und fest, ein handgeschriebener Validator reicht und bleibt
nachvollziehbar.
"""

from __future__ import annotations

from typing import Any

ENTRY_TYPES = ["fact", "stakeholder_claim", "hypothesis", "implication"]
SCOPES = ["local", "functional", "cross_functional", "company_wide"]
EVIDENCE_STRENGTHS = ["single_source", "multiple_sources", "corroborated"]
CONFIDENCES = ["low", "medium", "high"]
DECISION_SCOPES = ["local", "provisional", "pilot", "permanent", "company_wide"]
LOOP_TYPES = [
    "data_need",
    "leadership_question",
    "decision_validation",
    "process_gap",
    "ownership_gap",
    "economic_impact",
    "other",
]

# Reihenfolge = Reihenfolge der Kritik-Checkliste in der Aufgabenstellung
# (Abschnitt 11). "true" heisst jeweils: die Pruefung wurde durchgefuehrt
# UND ein dabei gefundenes Problem wurde vor der Antwort bereits behoben -
# nicht "es gibt hier kein Problem, ungeprueft".
SELF_REVIEW_KEYS = [
    "stakeholder_claims_marked_not_as_fact",
    "no_overgeneralization_from_single_case",
    "interventions_not_presented_as_proven",
    "systemic_implications_considered",
    "key_person_dependencies_considered",
    "customer_or_economic_impact_considered",
    "leadership_as_permanent_substitute_flagged",
    "entries_are_long_term_relevant_not_redundant",
    "claims_do_not_exceed_source_evidence",
]

# An codex exec --output-schema uebergeben, um die Modellausgabe bereits
# bei der Generierung auf diese Form zu zwingen. OpenAIs Strict-Mode
# verlangt bei additionalProperties=False JEDES Property auch in
# "required" - echte Optionalitaet wird ueber nullable Typen ausgedrueckt,
# nie durch Weglassen aus "required" (siehe Projekt-Memory, am ersten
# echten codex-exec-Aufruf so gefunden).
JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "knowledge_updates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "content": {"type": "string"},
                    "entry_type": {"type": "string", "enum": ENTRY_TYPES},
                    "scope": {"type": "string", "enum": SCOPES},
                    "evidence_strength": {"type": "string", "enum": EVIDENCE_STRENGTHS},
                    "confidence": {"type": "string", "enum": CONFIDENCES},
                    "mode": {"type": "string", "enum": ["append", "replace"]},
                    # Nummer des zu ersetzenden Eintrags (z. B. "K12") statt
                    # Textmarke (seit 2026-09-29)
                    "replaces_id": {"type": ["string", "null"]},
                    # Herkunft je Aussage: message_ids aus dem Quellinhalt
                    "source_message_ids": {"type": "array", "items": {"type": "string"}},
                    "related_existing_insight": {"type": ["string", "null"]},
                    "economic_relevance": {"type": ["string", "null"]},
                    "operating_model_relevance": {"type": ["string", "null"]},
                    "key_person_dependency": {"type": ["string", "null"]},
                    "causal_chain": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                    },
                },
                "required": [
                    "section",
                    "content",
                    "entry_type",
                    "scope",
                    "evidence_strength",
                    "confidence",
                    "mode",
                    "replaces_id",
                    "source_message_ids",
                    "related_existing_insight",
                    "economic_relevance",
                    "operating_model_relevance",
                    "key_person_dependency",
                    "causal_chain",
                ],
                "additionalProperties": False,
            },
        },
        "decisions_add": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "decision": {"type": "string"},
                    "rationale": {"type": "string"},
                    "date": {"type": "string"},
                    "decision_scope": {"type": "string", "enum": DECISION_SCOPES},
                    "validated": {"type": "boolean"},
                    "validation_note": {"type": ["string", "null"]},
                    "source_message_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "title",
                    "decision",
                    "rationale",
                    "date",
                    "decision_scope",
                    "validated",
                    "validation_note",
                    "source_message_ids",
                ],
                "additionalProperties": False,
            },
        },
        "decisions_update": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "decision_id": {"type": "string"},
                    "new_status": {"type": ["string", "null"]},
                    "replaced_by": {"type": ["string", "null"]},
                    "note": {"type": ["string", "null"]},
                    "validated": {"type": ["boolean", "null"]},
                    "validation_note": {"type": ["string", "null"]},
                    "source_message_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "decision_id",
                    "source_message_ids",
                    "new_status",
                    "replaced_by",
                    "note",
                    "validated",
                    "validation_note",
                ],
                "additionalProperties": False,
            },
        },
        "open_loops_add": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "loop_type": {"type": "string", "enum": LOOP_TYPES},
                    "source_message_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "loop_type", "source_message_ids"],
                "additionalProperties": False,
            },
        },
        "open_loops_close": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "loop_id": {"type": "string"},
                    "resolution_note": {"type": ["string", "null"]},
                    "source_message_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["loop_id", "resolution_note", "source_message_ids"],
                "additionalProperties": False,
            },
        },
        "conflicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "current_stand": {"type": "string"},
                    "previous_stand": {"type": "string"},
                    "note": {"type": ["string", "null"]},
                },
                "required": ["topic", "current_stand", "previous_stand", "note"],
                "additionalProperties": False,
            },
        },
        "irrelevant_sources": {
            "type": "array",
            "items": {"type": "string"},
        },
        "self_review": {
            "type": "object",
            "properties": {
                **{k: {"type": "boolean"} for k in SELF_REVIEW_KEYS},
                "notes": {"type": ["string", "null"]},
            },
            "required": [*SELF_REVIEW_KEYS, "notes"],
            "additionalProperties": False,
        },
    },
    "required": [
        "knowledge_updates",
        "decisions_add",
        "decisions_update",
        "open_loops_add",
        "open_loops_close",
        "conflicts",
        "irrelevant_sources",
        "self_review",
    ],
    "additionalProperties": False,
}


class ChangeValidationError(Exception):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ChangeValidationError(msg)


def _validate_str_dict(item: Any, required: list[str], name: str) -> None:
    _require(isinstance(item, dict), f"{name}: kein Objekt")
    for key in required:
        _require(key in item, f"{name}: Feld '{key}' fehlt")


def _validate_nonempty_str(item: dict, field_name: str, name: str) -> None:
    _require(
        isinstance(item.get(field_name), str) and item[field_name].strip(),
        f"{name}.{field_name} leer oder fehlt",
    )


def _validate_id_list(item: dict, name: str) -> None:
    """source_message_ids: Liste von Strings (fehlt = leer; die Pipeline
    faellt dann auf den ganzen Batch zurueck)."""
    ids = item.setdefault("source_message_ids", [])
    _require(isinstance(ids, list) and all(isinstance(i, str) for i in ids),
             f"{name}.source_message_ids muss eine Liste von Strings sein")


def _validate_enum(item: dict, field_name: str, allowed: list[str], name: str) -> None:
    _require(item.get(field_name) in allowed, f"{name}.{field_name} ungueltig: {item.get(field_name)!r}")


def validate_change_object(data: Any) -> dict:
    """Prueft die Modellausgabe strukturell. Wirft ChangeValidationError
    bei jeder Abweichung - der Aufrufer darf ein ungueltiges Ergebnis
    niemals mergen oder als verarbeitet markieren."""
    _require(isinstance(data, dict), "Antwort ist kein JSON-Objekt")

    top_level_keys = [
        "knowledge_updates",
        "decisions_add",
        "decisions_update",
        "open_loops_add",
        "open_loops_close",
        "conflicts",
        "irrelevant_sources",
        "self_review",
    ]
    for key in top_level_keys:
        _require(key in data, f"Pflichtfeld '{key}' fehlt")
    for key in top_level_keys:
        if key == "self_review":
            continue
        _require(isinstance(data[key], list), f"'{key}' muss eine Liste sein")

    for item in data["knowledge_updates"]:
        _validate_str_dict(
            item,
            ["section", "content", "entry_type", "scope", "evidence_strength", "confidence", "mode"],
            "knowledge_updates[]",
        )
        _validate_nonempty_str(item, "section", "knowledge_updates[]")
        _validate_nonempty_str(item, "content", "knowledge_updates[]")
        _validate_enum(item, "entry_type", ENTRY_TYPES, "knowledge_updates[]")
        _validate_enum(item, "scope", SCOPES, "knowledge_updates[]")
        _validate_enum(item, "evidence_strength", EVIDENCE_STRENGTHS, "knowledge_updates[]")
        _validate_enum(item, "confidence", CONFIDENCES, "knowledge_updates[]")
        _require(item["mode"] in ("append", "replace"), "knowledge_updates[].mode ungueltig")
        # replace ohne replaces_id verwirft nicht mehr die ganze Antwort: der
        # Eintrag wird angehaengt und gewarnt (merge.py, Review 2026-09-29).
        _validate_id_list(item, "knowledge_updates[]")
        causal_chain = item.get("causal_chain")
        if causal_chain is not None:
            _require(isinstance(causal_chain, list), "knowledge_updates[].causal_chain muss Liste oder null sein")
            _require(
                all(isinstance(step, str) and step.strip() for step in causal_chain),
                "knowledge_updates[].causal_chain darf keine leeren Schritte enthalten",
            )

    for item in data["decisions_add"]:
        _validate_str_dict(
            item,
            ["title", "decision", "rationale", "date", "decision_scope", "validated"],
            "decisions_add[]",
        )
        _validate_nonempty_str(item, "title", "decisions_add[]")
        _validate_nonempty_str(item, "decision", "decisions_add[]")
        _validate_nonempty_str(item, "rationale", "decisions_add[]")
        _validate_enum(item, "decision_scope", DECISION_SCOPES, "decisions_add[]")
        _require(isinstance(item["validated"], bool), "decisions_add[].validated muss bool sein")
        _require(
            item["validated"] is False,
            "decisions_add[].validated muss bei einer neuen Entscheidung false sein "
            "(eine eingefuehrte Massnahme gilt nicht automatisch als wirksam)",
        )

    for item in data["decisions_add"]:
        _validate_id_list(item, "decisions_add[]")
    for item in data["decisions_update"]:
        _validate_str_dict(item, ["decision_id"], "decisions_update[]")
        _validate_nonempty_str(item, "decision_id", "decisions_update[]")
        _validate_id_list(item, "decisions_update[]")
        has_change = any(
            item.get(f) not in (None, "")
            for f in ("new_status", "replaced_by", "note", "validation_note")
        ) or item.get("validated") is not None
        _require(has_change, "decisions_update[]: keine tatsaechliche Aenderung angegeben")

    for item in data["open_loops_add"]:
        _validate_str_dict(item, ["text", "loop_type"], "open_loops_add[]")
        _validate_nonempty_str(item, "text", "open_loops_add[]")
        _validate_enum(item, "loop_type", LOOP_TYPES, "open_loops_add[]")
        _validate_id_list(item, "open_loops_add[]")
    for item in data["open_loops_close"]:
        _validate_str_dict(item, ["loop_id"], "open_loops_close[]")
        _validate_nonempty_str(item, "loop_id", "open_loops_close[]")
        _validate_id_list(item, "open_loops_close[]")

    for item in data["conflicts"]:
        _validate_str_dict(
            item, ["topic", "current_stand", "previous_stand"], "conflicts[]"
        )

    for item in data["irrelevant_sources"]:
        _require(isinstance(item, str), "irrelevant_sources[] muss Strings enthalten")

    review = data["self_review"]
    _require(isinstance(review, dict), "self_review muss ein Objekt sein")
    for key in SELF_REVIEW_KEYS:
        _require(key in review, f"self_review.{key} fehlt")
        _require(isinstance(review[key], bool), f"self_review.{key} muss bool sein")

    return data


def failed_self_review_checks(change: dict) -> list[str]:
    """Liste der vom Modell selbst als 'false' gemeldeten Kritik-Checks
    (Abschnitt 11). Nicht Teil der strukturellen Validierung, weil ein
    einzelnes 'false' kein Formatfehler ist, sondern ein inhaltliches
    Signal, dem die Pipeline separat vertrauen oder misstrauen kann."""
    review = change.get("self_review") or {}
    return [k for k in SELF_REVIEW_KEYS if review.get(k) is not True]
