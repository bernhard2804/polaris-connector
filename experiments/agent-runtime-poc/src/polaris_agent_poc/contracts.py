"""Framework-neutral contracts owned by the thin Polaris layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Protocol


class PermissionClass(StrEnum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    SHELL_EXECUTE = "shell-execute"
    DESTRUCTIVE_SYSTEM = "destructive/system"
    NETWORK = "network"
    SECRETS = "secrets"


@dataclass(frozen=True)
class ModelRoute:
    profile: str
    model_class: str
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    reports_usage: bool = True
    reports_cache_usage: bool = False


@dataclass(frozen=True)
class AgentPolicy:
    permissions: frozenset[PermissionClass]

    def allows(self, permission: PermissionClass) -> bool:
        return permission in self.permissions


@dataclass(frozen=True)
class MemoryHit:
    source: str
    excerpt: str


class MemoryReader(Protocol):
    """Read-only retrieval port; long-term writes remain Curator-owned."""

    async def search(self, query: str, *, limit: int = 5) -> list[MemoryHit]: ...


@dataclass(frozen=True)
class SessionSummary:
    schema: str
    session_id: str
    parent_session_id: str | None
    run_id: str
    agent_role: str
    provider: str
    model: str
    started_at: str
    ended_at: str
    steps: int
    tool_calls: int
    input_tokens: int | None
    cached_input_tokens: int | None
    cache_write_tokens: int | None
    output_tokens: int | None
    cache_hit_rate: float | None
    cost_usd: str | None
    duration_ms: int
    compactions: int
    memory_reads: int
    memory_writes: int
    errors: list[str]
    exit_status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
