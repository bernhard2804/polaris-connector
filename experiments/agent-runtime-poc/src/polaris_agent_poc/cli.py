"""CLI for the isolated PoC and opt-in cache experiment."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .cache_test import run_cache_test
from .config import load_config
from .runtime import AgentRuntime


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.example.toml")
    parser.add_argument("--profile", default="offline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="start or resume a read-only session")
    run.add_argument("--session", required=True)
    run.add_argument("--prompt", required=True)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--parent-session")

    cache = subparsers.add_parser("cache-test", help="run a paid 20-50 step provider A/B test")
    cache.add_argument("--steps", type=int, default=20)
    cache.add_argument("--output", type=Path, default=Path(".poc-state/cache-results.jsonl"))
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    config = load_config(args.config, args.profile)
    if args.command == "run":
        outcome = await AgentRuntime(config).run(
            session_id=args.session,
            prompt=args.prompt,
            resume=args.resume,
            parent_session_id=args.parent_session,
        )
        print(outcome.output)
        print(json.dumps(outcome.summary.to_dict(), ensure_ascii=False, indent=2))
        print(f"export={outcome.export_path}")
        print(f"telemetry={outcome.telemetry_path}")
        return 0
    summaries = await run_cache_test(config, steps=args.steps, output=args.output.resolve())
    print(json.dumps(summaries, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main_async(_parser().parse_args())))


if __name__ == "__main__":
    main()
