"""Claude als letzte Ersatzstufe (seit 2026-09-30)."""

import json
import subprocess
import unittest
from unittest.mock import patch

from polaris_curator import llm
from polaris_curator.config import CuratorConfig


def cfg(**kw):
    base = dict(model="gpt-a", fallback_model="gpt-b", claude_fallback_model="opus", llm_timeout_seconds=5)
    base.update(kw)
    return CuratorConfig(**base)


def done(stdout, rc=0):
    return subprocess.CompletedProcess(["claude"], rc, stdout=stdout, stderr="")


class TestClaudeFallback(unittest.TestCase):
    def setUp(self):
        p = patch.object(llm, "_run_codex_exec", side_effect=llm.LlmUnavailableError("Codex-Nutzungslimit erreicht"))
        self.codex = p.start()
        self.addCleanup(p.stop)

    def test_claude_takes_over_when_codex_unavailable(self):
        out = {"is_error": False, "structured_output": {"ok": 1}, "usage": {"input_tokens": 10, "output_tokens": 5},
               "modelUsage": {"claude-opus-5-5": {}}}
        with patch.object(llm.subprocess, "run", return_value=done(json.dumps(out))) as run:
            res = llm.call_llm("PROMPT", cfg(), schema={"type": "object"})
        self.assertEqual(res.data, {"ok": 1})
        self.assertEqual(res.model, "claude:claude-opus-5-5")
        self.assertEqual(self.codex.call_count, 1)  # nur das Hauptmodell, dann direkt Claude
        cmd = run.call_args.args[0]
        for flag in ("--restricted", "--setting-sources=", "--strict-mcp-config", "--no-session-persistence"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertEqual(run.call_args.kwargs["input"], "PROMPT")

    def test_not_used_when_disabled_or_with_images(self):
        with patch.object(llm.subprocess, "run") as run:
            with self.assertRaises(llm.LlmUnavailableError):
                llm.call_llm("p", cfg(claude_fallback_model=None))
            with self.assertRaises(llm.LlmUnavailableError):
                llm.call_llm("p", cfg(), images=[("a.png", b"x")])
        run.assert_not_called()
        # mit Bildern: zweites Codex-Modell versucht
        self.assertIn("gpt-b", [c.args[1] for c in self.codex.call_args_list])

    def test_claude_error_is_unavailable_without_prompt_echo(self):
        out = {"is_error": True, "result": "Not logged in · Please run /login"}
        with patch.object(llm.subprocess, "run", return_value=done(json.dumps(out), rc=1)):
            with self.assertRaises(llm.LlmUnavailableError) as ctx:
                llm.call_llm("GEHEIMER KUNDENTEXT", cfg())
        self.assertIn("Not logged in", str(ctx.exception))
        self.assertNotIn("GEHEIMER", str(ctx.exception))

    def test_missing_structured_output_is_output_error(self):
        out = {"is_error": False, "result": "kein json"}
        with patch.object(llm.subprocess, "run", return_value=done(json.dumps(out))):
            with self.assertRaises(llm.LlmOutputError):
                llm.call_llm("p", cfg())


class TestClaudeRuntime(unittest.TestCase):
    """runtime: claude (seit 2026-10-07) - Rechner ohne Codex."""

    def test_codex_never_called(self):
        out = {"is_error": False, "structured_output": {"ok": 1}, "modelUsage": {"claude-x": {}}}
        with patch.object(llm, "_run_codex_exec") as codex, \
                patch.object(llm.subprocess, "run", return_value=done(json.dumps(out))) as run:
            res = llm.call_llm("p", cfg(runtime="claude", model="opus", fallback_model=None),
                               images=[("a.png", b"x")], schema={"type": "object"})
        codex.assert_not_called()
        self.assertEqual(res.data, {"ok": 1})
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--model") + 1], "opus")

    def test_fallback_model_also_via_claude(self):
        calls = []

        def fake(prompt, model, c, schema=None):
            calls.append(model)
            if model == "opus":
                raise llm.LlmUnavailableError("limit")
            return llm.LlmResult({"ok": 1}, f"claude:{model}", None)

        with patch.object(llm, "_run_claude", side_effect=fake), patch.object(llm, "_run_codex_exec") as codex:
            res = llm.call_llm("p", cfg(runtime="claude", model="opus", fallback_model="sonnet"))
        self.assertEqual(calls, ["opus", "sonnet"])
        self.assertEqual(res.model, "claude:sonnet")
        codex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
