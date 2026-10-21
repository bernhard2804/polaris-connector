"""Deterministischer Fallback-Identifier fuer Messages ohne native ID.

Muss exakt mit der Implementierung in extension/content.js
(computeFallbackId) uebereinstimmen:

    canonical = conversation_id|role|parent_id|normalized_content|position
    id = "fb_" + sha256(canonical).hex[:32]

In der Praxis berechnet die Extension den Fallback-Identifier bereits beim
Erfassen (dort sind conversation_id, role, parent_id, content und Position
zuverlaessig verfuegbar). Diese Funktion existiert als Sicherheitsnetz in
der Bridge, falls jemals ein Event ohne message.id ankommt.
"""

from __future__ import annotations

import hashlib
import re


def normalize_content(text: str) -> str:
    text = text.strip().lower()
    return re.sub(r"\s+", " ", text)


def compute_fallback_id(
    conversation_id: str | None,
    role: str | None,
    parent_id: str | None,
    content: str,
    position: int,
) -> str:
    canonical = "|".join(
        [
            conversation_id or "",
            role or "",
            parent_id or "",
            normalize_content(content or ""),
            str(position),
        ]
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return "fb_" + digest[:32]
