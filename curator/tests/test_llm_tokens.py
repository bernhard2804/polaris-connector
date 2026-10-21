import unittest

import conftest_helpers  # noqa: F401 - setzt sys.path

from polaris_curator.llm import _parse_tokens_used

HEADER = "OpenAI Codex v0.156.1\n--------\nsession id: abc-123\n--------\nuser\nPrompt\n\ncodex\n{}\n"


class TestTokenParsing(unittest.TestCase):
    def test_regular_codex_output(self):
        self.assertEqual(_parse_tokens_used(HEADER + "tokens used\n4,018\n"), 4018)

    def test_missing_summary(self):
        self.assertIsNone(_parse_tokens_used(HEADER))

    def test_multiple_summaries_use_last_and_warn(self):
        with self.assertLogs("polaris_curator", level="WARNING") as logs:
            value = _parse_tokens_used(HEADER + "tokens used\n3,107\n...\ntokens used\n98,765\n")
        self.assertEqual(value, 98765)
        self.assertIn("abc-123", logs.output[0])
        self.assertNotIn("Prompt", logs.output[0])


class TestReasoningEffortArgument(unittest.TestCase):
    def _cmd(self, **cfg_kwargs):
        from unittest.mock import patch
        from polaris_curator import llm
        from polaris_curator.config import CuratorConfig

        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            raise OSError("stop")

        with patch.object(llm.subprocess, "run", fake_run):
            with self.assertRaises(llm.LlmUnavailableError):
                llm._run_codex_exec("p", "m", CuratorConfig(**cfg_kwargs))
        return captured["cmd"]

    def test_not_set_by_default(self):
        self.assertNotIn("-c", self._cmd())

    def test_passed_when_configured(self):
        cmd = self._cmd(reasoning_effort="medium")
        self.assertIn('model_reasoning_effort="medium"', cmd)

    def test_config_override(self):
        from polaris_curator.config import CuratorConfig
        self.assertEqual(CuratorConfig().merged_with({"reasoning_effort": "high"}).reasoning_effort, "high")
        self.assertIsNone(CuratorConfig().merged_with({"reasoning_effort": None}).reasoning_effort)


if __name__ == "__main__":
    unittest.main()
