"""Kapselt den Aufruf der konfigurierten LLM-Runtime (aktuell: Codex CLI,
nicht-interaktiv via `codex exec`).

Bewusst hinter einer schmalen Funktion (call_llm) gekapselt, damit spaeter
ein automatischer Model Resolver oder eine andere Runtime (z.B. direkter
API-Zugriff) ergaenzt werden kann, ohne den Rest des Curators anzufassen.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .change_schema import JSON_SCHEMA
from .config import CuratorConfig

_TOKENS_RE = re.compile(r"tokens used\s*\n?\s*([\d,]+)", re.IGNORECASE)
_SESSION_RE = re.compile(r"^session id:\s*(\S+)", re.IGNORECASE | re.MULTILINE)

logger = logging.getLogger("polaris_curator")


class LlmUnavailableError(Exception):
    """Die Runtime/das Modell selbst ist nicht erreichbar (Exec-Fehler,
    Timeout, Auth-Problem). Rechtfertigt einen Fallback-Versuch bzw. einen
    sauberen Abbruch des gesamten Laufs, siehe pipeline.py."""


class LlmOutputError(Exception):
    """Die Runtime lief durch, aber die Ausgabe war kein gueltiges JSON.
    Betrifft typischerweise nur diesen einen Batch."""


@dataclass
class LlmResult:
    data: dict
    model: str
    tokens_used: int | None


def _parse_tokens_used(stderr: str) -> int | None:
    """Liest die Token-Summe aus der Codex-stderr ("tokens used\\n12,345").

    Codex gibt die Summe am Ende aus -> letzter Treffer. Mehrere Treffer sind
    ungewoehnlich (am 2026-09-24 meldete ein 573-s-Aufruf unplausible 3.107
    Tokens, nicht mehr rekonstruierbar, da --ephemeral keine Codex-Logs
    schreibt) und werden deshalb mit allen Werten und der Session-ID geloggt
    - bewusst ohne weiteren stderr-Inhalt, der Prompt und Antwort enthaelt.
    """
    values: list[int] = []
    for raw in _TOKENS_RE.findall(stderr):
        try:
            values.append(int(raw.replace(",", "")))
        except ValueError:
            continue
    if not values:
        return None
    if len(values) > 1:
        session = _SESSION_RE.search(stderr)
        logger.warning(
            "codex exec meldete mehrere Token-Summen %s (session=%s); verwende die letzte",
            values,
            session.group(1) if session else "?",
        )
    return values[-1]


def _run_codex_exec(
    prompt: str,
    model: str,
    cfg: CuratorConfig,
    images: list[tuple[str, bytes]] | None = None,
    schema: dict | None = None,
) -> LlmResult:
    with tempfile.TemporaryDirectory(prefix="polaris-curator-") as tmp_dir:
        tmp = Path(tmp_dir)
        schema_path = tmp / "schema.json"
        output_path = tmp / "output.json"
        schema_path.write_text(json.dumps(schema or JSON_SCHEMA), encoding="utf-8")

        image_args: list[str] = []
        for idx, (name, data) in enumerate(images or [], start=1):
            image_path = tmp / f"image-{idx}{Path(name).suffix.lower() or '.png'}"
            image_path.write_bytes(data)
            # --image=<pfad> statt "-i <pfad>": die Option nimmt mehrere Werte
            # an und koennte sonst das abschliessende "-" (Prompt via stdin)
            # als weiteren Bildpfad schlucken.
            image_args.append(f"--image={image_path}")

        cmd = [
            "codex",
            "exec",
            *image_args,
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "--ephemeral",
            "-s",
            "read-only",
            "-m",
            model,
            *(
                ["-c", f'model_reasoning_effort="{cfg.reasoning_effort}"']
                if cfg.reasoning_effort
                else []
            ),
            "-C",
            str(tmp),
            "--output-schema",
            str(schema_path),
            "-o",
            str(output_path),
            "-",
        ]

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=cfg.llm_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LlmUnavailableError(f"codex exec konnte nicht ausgefuehrt werden: {exc}") from exc

        if result.returncode != 0:
            raise LlmUnavailableError(
                f"codex exec beendete sich mit Code {result.returncode}: {_stderr_summary(result.stderr)}"
            )

        if not output_path.is_file():
            raise LlmOutputError("codex exec hat keine Ausgabedatei erzeugt")

        raw = output_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LlmOutputError(f"Ausgabe ist kein gueltiges JSON: {exc}") from exc

        return LlmResult(data=data, model=model, tokens_used=_parse_tokens_used(result.stderr))


def call_llm(
    prompt: str,
    cfg: CuratorConfig,
    images: list[tuple[str, bytes]] | None = None,
    schema: dict | None = None,
) -> LlmResult:
    """Ruft zuerst cfg.model auf. Ist die Runtime dabei nicht erreichbar
    (LlmUnavailableError), wird - falls konfiguriert - genau einmal
    cfg.fallback_model versucht. Schlaegt auch das fehl, wird der Fehler
    weitergereicht (Aufrufer bricht den Lauf sauber ab). Ein reiner
    Ausgabefehler (LlmOutputError) wird NICHT automatisch mit dem
    Fallback-Modell wiederholt - er betrifft typischerweise nur die
    konkrete Antwort, nicht die Verfuegbarkeit der Runtime.

    runtime "claude" (seit 2026-10-07, fuer Rechner ohne Codex): Haupt- und
    Ersatzmodell laufen ueber die Claude-Code-CLI. Bilder (Mail-Anhaenge)
    kann dieser Weg nicht mitgeben - sie entfallen mit Warnung, der Text des
    Dokuments wird trotzdem kuratiert."""
    if cfg.runtime == "command":
        if images:
            logger.warning("runtime command: %d Bild(er) entfallen (nur Text wird kuratiert)", len(images))
        try:
            return _run_command(prompt, cfg.model, cfg, schema)
        except LlmUnavailableError:
            if not cfg.fallback_model or cfg.fallback_model == cfg.model:
                raise
            return _run_command(prompt, cfg.fallback_model, cfg, schema)
    if cfg.runtime == "claude":
        if images:
            logger.warning("runtime claude: %d Bild(er) entfallen (nur Text wird kuratiert)", len(images))
        try:
            return _run_claude(prompt, cfg.model, cfg, schema)
        except LlmUnavailableError:
            if not cfg.fallback_model or cfg.fallback_model == cfg.model:
                raise
            return _run_claude(prompt, cfg.fallback_model, cfg, schema)
    try:
        return _run_codex_exec(prompt, cfg.model, cfg, images, schema)
    except LlmUnavailableError as first:
        error = first
    claude_model = getattr(cfg, "claude_fallback_model", None)
    # Ist Claude eingerichtet, springt es direkt ein (Nutzerwunsch 2026-09-30:
    # "claude direkt als alternative, nicht ein weiteres codex modell" - ein
    # zweites Codex-Modell teilt Konto und Limit). Nur bei Bildern (Mail-
    # Anhaenge), die der Claude-Weg nicht mitgeben kann, bleibt fallback_model.
    if claude_model and not images:
        logger.warning("Codex nicht erreichbar (%s) - Ersatz: Claude %s", str(error)[:200], claude_model)
        return _run_claude(prompt, claude_model, cfg, schema)
    if cfg.fallback_model:
        return _run_codex_exec(prompt, cfg.fallback_model, cfg, images, schema)
    raise error


def _run_claude(prompt: str, model: str, cfg: CuratorConfig, schema: dict | None = None) -> LlmResult:
    """Claude ueber die Claude-Code-CLI (`claude -p`), abgeschottet: keine
    Werkzeuge, keine MCP-Server, keine Einstellungen/CLAUDE.md/Hooks aus dem
    Home, keine gespeicherte Sitzung, leeres Arbeitsverzeichnis. Geprueft
    2026-09-30: ~1k Token Vorlauf, die globale CLAUDE.md wird nicht geladen.
    (--bare liest die Anmeldung nicht und scheidet deshalb aus.)"""
    with tempfile.TemporaryDirectory(prefix="polaris-curator-claude-") as tmp_dir:
        cmd = [
            "claude", "-p",
            "--restricted", "--setting-sources=", "--tools", "", "--strict-mcp-config",
            "--no-session-persistence", "--disable-slash-commands",
            "--output-format", "json",
            "--model", model,
            "--json-schema", json.dumps(schema or JSON_SCHEMA),
        ]
        try:
            result = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                    timeout=cfg.llm_timeout_seconds, cwd=tmp_dir)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LlmUnavailableError(f"claude konnte nicht ausgefuehrt werden: {exc}") from exc
    try:
        out = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        out = {}
    if result.returncode != 0 or out.get("is_error") or not out:
        # Nur die Kurzmeldung (z. B. "Not logged in", Limit), nie den Prompt.
        reason = str(out.get("result") or "").strip()[:300] or _stderr_summary(result.stderr)
        raise LlmUnavailableError(f"claude beendete sich mit Code {result.returncode}: {reason}")
    data = out.get("structured_output")
    if not isinstance(data, dict):
        try:
            data = json.loads(out.get("result") or "")
        except (TypeError, json.JSONDecodeError) as exc:
            raise LlmOutputError(f"claude lieferte kein strukturiertes Ergebnis: {exc}") from exc
        if not isinstance(data, dict):
            raise LlmOutputError("claude lieferte kein JSON-Objekt")
    usage = out.get("usage") or {}
    tokens = sum(int(usage.get(k) or 0) for k in
                 ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")) or None
    used = next(iter((out.get("modelUsage") or {}).keys()), model)
    return LlmResult(data=data, model=f"claude:{used}", tokens_used=tokens)


def _stderr_summary(stderr: str) -> str:
    """Nur die Fehlerzeilen von codex, nicht das Echo des Prompts: sonst
    landeten Kundeninhalte im systemd-Journal und in Benachrichtigungen, und
    die eigentliche Ursache (z. B. Nutzungslimit) ging darin unter
    (2026-09-29, Timer-Lauf 21:30)."""
    errors = list(dict.fromkeys(ln.strip() for ln in (stderr or "").splitlines() if ln.strip().startswith("ERROR")))
    for ln in errors:
        m = re.search(r"usage limit.*?try again at ([^.]+(?:\.[^.]+)?)", ln)
        if "usage limit" in ln:
            when = f", wieder ab {m.group(1).strip()}" if m else ""
            return f"Codex-Nutzungslimit erreicht{when}"
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    if last and last not in errors:
        errors.append(last)  # der eigentliche Grund steht meist zuletzt
    return " | ".join(errors)[:500]



RUNTIMES = {"codex": "codex", "claude": "claude", "command": None}

_SCHEMA_INSTRUCTION = (
    "\n\n---\nAusgabeformat (verbindlich): Antworte ausschliesslich mit genau einem "
    "JSON-Objekt, ohne Erlaeuterung und ohne Codeblock, das diesem JSON-Schema entspricht:\n"
)
_ERROR_HINT = re.compile(r"error|fehler|limit|quota|auth|login|unauthor|denied|not found|rate", re.I)


def _command_error_summary(stderr: str, stdout: str) -> str:
    """Nur kurze Fehlerzeilen, nie das Prompt-Echo (Kundeninhalte)."""
    lines = [ln.strip() for ln in (stderr or "").splitlines() + (stdout or "").splitlines()[-5:]
             if ln.strip() and _ERROR_HINT.search(ln)]
    picked = list(dict.fromkeys(ln[:200] for ln in lines))[:3]
    return " | ".join(picked) or "keine Fehlermeldung"


def _extract_json(stdout: str, key: str | None) -> dict:
    """Antwort einer beliebigen CLI -> dict. Mit key: Feld einer JSON-Huelle
    (Punkt-Pfad). Sonst: ganze Ausgabe als JSON, bekannte Huellenfelder, oder
    das aeusserste {...} im Text (z. B. in einem ```json-Block)."""
    text = (stdout or "").strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    if key:
        node = parsed
        for part in key.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if isinstance(node, str):
            return _extract_json(node, None)
        if isinstance(node, dict):
            return node
        raise LlmOutputError(f"Ausgabe enthaelt kein Objekt unter {key!r}")
    if isinstance(parsed, dict):
        for wrap in ("structured_output", "response", "result", "output", "text"):
            inner = parsed.get(wrap)
            if isinstance(inner, dict):
                return inner
            if isinstance(inner, str) and "{" in inner:
                try:
                    return _extract_json(inner, None)
                except LlmOutputError:
                    pass
        return parsed
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise LlmOutputError(f"Ausgabe ist kein gueltiges JSON: {exc}") from exc
        if isinstance(obj, dict):
            return obj
    raise LlmOutputError("Ausgabe enthaelt kein JSON-Objekt")


def _run_command(prompt: str, model: str, cfg: CuratorConfig, schema: dict | None = None) -> LlmResult:
    """Beliebige Agenten-CLI (runtime: command). Laeuft in einem leeren
    Arbeitsverzeichnis; ob die CLI Werkzeuge (Shell, Dateien) benutzen darf,
    bestimmen ihre eigenen Flags in `command` - dort abschalten, die Chats
    sind fremder Inhalt (siehe curator/README.md)."""
    if not cfg.command:
        raise LlmUnavailableError("runtime command ohne 'command' in curator.yaml")
    schema_json = json.dumps(schema or JSON_SCHEMA, ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix="polaris-curator-cmd-") as tmp_dir:
        tmp = Path(tmp_dir)
        uses_prompt_file = any("{prompt_file}" in a for a in cfg.command)
        uses_schema = any("{schema_file}" in a or "{schema_json}" in a for a in cfg.command)
        full_prompt = prompt if uses_schema else prompt + _SCHEMA_INSTRUCTION + schema_json
        (tmp / "prompt.txt").write_text(full_prompt, encoding="utf-8")
        (tmp / "schema.json").write_text(schema_json, encoding="utf-8")
        subst = {"{model}": model, "{prompt_file}": str(tmp / "prompt.txt"),
                 "{schema_file}": str(tmp / "schema.json"), "{schema_json}": schema_json}
        argv = []
        for arg in cfg.command:
            for k, v in subst.items():
                arg = arg.replace(k, v)
            argv.append(arg)
        try:
            # Im Dateimodus stdin schliessen: manche CLIs warten sonst auf Eingabe.
            result = subprocess.run(argv, input=None if uses_prompt_file else full_prompt,
                                    stdin=subprocess.DEVNULL if uses_prompt_file else None,
                                    capture_output=True, text=True, timeout=cfg.llm_timeout_seconds,
                                    cwd=tmp_dir)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LlmUnavailableError(f"{cfg.command[0]} konnte nicht ausgefuehrt werden: {exc}") from exc
    if result.returncode != 0:
        raise LlmUnavailableError(f"{cfg.command[0]} beendete sich mit Code {result.returncode}: "
                                  f"{_command_error_summary(result.stderr, result.stdout)}")
    data = _extract_json(result.stdout, cfg.command_output_key)
    return LlmResult(data=data, model=f"{Path(cfg.command[0]).name}:{model}", tokens_used=None)


def main_binary(cfg: CuratorConfig) -> str:
    if cfg.runtime == "command":
        return cfg.command[0] if cfg.command else "(command fehlt)"
    return RUNTIMES.get(cfg.runtime) or cfg.runtime
_CHECK_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                 "required": ["ok"], "additionalProperties": False}


def runtime_binaries(cfg: CuratorConfig) -> dict[str, str | None]:
    """Benoetigte CLIs -> Pfad (None = fehlt): die Runtime selbst plus Claude,
    falls als Ersatz oder Pruefer eingetragen."""
    import shutil

    needed = [main_binary(cfg)]
    if cfg.runtime != "claude" and (cfg.claude_fallback_model or cfg.reviewer_model):
        needed.append("claude")
    if cfg.runtime == "claude" and cfg.reviewer_model:
        needed.append("claude")
    return {b: (shutil.which(b) or (b if b.startswith("/") and Path(b).is_file() else None))
            for b in dict.fromkeys(needed)}


def check_call(cfg: CuratorConfig) -> LlmResult:
    """Ein winziger echter Aufruf des Hauptmodells ohne Ersatzstufen - zeigt,
    ob CLI, Anmeldung und Modellname stimmen (polaris-curator llm-check --call)."""
    prompt = 'Antworte ausschliesslich mit dem JSON-Objekt {"ok": true}.'
    if cfg.runtime == "claude":
        return _run_claude(prompt, cfg.model, cfg, _CHECK_SCHEMA)
    if cfg.runtime == "command":
        return _run_command(prompt, cfg.model, cfg, _CHECK_SCHEMA)
    return _run_codex_exec(prompt, cfg.model, cfg, None, _CHECK_SCHEMA)
