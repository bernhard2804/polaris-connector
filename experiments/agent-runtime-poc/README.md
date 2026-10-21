# Polaris Agent Runtime PoC

This is an isolated, reversible proof of concept for ADR 001. It does not
replace the Bridge, Curator, Machine Memory, Claude Code, Codex, Antigravity,
backup jobs, or any systemd unit.

## What it proves

- Pydantic AI owns the agent/model/tool loop and OpenTelemetry spans.
- Pydantic AI Harness owns append-only step events, snapshots, the tool-effect
  ledger, and restart/resume.
- A small Polaris layer owns only profile configuration, policy contracts,
  normalized summaries, and a versioned open JSONL export.
- The same three read-only business tools are used by all model profiles.
- Session state and long-term memory are separate. The `MemoryReader` protocol
  is read-only; this PoC deliberately performs no Machine Memory write.

The PoC registers no shell, network, write, secret, or destructive tool. Every
file path is resolved below `fixture-workspace`, symlinks are rejected, and
individual reads are size-limited.

## Reproduce the offline start/resume test

```bash
cd experiments/agent-runtime-poc
uv sync
PYDANTIC_AI_NO_BANNER=1 uv run polaris-agent-poc \
  --config config.example.toml --profile offline run \
  --session demo --prompt 'Durchsuche das Projekt nach Runtime-Hinweisen.'

# This is intentionally a second process.
PYDANTIC_AI_NO_BANNER=1 uv run polaris-agent-poc \
  --config config.example.toml --profile offline run \
  --session demo --resume \
  --prompt 'Setze die Arbeit fort und lies die Architekturdatei im Detail.'

uv run pytest
```

Verified on 2026-09-20 with Python 3.14.7, Pydantic AI 2.46.0, and Harness
0.32.0: start and resume completed in separate processes, each invoked one
read-only tool, two native run directories were created, the export contained
both runs and eight normalized transcript messages, and OTel contained two
agent spans, four model spans, and the two tool spans. The automated suite
reported `4 passed`.

Generated state is ignored by Git:

```text
.poc-state/
  step-store/<run_id>/
    run.json
    events.jsonl                 # append-only Harness events
    tool_effects.jsonl           # append-only effect transitions
    snapshots/*.json             # native resume source, max 3 per run
  exports/<session_id>.jsonl     # polaris.session.v1 audit/exit format
  metrics/<session_id>.jsonl     # provider-neutral run summaries
  telemetry/<session_id>.jsonl   # local OTel spans
```

The native Harness store remains the resume source. `polaris.session.v1` is an
append-only audit and migration format, not a second agent engine. It contains
normalized transcript messages, tool calls/results, lineage, boundary events,
and summaries, but no serialized Pydantic message objects.

Prompt, response, tool-argument, and tool-result contents are disabled in OTel.
Tool definitions and redacted message shapes remain visible for diagnosis.

## Switching providers

Select a profile; do not modify the task or tool code:

```bash
# Ollama must already be running and the configured model must support tools.
uv run polaris-agent-poc --config config.example.toml --profile ollama run \
  --session local-demo --prompt 'Find runtime evidence.'

# Direct provider SDKs read their standard environment variables.
OPENAI_API_KEY=... uv run polaris-agent-poc \
  --config config.example.toml --profile openai run \
  --session cloud-demo --prompt 'Find runtime evidence.'

ANTHROPIC_API_KEY=... uv run polaris-agent-poc \
  --config config.example.toml --profile anthropic run \
  --session cloud-demo --prompt 'Find runtime evidence.'

DEEPSEEK_API_KEY=... uv run polaris-agent-poc \
  --config config.example.toml --profile deepseek run \
  --session cloud-demo --prompt 'Find runtime evidence.'
```

The API key is never written into TOML or session metadata. A custom
OpenAI-compatible endpoint can use the `openai_compatible` profile and an
environment-variable name in `api_key_env`.

At evaluation time no cloud API key was present, the Ollama daemon was stopped,
and its only manifest referenced missing blobs. Therefore the Cloud/Ollama E2E
runs were not performed and no result is claimed for them.

## Opt-in provider cache A/B test

This test makes real, potentially billable model calls. It rejects the offline
model because synthetic token values are not cache evidence.

```bash
OPENAI_API_KEY=... uv run polaris-agent-poc \
  --config config.example.toml --profile openai cache-test \
  --steps 20 --output .poc-state/cache-openai.jsonl
```

It executes both variants with the same provider, model, prompts, and step
count:

- `stable-prefix`: a long unchanged system prefix plus append-only history;
- `mutated-prefix`: a mutation at the start of that prefix on every step.

Every request records provider-normalized `input_tokens`,
`cached_input_tokens`, cache writes, output, cost when available, and elapsed
time. The final summaries use `cached_input_tokens / input_tokens`; Pydantic AI
normalizes input tokens to include cache reads. A provider returning no cache
field is reported as zero by its adapter, so provider capability must be noted
alongside any interpretation. This repository contains no fabricated cache
benchmark result.

Profiles declare whether their adapter reports ordinary and cache-token usage.
Unsupported values are persisted as `null`, never silently interpreted as a
measured zero. The deterministic offline model therefore has `null` token
metrics in the neutral summary even though its test double supplies synthetic
usage internally so the framework instrumentation path can be exercised.

## Rollback

Delete this `experiments/agent-runtime-poc/` directory (or simply revert its
commit). No productive path, service, Machine Memory file, or Omarchy setting
depends on it.
