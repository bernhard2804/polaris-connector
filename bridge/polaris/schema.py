"""Validierung eingehender Message Events aus der Polaris Inbox.

Keine fachliche Analyse, keine KI - reine Strukturpruefung.
"""

from __future__ import annotations

from datetime import datetime

from .idgen import compute_fallback_id


class ValidationError(Exception):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValidationError(msg)


def validate_and_normalize(data: dict) -> dict:
    """Prueft ein Event auf Pflichtfelder und liefert eine normalisierte Kopie
    zurueck (fehlende optionale Felder werden mit None/Defaults ergaenzt,
    ein fehlendes message.id wird ueber den Fallback-Algorithmus ersetzt).
    """
    _require(isinstance(data, dict), "Event ist kein JSON-Objekt")
    _require(type(data.get("schema_version")) is int and data["schema_version"] == 1,
             "Unbekannte oder fehlende schema_version")
    _require(data.get("source") in ("chatgpt", "claude"), "Unbekannte oder fehlende source")

    project = data.get("project")
    _require(isinstance(project, dict), "project fehlt")
    _require(isinstance(project.get("id"), str) and project["id"].strip(), "project.id fehlt")
    _require(isinstance(project.get("name"), str) and project["name"], "project.name fehlt")

    conversation = data.get("conversation")
    _require(isinstance(conversation, dict), "conversation fehlt")
    _require(
        isinstance(conversation.get("id"), str) and conversation["id"].strip(),
        "conversation.id fehlt",
    )

    message = data.get("message")
    _require(isinstance(message, dict), "message fehlt")
    _require(isinstance(message.get("role"), str) and message["role"], "message.role fehlt")
    _require(isinstance(message.get("content"), str), "message.content fehlt oder kein String")

    _require(isinstance(data.get("captured_at"), str) and data["captured_at"], "captured_at fehlt")
    try:
        datetime.fromisoformat(data["captured_at"].replace("Z", "+00:00"))
    except ValueError:
        raise ValidationError("captured_at ist kein ISO-Zeitstempel") from None
    # message.id ist optional (Ersatz-ID), aber wenn vorhanden ein nicht
    # leerer Text - sonst kollidierten z. B. alle Leer-IDs in unknown.json
    # oder eine Zahl liess sanitize_id abstuerzen (Review 2026-09-29).
    mid = message.get("id")
    _require(mid is None or (isinstance(mid, str) and mid.strip() != ""), "message.id ungueltig")

    normalized = dict(data)
    normalized["conversation"] = dict(conversation)
    normalized["conversation"].setdefault("title", None)
    normalized["conversation"].setdefault("url", None)

    normalized["message"] = dict(message)
    normalized["message"].setdefault("parent_id", None)
    normalized["message"].setdefault("parent_id_source", None)
    normalized["message"].setdefault("source_timestamp", None)

    msg_id = normalized["message"].get("id")
    if not msg_id:
        msg_id = compute_fallback_id(
            conversation.get("id"),
            message.get("role"),
            message.get("parent_id"),
            message.get("content", ""),
            0,
        )
        normalized["message"]["id"] = msg_id
        normalized["message"]["id_source"] = "fallback"
    else:
        normalized["message"].setdefault("id_source", "unknown")

    return normalized
