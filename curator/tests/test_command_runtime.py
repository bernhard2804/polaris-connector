"""runtime: command - beliebige Agenten-CLI (seit 2026-10-07)."""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import conftest_helpers  # noqa: F401 - sys.path

from polaris_curator import llm
from polaris_curator.config import CuratorConfig, _default_config


def fake_cli(body: str) -> str:
    d = Path(tempfile.mkdtemp())
    f = d / "fake-agent"
    f.write_text(f"#!{sys.executable}\nimport sys, json\n{body}\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return str(f)


def cfg(command, **kw):
    base = dict(runtime="command", model="m1", fallback_model=None, command=command, llm_timeout_seconds=20)
    base.update(kw)
    return CuratorConfig(**base)


class TestCommandRuntime(unittest.TestCase):
    def test_stdin_prompt_and_schema_instruction(self):
        cli = fake_cli("p = sys.stdin.read()\n"
                       "assert 'JSON-Schema' in p and 'PROMPT' in p\n"
                       "print(json.dumps({'ok': True, 'model': sys.argv[2]}))")
        res = llm.call_llm("PROMPT", cfg([cli, "--model", "{model}"]), schema={"type": "object"})
        self.assertEqual(res.data, {"ok": True, "model": "m1"})
        self.assertEqual(res.model, "fake-agent:m1")

    def test_prompt_file_and_wrapper_key(self):
        cli = fake_cli("p = open(sys.argv[1]).read()\n"
                       "print(json.dumps({'response': json.dumps({'len': len(p) > 0})}))")
        res = llm.call_llm("X", cfg([cli, "{prompt_file}"], command_output_key="response"))
        self.assertEqual(res.data, {"len": True})

    def test_json_in_chatty_output(self):
        cli = fake_cli("sys.stdin.read()\nprint('Klar, hier:\\n```json\\n{\"a\": 1}\\n```')")
        self.assertEqual(llm.call_llm("p", cfg([cli])).data, {"a": 1})

    def test_known_wrapper_detected(self):
        cli = fake_cli("sys.stdin.read()\nprint(json.dumps({'result': '{\"b\": 2}', 'usage': {}}))")
        self.assertEqual(llm.call_llm("p", cfg([cli])).data, {"b": 2})

    def test_error_does_not_echo_prompt(self):
        cli = fake_cli("p = sys.stdin.read()\nsys.stderr.write(p + '\\nError: quota exceeded\\n')\nsys.exit(3)")
        with self.assertRaises(llm.LlmUnavailableError) as ctx:
            llm.call_llm("GEHEIMER KUNDENTEXT", cfg([cli]))
        self.assertIn("quota exceeded", str(ctx.exception))
        self.assertNotIn("GEHEIMER", str(ctx.exception))

    def test_no_json_is_output_error(self):
        cli = fake_cli("sys.stdin.read()\nprint('keine Ahnung')")
        with self.assertRaises(llm.LlmOutputError):
            llm.call_llm("p", cfg([cli]))

    def test_fallback_model_same_command(self):
        cli = fake_cli("sys.stdin.read()\n"
                       "m = sys.argv[1]\n"
                       "sys.exit(1) if m == 'big' else print(json.dumps({'m': m}))")
        res = llm.call_llm("p", cfg([cli, "{model}"], model="big", fallback_model="small"))
        self.assertEqual(res.data, {"m": "small"})

    def test_codex_and_claude_untouched(self):
        cli = fake_cli("print('{}')")
        with patch.object(llm, "_run_codex_exec") as codex, patch.object(llm, "_run_claude") as claude:
            llm.call_llm("p", cfg([cli]))
        codex.assert_not_called(); claude.assert_not_called()

    def test_config_parsing_and_binaries(self):
        c = _default_config().merged_with({"runtime": "command", "command": ["gemini", "-p", "{model}"],
                                           "command_output_key": "response"})
        self.assertEqual((c.command, c.command_output_key), (["gemini", "-p", "{model}"], "response"))
        with self.assertRaises(ValueError):
            _default_config().merged_with({"command": "gemini -p"})
        self.assertEqual(llm.main_binary(c), "gemini")
        with patch("shutil.which", return_value=None):
            self.assertEqual(llm.runtime_binaries(c), {"gemini": None})


if __name__ == "__main__":
    unittest.main()
