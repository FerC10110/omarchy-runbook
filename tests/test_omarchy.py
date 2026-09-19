import io
import json
import os
import subprocess
import tempfile
import unittest

from _load import load
from fakes import FakeRun

engine = load()

ALIVE = (0, "0\t\t\t4242\n", "")
GONE = (1, "", "can't find session: x\n")
OK = (0, "", "")

THEME_SET = {
    "route": "omarchy theme set", "group": "theme", "name": "set",
    "summary": "Apply an Omarchy theme", "requires_sudo": False, "hidden": False,
    "args": "<theme-name>", "examples": ["omarchy theme set \"Tokyo Night\""],
}
SNAPSHOT = {
    "route": "omarchy snapshot", "group": "snapshot", "name": "",
    "summary": "Create or restore system snapshots with snapper", "requires_sudo": True,
    "hidden": False, "args": "<create|restore>", "examples": [],
}
HIDDEN = {"route": "omarchy migrate run", "summary": "Internal", "hidden": True}
BAD_ROUTE = {"route": "omarchy x; rm -rf ~", "summary": "Not a plain route", "hidden": False}
NOT_OMARCHY = {"route": "sudo reboot", "summary": "Not an omarchy route", "hidden": False}


def listing(*items):
    return (0, json.dumps({"ok": True, "commands": list(items)}), "")


class ReadOmarchyTest(unittest.TestCase):
    def read(self, *answers):
        fake = FakeRun(answers)
        return engine.read_omarchy_commands({"PATH": "/usr/bin"}, fake), fake

    def test_asks_omarchy_for_its_command_list(self):
        _, fake = self.read(listing())
        self.assertEqual(fake.calls, [["omarchy", "commands", "--json"]])

    def test_keeps_visible_commands_sorted_by_route(self):
        result, _ = self.read(listing(THEME_SET, SNAPSHOT))
        self.assertTrue(result["ok"])
        self.assertIsNone(result["warning"])
        self.assertEqual([c["route"] for c in result["commands"]],
                         ["omarchy snapshot", "omarchy theme set"])

    def test_entry_shape(self):
        result, _ = self.read(listing(THEME_SET))
        entry = result["commands"][0]
        self.assertEqual(entry, {
            "id": engine._omarchy_id("omarchy theme set"),
            "name": "theme set",
            "route": "omarchy theme set",
            "summary": "Apply an Omarchy theme",
            "args": "<theme-name>",
            "examples": ["omarchy theme set \"Tokyo Night\""],
            "sudo": False,
            "source": "omarchy",
        })
        self.assertRegex(entry["id"], r"^[0-9a-f]{32}$")

    def test_sudo_flag(self):
        result, _ = self.read(listing(SNAPSHOT))
        self.assertTrue(result["commands"][0]["sudo"])

    def test_drops_hidden_and_unsafe_routes(self):
        result, _ = self.read(listing(HIDDEN, BAD_ROUTE, NOT_OMARCHY, THEME_SET))
        self.assertEqual([c["route"] for c in result["commands"]], ["omarchy theme set"])

    def test_drops_duplicate_routes(self):
        result, _ = self.read(listing(THEME_SET, THEME_SET))
        self.assertEqual(len(result["commands"]), 1)

    def test_bare_omarchy_route_is_named_after_itself(self):
        result, _ = self.read(listing({"route": "omarchy", "summary": "Menu"}))
        self.assertEqual(result["commands"][0]["name"], "omarchy")

    def test_missing_optional_fields_become_empty(self):
        result, _ = self.read(listing({"route": "omarchy update", "examples": "not a list"}))
        entry = result["commands"][0]
        self.assertEqual((entry["summary"], entry["args"], entry["examples"], entry["sudo"]),
                         ("", "", [], False))

    def test_id_is_stable_and_depends_only_on_the_route(self):
        self.assertEqual(engine._omarchy_id("omarchy theme set"), engine._omarchy_id("omarchy theme set"))
        self.assertNotEqual(engine._omarchy_id("omarchy theme set"), engine._omarchy_id("omarchy theme list"))

    def test_failed_listing_degrades_to_a_warning(self):
        result, _ = self.read((1, "", "unknown command"))
        self.assertEqual((result["ok"], result["commands"]), (False, []))
        self.assertIn("omarchy commands --json", result["warning"])

    def test_unparseable_listing_degrades_to_a_warning(self):
        for out in ("not json", "[]", json.dumps({"commands": "nope"})):
            result, _ = self.read((0, out, ""))
            self.assertEqual((result["ok"], result["commands"]), (False, []), out)
            self.assertTrue(result["warning"], out)

    def test_missing_binary_degrades_to_a_warning(self):
        def run(argv, **kwargs):
            raise FileNotFoundError(argv[0])
        result = engine.read_omarchy_commands({}, run)
        self.assertEqual((result["ok"], result["warning"]), (False, "The omarchy command was not found"))

    def test_timeout_degrades_to_a_warning(self):
        def run(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        result = engine.read_omarchy_commands({}, run)
        self.assertFalse(result["ok"])
        self.assertIn("did not answer", result["warning"])


class OmarchyArgsTest(unittest.TestCase):
    def test_trims_and_accepts_empty(self):
        self.assertEqual(engine.validate_omarchy_args({}), "")
        self.assertEqual(engine.validate_omarchy_args({"args": None}), "")
        self.assertEqual(engine.validate_omarchy_args({"args": "  create "}), "create")

    def test_rejects_non_text(self):
        with self.assertRaises(engine.RunbookError):
            engine.validate_omarchy_args({"args": 3})

    def test_rejects_more_than_one_line(self):
        with self.assertRaisesRegex(engine.RunbookError, "one line"):
            engine.validate_omarchy_args({"args": "a\nb"})

    def test_rejects_long_arguments(self):
        with self.assertRaisesRegex(engine.RunbookError, "at most"):
            engine.validate_omarchy_args({"args": "x" * (engine.ARGS_MAX + 1)})

    def test_command_line(self):
        entry = {"route": "omarchy theme set"}
        self.assertEqual(engine.omarchy_command_line(entry, ""), "omarchy theme set")
        self.assertEqual(engine.omarchy_command_line(entry, "\"Tokyo Night\""),
                         "omarchy theme set \"Tokyo Night\"")


class OmarchyCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environ = {"HOME": "/h", "PATH": "/usr/bin", "XDG_CONFIG_HOME": self.tmp.name,
                        "RUNBOOK_TMUX_SOCKET": "sock", "RUNBOOK_TMUX_CONF": "/plug/tmux.conf"}
        self.theme_id = engine._omarchy_id("omarchy theme set")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, argv, stdin_text="", answers=()):
        fake = FakeRun(answers)
        out = io.StringIO()
        code = engine.main(argv, environ=self.environ, run=fake, stdin=io.StringIO(stdin_text),
                           stdout=out, kill=lambda pid, sig: None, sleep=lambda s: None,
                           clock=lambda: 0.0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "exactly one JSON document")
        return code, json.loads(lines[0]), fake

    def test_omarchy_lists_the_commands(self):
        code, payload, _ = self.cli(["omarchy"], answers=[listing(THEME_SET)])
        self.assertEqual(code, 0)
        self.assertEqual([c["route"] for c in payload["commands"]], ["omarchy theme set"])
        self.assertIsNone(payload["warning"])

    def test_omarchy_reports_a_failure_as_a_warning_not_an_error(self):
        code, payload, _ = self.cli(["omarchy"], answers=[(1, "", "")])
        self.assertEqual(code, 0)
        self.assertEqual(payload["commands"], [])
        self.assertTrue(payload["warning"])

    def test_run_starts_the_route_with_its_arguments(self):
        code, payload, fake = self.cli(
            ["omarchy-run", self.theme_id, "--cols", "100", "--rows", "30"],
            stdin_text=json.dumps({"args": "\"Tokyo Night\""}),
            answers=[listing(THEME_SET), GONE, OK])
        self.assertEqual((code, payload), (0, {"ok": True, "already": False}))
        argv = fake.tails()[1]
        self.assertEqual(argv[:9], ["new-session", "-d", "-s", self.theme_id, "-x", "100", "-y", "30", "-c"])
        self.assertIn("RUNBOOK_COMMAND=omarchy theme set \"Tokyo Night\"", argv)

    def test_run_without_arguments_runs_the_bare_route(self):
        _, _, fake = self.cli(["omarchy-run", self.theme_id], stdin_text="{}",
                              answers=[listing(THEME_SET), GONE, OK])
        self.assertIn("RUNBOOK_COMMAND=omarchy theme set", fake.tails()[1])

    def test_run_on_a_live_session_does_nothing(self):
        code, payload, fake = self.cli(["omarchy-run", self.theme_id], stdin_text="{}",
                                       answers=[listing(THEME_SET), ALIVE])
        self.assertEqual((code, payload), (0, {"ok": True, "already": True}))
        self.assertEqual(len(fake.tails()), 1)

    def test_run_unknown_command(self):
        code, payload, _ = self.cli(["omarchy-run", "f" * 32], stdin_text="{}",
                                    answers=[listing(THEME_SET)])
        self.assertEqual((code, payload), (1, {"error": "No such Omarchy command"}))

    def test_run_rejects_a_bad_id_before_calling_anything(self):
        code, payload, fake = self.cli(["omarchy-run", "bad-id"], stdin_text="{}")
        self.assertEqual((code, payload), (1, {"error": "Invalid script id"}))
        self.assertEqual(fake.calls, [])

    def test_run_rejects_bad_arguments_before_calling_anything(self):
        code, payload, fake = self.cli(["omarchy-run", self.theme_id],
                                       stdin_text=json.dumps({"args": "a\nb"}))
        self.assertEqual(code, 1)
        self.assertIn("one line", payload["error"])
        self.assertEqual(fake.calls, [])

    def test_run_needs_a_json_object_on_stdin(self):
        code, payload, fake = self.cli(["omarchy-run", self.theme_id], stdin_text="")
        self.assertEqual((code, payload), (1, {"error": "Expected a JSON object on stdin"}))
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
