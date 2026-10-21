"""Small TOML configuration boundary for task/profile/model routing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib

from .contracts import AgentPolicy, ModelRoute, PermissionClass


@dataclass(frozen=True)
class RuntimeConfig:
    config_path: Path
    workspace: Path
    state_dir: Path
    agent_role: str
    route: ModelRoute
    policy: AgentPolicy


def _relative_to_config(value: str, config_path: Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (config_path.parent / candidate).resolve()


def load_config(path: str | Path, profile: str) -> RuntimeConfig:
    config_path = Path(path).expanduser().resolve()
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    runtime = raw.get("runtime", {})
    profiles = raw.get("profiles", {})
    if profile not in profiles:
        raise ValueError(f"unknown profile {profile!r}")
    selected = profiles[profile]
    provider = selected["provider"]

    permissions = frozenset(PermissionClass(value) for value in runtime.get("permissions", ["read-only"]))
    if permissions != {PermissionClass.READ_ONLY}:
        raise ValueError("the PoC deliberately permits only the read-only policy")

    return RuntimeConfig(
        config_path=config_path,
        workspace=_relative_to_config(runtime.get("workspace", "fixture-workspace"), config_path),
        state_dir=_relative_to_config(runtime.get("state_dir", ".poc-state"), config_path),
        agent_role=runtime.get("agent_role", "project-reader"),
        route=ModelRoute(
            profile=profile,
            model_class=selected.get("model_class", "standard"),
            provider=provider,
            model=selected["model"],
            base_url=selected.get("base_url"),
            api_key_env=selected.get("api_key_env"),
            reports_usage=bool(selected.get("reports_usage", provider != "offline")),
            reports_cache_usage=bool(selected.get("reports_cache_usage", False)),
        ),
        policy=AgentPolicy(permissions=permissions),
    )
