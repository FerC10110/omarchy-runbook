import io
import json
import unittest

from _load import load

engine = load()


def run_cli(argv, stdin_text="", environ=None, run=None):
    out = io.StringIO()
    code = engine.main(argv, environ=environ or {}, run=run,
                       stdin=io.StringIO(stdin_text), stdout=out)
    return code, json.loads(out.getvalue())


class UnknownCommandTest(unittest.TestCase):
    def test_unknown_command_is_a_json_error_with_exit_1(self):
        code, payload = run_cli(["bogus"])
        self.assertEqual(code, 1)
        self.assertEqual(payload, {"error": "Unknown command: bogus"})

    def test_missing_command_is_a_json_error(self):
        code, payload = run_cli([])
        self.assertEqual(code, 1)
        self.assertEqual(payload["error"], "Usage: runbook <command> [id] [--cols N --rows N]")
