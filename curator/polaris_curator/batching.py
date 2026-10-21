"""Gruppiert neue ChatGPT-Messages zu Tagesbatches (Projekt + lokaler
Kalendertag) und teilt zu grosse Tage in kleinere Sub-Batches, ohne
Conversations unnoetig zu zerreissen."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby

from .chatgpt_source import ChatMessage
from .config import CuratorConfig


@dataclass
class DayBatch:
    day: str
    batch_index: int
    messages: list[ChatMessage] = field(default_factory=list)

    @property
    def batch_id(self) -> str:
        return f"{self.day}#{self.batch_index}"

    @property
    def message_ids(self) -> list[str]:
        return [m.message_id for m in self.messages]

    @property
    def total_chars(self) -> int:
        return sum(len(m.content) for m in self.messages)


def _group_by_conversation_preserving_order(messages: list[ChatMessage]) -> list[list[ChatMessage]]:
    """Gruppiert aufeinanderfolgende Messages derselben Conversation zu
    Bloecken, ohne die Gesamtreihenfolge zu veraendern. Das reicht, um
    "Conversations nicht unnoetig zerreissen" pragmatisch umzusetzen,
    ohne eine komplexe globale Neusortierung vorzunehmen."""
    blocks: list[list[ChatMessage]] = []
    for _, group in groupby(messages, key=lambda m: m.conversation_id):
        blocks.append(list(group))
    return blocks


def _split_block(block: list[ChatMessage], cfg: CuratorConfig) -> list[list[ChatMessage]]:
    parts: list[list[ChatMessage]] = []
    current: list[ChatMessage] = []
    chars = 0
    for m in block:
        if current and (
            len(current) + 1 > cfg.max_messages_per_batch or chars + len(m.content) > cfg.max_input_chars_per_batch
        ):
            parts.append(current)
            current, chars = [], 0
        current.append(m)
        chars += len(m.content)
    if current:
        parts.append(current)
    return parts


def build_day_batches(messages: list[ChatMessage], cfg: CuratorConfig) -> list[DayBatch]:
    """Baut aus bereits nach (day, captured_at) sortierten Messages eine
    Liste von DayBatch-Objekten, dabei Tage mit zu vielen Messages oder zu
    viel Text automatisch in mehrere Sub-Batches aufgeteilt."""
    result: list[DayBatch] = []

    for day, day_messages_iter in groupby(messages, key=lambda m: m.local_date):
        day_messages = list(day_messages_iter)
        conversation_blocks = _group_by_conversation_preserving_order(day_messages)

        batch_index = 1
        current: list[ChatMessage] = []
        current_chars = 0

        def flush():
            nonlocal current, current_chars, batch_index
            if current:
                result.append(DayBatch(day=day, batch_index=batch_index, messages=current))
                batch_index += 1
                current = []
                current_chars = 0

        for block in conversation_blocks:
            block_chars = sum(len(m.content) for m in block)

            # Ein Conversation-Block, der allein schon das Batch-Budget
            # sprengt, bleibt zusammen (lieber ein grosser Call als eine
            # zerschnittene Conversation); die Pipeline laesst ihn notfalls
            # allein laufen. Nur wenn er sogar das ganze Laufbudget
            # uebersteigt, wird er in Teile zerlegt - sonst waere der Prompt
            # unbegrenzt gross (Review 2026-09-29).
            if len(block) > cfg.max_messages_per_batch or block_chars > cfg.max_input_chars_per_batch:
                flush()
                parts = (
                    _split_block(block, cfg)
                    if block_chars > cfg.max_total_input_chars_per_run
                    else [list(block)]
                )
                for part in parts:
                    result.append(DayBatch(day=day, batch_index=batch_index, messages=part))
                    batch_index += 1
                continue

            would_exceed = (
                len(current) + len(block) > cfg.max_messages_per_batch
                or current_chars + block_chars > cfg.max_input_chars_per_batch
            )
            if would_exceed and current:
                flush()

            current.extend(block)
            current_chars += block_chars

        flush()

    return result
