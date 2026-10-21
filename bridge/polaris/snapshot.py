"""Chat-Abbilder: aus dem rohen Unterhaltungs-JSON von ChatGPT Turns bilden
(seit 2026-09-29, Architektur "Chat-Abbild").

Anlass: Bis Extension 0.3.0 entschieden drei Stellen unabhaengig, was ein
Turn ist, welche ID er hat und wann er fertig ist (DOM-Erfassung und
Schnittstellen-Modus in JavaScript, Export-Import hier). Jede Abweichung
erzeugte Dubletten, abgeschnittene Antworten oder falsche Zuordnungen. Jetzt
liefert die Extension nur noch das rohe JSON der Web-Schnittstelle, und NUR
diese Funktion interpretiert es - dieselbe fuer den offiziellen
Datenexport, der dasselbe Format hat.

Regeln:
- Projekt aus den Anbieterdaten (gizmo_id, Praefix g-p-<id> ohne Slug),
  nie aus einer Adresse. Chats ohne Projekt ergeben nichts.
- Nur der aktuelle Strang (current_node -> Wurzel).
- Ein Turn: eine Nutzer-Nachricht, oder alle Nachrichten zwischen zwei
  Nutzer-Nachrichten (Assistent, Werkzeugschritte, Denkprozesse). member_ids
  nennt alle; die Bridge ordnet den Turn einem schon archivierten zu, wenn
  eine davon bekannt ist (die DOM-Erfassung hat evtl. eine andere davon als
  ID verwendet).
- ID eines Turns = frueheste Nachricht (bleibt stabil, auch wenn spaeter
  Text dazukommt).
- Inhalt = sichtbarer Text (text/multimodal_text) der Assistenten-
  Nachrichten an "all"; Denkprozesse und Werkzeugaufrufe zaehlen als
  Mitglied, liefern aber keinen Text.
- Ein Assistenten-Turn am Ende des Strangs ist erst fertig, wenn seine
  letzte Nachricht sichtbarer Assistententext ist, nicht mehr laeuft
  (status != in_progress) und end_turn nicht ausdruecklich False ist -
  ODER wenn der Chat beim ERFASSEN seit IDLE_SECONDS unveraendert war (now =
  captured_at, nicht die aktuelle Uhr - sonst galt ein mitten in der Antwort
  erfasstes, spaet verarbeitetes Abbild als ruhend; Review Runde 5) und die
  letzte Nachricht nicht ausdruecklich in_progress ist. Nachweislich ruhende
  Abbilder liefert die Extension (erneuter Abruf nach 190 s Ruhe).
  Unfertige Turns werden nicht ausgegeben - das naechste Abbild bringt sie.
- Canvas-Dokumente (recipient canmore.*) und Code-Bloecke an "all" zaehlen
  als Inhalt.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

TEXT_CONTENT_TYPES = {"text", "multimodal_text"}
IDLE_SECONDS = 180
STALE_STREAM_SECONDS = 24 * 3600
_GIZMO_RE = re.compile(r"^(g-p-[A-Za-z0-9]+)")


def project_id_of(conv: dict) -> str | None:
    m = _GIZMO_RE.match(str(conv.get("gizmo_id") or ""))
    return m.group(1) if m else None


def _iso(ts) -> str | None:
    if ts in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _text_of(message: dict) -> str:
    content = message.get("content") or {}
    if content.get("content_type") not in TEXT_CONTENT_TYPES:
        return ""
    parts = [p for p in (content.get("parts") or []) if isinstance(p, str)]
    return "\n".join(parts).strip()


def _role(message: dict) -> str | None:
    return (message.get("author") or {}).get("role")


def _hidden(message: dict) -> bool:
    return bool((message.get("metadata") or {}).get("is_visually_hidden_from_conversation"))


def _to_all(message: dict) -> bool:
    return message.get("recipient") in (None, "", "all")


def _canvas_text(message: dict) -> str:
    """Canvas (recipient canmore.create_textdoc/update_textdoc): JSON mit
    name/content bzw. updates[].replacement."""
    if not str(message.get("recipient") or "").startswith("canmore."):
        return ""
    raw = "".join(p for p in ((message.get("content") or {}).get("parts") or [message.get("content", {}).get("text", "")]) if isinstance(p, str))
    try:
        import json as _json
        data = _json.loads(raw)
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    body = data.get("content") or "\n".join(
        str(u.get("replacement", "")) for u in data.get("updates") or [] if isinstance(u, dict)
    )
    title = data.get("name") or data.get("title") or "Canvas"
    return f"[Canvas: {title}]\n{body}".strip() if body else ""


def _visible_text(message: dict) -> str:
    """Sichtbarer Inhalt einer Assistenten-Nachricht."""
    if _hidden(message):
        return ""
    canvas = _canvas_text(message)
    if canvas:
        return canvas
    if not _to_all(message):
        return ""
    content = message.get("content") or {}
    if content.get("content_type") == "code" and isinstance(content.get("text"), str):
        return f"```\n{content['text'].strip()}\n```" if content["text"].strip() else ""
    return _text_of(message)


def _attachments(message: dict) -> list[str]:
    names = [a.get("name") for a in (message.get("metadata") or {}).get("attachments") or [] if isinstance(a, dict)]
    return sorted({n for n in names if n})


def active_branch(conv: dict) -> list[dict]:
    nodes = conv.get("mapping") or {}
    chain, seen = [], set()
    node_id = conv.get("current_node")
    while node_id and node_id in nodes and node_id not in seen:
        seen.add(node_id)
        chain.append(nodes[node_id])
        node_id = nodes[node_id].get("parent")
    chain.reverse()
    return [n.get("message") for n in chain if n.get("message")]


def _assistant_turn_complete(members: list[dict], idle: bool = False) -> bool:
    last = members[-1]
    # Ruhe gibt nur Enden frei, die nicht ausdruecklich noch laufen (Bild,
    # Canvas, Werkzeug) - nie ein "in_progress" (Review Runde 5, F1). Ausnahme:
    # seit STALE_STREAM_SECONDS unveraendert - dann ist der Stream abgebrochen,
    # der sichtbare Teil wird uebernommen statt fuer immer zu fehlen (Runde 6).
    if idle and (last.get("status") != "in_progress" or idle == "stale"):
        return True
    if _role(last) != "assistant" or _hidden(last) or not _visible_text(last):
        return False  # Werkzeugschritt oder Denkprozess zuletzt: laeuft noch
    if last.get("status") == "in_progress":
        return False
    if last.get("end_turn") is False:
        return False
    return True


def is_idle(conv: dict, now: float | None = None):
    """False, True (ruhig) oder "stale" (seit STALE_STREAM_SECONDS unveraendert)."""
    try:
        updated = float(conv.get("update_time"))
    except (TypeError, ValueError):
        return False
    now = now if now is not None else datetime.now(timezone.utc).timestamp()
    age = now - updated
    if age >= STALE_STREAM_SECONDS:
        return "stale"
    return age >= IDLE_SECONDS


def events_from_conversation(conv: dict, captured_fallback: str, capture: str = "api",
                             now: float | None = None) -> list[dict]:
    """Events im Bridge-Schema (schema_version 1) fuer alle FERTIGEN Turns.
    Der offizielle Export ist immer "ruhend" (update_time liegt zurueck)."""
    idle = is_idle(conv, now)
    project_id = project_id_of(conv)
    if not project_id:
        return []
    conv_id = conv.get("conversation_id") or conv.get("id")
    if not conv_id:
        return []
    messages = [m for m in active_branch(conv) if m.get("id")]

    # Gruppieren: Nutzer-Nachricht = eigener Turn; alles dazwischen = ein Turn.
    groups: list[tuple[str, list[dict]]] = []
    for m in messages:
        role = _role(m)
        if role == "system":
            continue
        if role == "user":
            if _hidden(m):
                continue
            groups.append(("user", [m]))
        elif groups and groups[-1][0] == "assistant":
            groups[-1][1].append(m)
        else:
            groups.append(("assistant", [m]))

    events, parent = [], None
    for index, (role, members) in enumerate(groups):
        is_last = index == len(groups) - 1
        if role == "user":
            text = _text_of(members[0])
            files = _attachments(members[0])
            if not text and not files:
                continue
        else:
            if is_last and not _assistant_turn_complete(members, idle):
                break
            text = "\n\n".join(
                t for t in (_visible_text(m) for m in members if _role(m) == "assistant") if t
            )
            files = []
            if not text:
                continue
        head = members[0]
        created = _iso(head.get("create_time"))
        message = {
            "id": head["id"],
            "id_source": "native",
            "parent_id": parent,
            "parent_id_source": "provider" if parent else None,
            "role": role,
            "source_timestamp": created,
            "content": text,
            "capture": capture,
            "member_ids": [m["id"] for m in members],
        }
        if files:
            message["attachments"] = files
        events.append({
            "schema_version": 1,
            "source": "chatgpt",
            "project": {"id": project_id, "name": project_id},
            "conversation": {
                "id": conv_id,
                "title": conv.get("title"),
                "url": f"https://chatgpt.com/g/{project_id}/c/{conv_id}",
            },
            "message": message,
            "captured_at": created or captured_fallback,
        })
        parent = head["id"]
    return events


def last_turn_held_back(conv: dict, now: float | None = None) -> bool:
    """True, wenn die letzte Antwort (noch) zurueckgehalten wird."""
    messages = [m for m in active_branch(conv) if m.get("id") and _role(m) != "system"]
    if not messages:
        return False
    if _role(messages[-1]) == "user":
        # Frage gestellt, Antwort noch nicht gespeichert: sie kommt nur mit
        # einem spaeteren Abbild - wird der Chat nicht mehr geoeffnet, soll das
        # auffallen (Review Runde 6, F2). Ruhte der Chat schon, kommt keine.
        return not is_idle(conv, now)
    members = []
    for m in reversed(messages):
        if _role(m) == "user":
            break
        members.insert(0, m)
    return bool(members) and not _assistant_turn_complete(members, is_idle(conv, now))


def active_message_ids(conv: dict) -> list[str]:
    """Alle Nachrichten-IDs des aktuellen Strangs (fuer die Markierung
    verworfener Zweige nach Neu-Generieren/Bearbeiten)."""
    return [m["id"] for m in active_branch(conv) if m.get("id")]


def finished_assistant_nodes(conv: dict) -> int:
    """Zahl sichtbar abgeschlossener Assistenten-Nachrichten an "all" - zum
    Erkennen einer geaenderten Antwortform (0 Events trotz Antworten)."""
    return sum(
        1 for m in active_branch(conv)
        if _role(m) == "assistant" and _to_all(m) and not _hidden(m) and m.get("status") != "in_progress"
    )
