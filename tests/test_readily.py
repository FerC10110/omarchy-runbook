"""Tests for the Readily reader: binary resolution and #runbook command mapping.

read_readily_commands() shells out to the `readily` binary through the
engine's injectable `run` (subprocess.run-compatible). These tests use a
purpose-built fake keyed on argv[1] ("where" vs "list") so each test can
script the two calls independently, matching how the engine already fakes
tmux/systemctl calls in the other test files.
"""
import hashlib
import io
import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from _load import load

engine = load()


def expected_id(title):
    """Recomputed independently of engine._readily_id, per the spec formula."""
    return hashlib.sha256(b"readily\0commands\0" + title.encode("utf-8")).hexdigest()[:32]


class FakeRun:
    """subprocess.run stand-in that answers `readily where`/`readily list` by
    argv[1], regardless of the resolved binary path in argv[0]."""

    def __init__(self, where=(0, '{"folder": "/vault", "exists": true}', ""),
                 listing=(0, '{"sections": []}', "")):
        self.calls = []
        self.where = where
        self.listing = listing

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if len(argv) > 1 and argv[1] == "where":
            code, out, err = self.where
        elif len(argv) > 1 and argv[1] == "list":
            code, out, err = self.listing
        else:
            code, out, err = (1, "", "unexpected call: " + " ".join(argv))
        return subprocess.CompletedProcess(argv, code, out, err)


GOOD_ITEM = {
    "index": 0, "kind": "text", "title": "Backup home",
    "description": "Nightly backup of home dir",
    "tags": ["runbook"], "inheritedTags": [],
    "preview": "rsync -a ~/ /backup/", "lineCount": 1,
    "search": "rsync -a ~/ /backup/",
    "image": None, "missing": False, "hash": "abc123",
}
IMAGE_ITEM = {
    "index": 1, "kind": "image", "title": "Diagram",
    "description": "", "tags": ["runbook"], "inheritedTags": [],
    "preview": "", "lineCount": 0, "search": "",
    "image": "diagram.png", "missing": False, "hash": "def456",
}
EMPTY_ITEM = {
    "index": 2, "kind": "text", "title": "Empty one",
    "description": "", "tags": ["runbook"], "inheritedTags": [],
    "preview": "", "lineCount": 0, "search": "",
    "image": None, "missing": False, "hash": "ghi789",
}
TRUNCATED_ITEM = {
    "index": 3, "kind": "text", "title": "Huge one",
    "description": "too long", "tags": ["runbook"], "inheritedTags": [],
    "preview": "x...", "lineCount": 500,
    "search": "x" * 4096,
    "image": None, "missing": False, "hash": "jkl012",
}
DOCKER_ITEM = {
    "index": 0, "kind": "text", "title": "Docker prune",
    "description": "Prune stale containers",
    "tags": ["runbook"], "inheritedTags": [],
    "preview": "docker system prune -f", "lineCount": 1,
    "search": "docker system prune -f",
    "image": None, "missing": False, "hash": "mno345",
}

LIST_PAYLOAD = json.dumps({
    "folder": "/vault",
    "tags": ["runbook"],
    "sections": [
        {"name": "commands", "error": None, "tags": ["runbook"],
         "items": [GOOD_ITEM, IMAGE_ITEM, EMPTY_ITEM, TRUNCATED_ITEM]},
        {"name": "docker", "error": None, "tags": ["runbook"],
         "items": [DOCKER_ITEM]},
    ],
})


class ReadReadilyCommandsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_bin = os.path.join(self.tmp.name, "readily")
        with open(self.fake_bin, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(self.fake_bin, 0o755)
        self.environ = {"RUNBOOK_READILY_BIN": self.fake_bin}

    def tearDown(self):
        self.tmp.cleanup()

    def test_maps_only_good_text_items_from_the_commands_section(self):
        run = FakeRun(listing=(0, LIST_PAYLOAD, ""))
        result = engine.read_readily_commands(self.environ, run)

        self.assertIsNone(result["warning"])
        self.assertEqual(result["skipped"], 3)  # image, empty, truncated
        self.assertEqual(len(result["scripts"]), 1)

        script = result["scripts"][0]
        self.assertEqual(script["name"], "Backup home")
        self.assertEqual(script["command"], "rsync -a ~/ /backup/")
        self.assertEqual(script["help"], "Nightly backup of home dir")
        self.assertEqual(script["id"], expected_id("Backup home"))
        self.assertEqual(script["source"], "readily")
        self.assertIs(script["readonly"], True)

        # The docker section's item must never appear.
        names = [s["name"] for s in result["scripts"]]
        self.assertNotIn("Docker prune", names)

        # Calls happened in the documented order: where, then list.
        self.assertEqual(run.calls[0][1:], ["where", "--json"])
        self.assertEqual(run.calls[1][1:], ["list", "--tag", "runbook", "--json"])

    def test_where_not_exists_gives_empty_result_with_warning(self):
        run = FakeRun(where=(0, '{"folder": "", "exists": false}', ""))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertIsInstance(result["warning"], str)
        self.assertTrue(result["warning"])

    def test_list_nonzero_returncode_gives_empty_result_with_warning(self):
        run = FakeRun(listing=(1, "", "boom"))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertIsInstance(result["warning"], str)
        self.assertTrue(result["warning"])

    def test_where_nonzero_returncode_gives_empty_result_with_warning(self):
        run = FakeRun(where=(1, "", "boom"))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertIsInstance(result["warning"], str)

    def test_unparseable_json_gives_empty_result_with_warning_not_raise(self):
        run = FakeRun(listing=(0, "not json", ""))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertIsInstance(result["warning"], str)

    def test_binary_unresolved_gives_empty_result_with_warning_not_raise(self):
        empty_path_dir = tempfile.mkdtemp()
        try:
            with mock.patch.object(engine, "PLUGIN_DIR", os.path.join(empty_path_dir, "plugins", "io.github.ferc10110.runbook")):
                environ = {"PATH": empty_path_dir}  # no RUNBOOK_READILY_BIN, empty PATH
                run = FakeRun()
                result = engine.read_readily_commands(environ, run)
        finally:
            os.rmdir(empty_path_dir)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertIsInstance(result["warning"], str)
        self.assertEqual(run.calls, [])  # never even tried to shell out

    def test_missing_commands_section_yields_no_scripts_without_warning(self):
        payload = json.dumps({"sections": [
            {"name": "docker", "error": None, "tags": [], "items": [DOCKER_ITEM]},
        ]})
        run = FakeRun(listing=(0, payload, ""))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertIsNone(result["warning"])


class ReadilyIdTest(unittest.TestCase):
    def test_formula_matches_sha256_of_the_namespaced_title(self):
        title = "Backup home"
        want = hashlib.sha256(b"readily\0commands\0" + title.encode("utf-8")).hexdigest()[:32]
        self.assertEqual(engine._readily_id(title), want)
        self.assertRegex(engine._readily_id(title), r"^[0-9a-f]{32}$")
        self.assertEqual(len(engine._readily_id(title)), 32)

    def test_different_titles_give_different_ids(self):
        self.assertNotEqual(engine._readily_id("A"), engine._readily_id("B"))


class ReadilyBinTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_env_override_used_when_it_exists(self):
        bin_path = os.path.join(self.tmp.name, "readily")
        with open(bin_path, "w") as fh:
            fh.write("")
        environ = {"RUNBOOK_READILY_BIN": bin_path}
        self.assertEqual(engine.readily_bin(environ), bin_path)

    def test_env_override_ignored_when_missing_falls_back(self):
        missing = os.path.join(self.tmp.name, "does-not-exist")
        plugins_dir = os.path.join(self.tmp.name, "plugins")
        runbook_dir = os.path.join(plugins_dir, "io.github.ferc10110.runbook")
        sibling_dir = os.path.join(plugins_dir, "io.github.ferc10110.readily", "bin")
        os.makedirs(sibling_dir)
        sibling_bin = os.path.join(sibling_dir, "readily")
        with open(sibling_bin, "w") as fh:
            fh.write("")
        with mock.patch.object(engine, "PLUGIN_DIR", runbook_dir):
            environ = {"RUNBOOK_READILY_BIN": missing, "PATH": ""}
            self.assertEqual(engine.readily_bin(environ), sibling_bin)

    def test_sibling_used_when_no_env_override(self):
        plugins_dir = os.path.join(self.tmp.name, "plugins")
        runbook_dir = os.path.join(plugins_dir, "io.github.ferc10110.runbook")
        sibling_dir = os.path.join(plugins_dir, "io.github.ferc10110.readily", "bin")
        os.makedirs(sibling_dir)
        sibling_bin = os.path.join(sibling_dir, "readily")
        with open(sibling_bin, "w") as fh:
            fh.write("")
        with mock.patch.object(engine, "PLUGIN_DIR", runbook_dir):
            self.assertEqual(engine.readily_bin({"PATH": ""}), sibling_bin)

    def test_none_when_unset_no_sibling_and_not_on_path(self):
        plugins_dir = os.path.join(self.tmp.name, "plugins")
        runbook_dir = os.path.join(plugins_dir, "io.github.ferc10110.runbook")
        os.makedirs(runbook_dir)  # no sibling "io.github.ferc10110.readily" here
        with mock.patch.object(engine, "PLUGIN_DIR", runbook_dir):
            environ = {"PATH": self.tmp.name}  # a PATH dir with no "readily" in it
            self.assertIsNone(engine.readily_bin(environ))


class SyncFakeRun:
    """Substitute for subprocess.run that answers systemd-analyze/systemctl
    calls, mirroring tests/test_schedule.py's FakeRun: systemd-analyze echoes
    back its argv[2] as the "Normalized form", and systemctl succeeds unless
    `available=False`. `responses` can override a specific argv tuple."""

    def __init__(self, responses=None, available=True):
        self.calls = []
        self.responses = responses or {}
        self.available = available

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        r = R()
        if argv[:2] == ["systemd-analyze", "calendar"]:
            r.stdout = "  Normalized form: " + argv[2] + "\n    Next elapse: ...\n"
        if argv[0] == "systemctl" and not self.available:
            r.returncode = 1
            r.stderr = "Failed to connect to bus"
        key = tuple(argv)
        if key in self.responses:
            r.stdout, r.returncode = self.responses[key]
        return r


class ReadilySchedulesPathTest(unittest.TestCase):
    def test_uses_xdg_config_home_when_set(self):
        path = engine.readily_schedules_path({"XDG_CONFIG_HOME": "/x/cfg", "HOME": "/h"})
        self.assertEqual(path, "/x/cfg/runbook/readily-schedules.json")

    def test_falls_back_to_home_dot_config(self):
        path = engine.readily_schedules_path({"HOME": "/h"})
        self.assertEqual(path, "/h/.config/runbook/readily-schedules.json")


class LoadSaveReadilySchedulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environ = {"XDG_CONFIG_HOME": self.tmp.name, "HOME": "/h"}
        self.path = os.path.join(self.tmp.name, "runbook", "readily-schedules.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_an_empty_dict(self):
        self.assertEqual(engine.load_readily_schedules(self.environ), {})

    def test_default_is_not_shared_mutable_state(self):
        first = engine.load_readily_schedules(self.environ)
        first["a" * 32] = {"kind": "interval", "seconds": 120}
        second = engine.load_readily_schedules(self.environ)
        self.assertEqual(second, {})

    def test_save_then_load_round_trips(self):
        sid = "a" * 32
        data = {sid: {"kind": "interval", "seconds": 120}}
        engine.save_readily_schedules(self.environ, data)
        self.assertEqual(engine.load_readily_schedules(self.environ), data)

    def test_save_creates_private_dir_and_file(self):
        engine.save_readily_schedules(self.environ, {})
        dir_mode = stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode)
        file_mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(dir_mode, 0o700)
        self.assertEqual(file_mode, 0o600)

    def test_malformed_json_repairs_to_empty_dict(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("not json")
        self.assertEqual(engine.load_readily_schedules(self.environ), {})

    def test_top_level_list_repairs_to_empty_dict(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("[]")
        self.assertEqual(engine.load_readily_schedules(self.environ), {})


class ScheduleSetDispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environ = {"HOME": "/h", "XDG_CONFIG_HOME": self.tmp.name}
        self.store_path = os.path.join(self.tmp.name, "runbook", "readily-schedules.json")
        os.makedirs(engine.unit_dir(self.environ))

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, argv, stdin_text="", run=None):
        fake = run if run is not None else SyncFakeRun()
        out = io.StringIO()
        code = engine.main(argv, environ=self.environ, run=fake, stdin=io.StringIO(stdin_text),
                           stdout=out, kill=lambda pid, sig: None, sleep=lambda s: None,
                           clock=lambda: 0.0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "exactly one JSON document")
        return code, json.loads(lines[0]), fake

    def test_interval_schedule_is_persisted_and_dispatch_syncs(self):
        sid = "a" * 32
        code, payload, fake = self.cli(["schedule-set", sid],
                                       stdin_text='{"kind":"interval","seconds":120}')
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(engine.load_readily_schedules(self.environ),
                         {sid: {"kind": "interval", "seconds": 120}})
        # native sync always daemon-reloads first, even though this id is
        # absent from scripts.json (Task 5 wires the side-store into sync).
        self.assertTrue(any(c[:3] == ["systemctl", "--user", "daemon-reload"] for c in fake.calls))
        # and it must NOT enable a runbook-<id>.timer for a Readily-only id.
        self.assertFalse(any("enable" in c and ("runbook-" + sid + ".timer") in c for c in fake.calls))

    def test_calendar_schedule_is_normalized_via_systemd_analyze(self):
        sid = "b" * 32
        expr = "*-*-* 08:00:00"
        code, payload, fake = self.cli(["schedule-set", sid],
                                       stdin_text=json.dumps({"kind": "calendar", "oncalendar": expr}))
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(engine.load_readily_schedules(self.environ),
                         {sid: {"kind": "calendar", "oncalendar": expr}})
        self.assertTrue(any(c[:2] == ["systemd-analyze", "calendar"] for c in fake.calls))

    def test_null_stdin_clears_an_existing_schedule(self):
        sid = "c" * 32
        self.cli(["schedule-set", sid], stdin_text='{"kind":"interval","seconds":120}')
        self.assertIn(sid, engine.load_readily_schedules(self.environ))

        code, payload, fake = self.cli(["schedule-set", sid], stdin_text="null")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertNotIn(sid, engine.load_readily_schedules(self.environ))

    def test_schedule_below_the_floor_is_rejected_and_not_written(self):
        sid = "d" * 32
        code, payload, fake = self.cli(["schedule-set", sid],
                                       stdin_text='{"kind":"interval","seconds":5}')
        self.assertEqual(code, 1)
        self.assertIn("error", payload)
        self.assertFalse(os.path.exists(self.store_path))

    def test_unknown_kind_is_rejected_and_leaves_the_store_unchanged(self):
        sid = "e" * 32
        self.cli(["schedule-set", sid], stdin_text='{"kind":"interval","seconds":120}')
        before = engine.load_readily_schedules(self.environ)

        code, payload, fake = self.cli(["schedule-set", sid], stdin_text='{"kind":"nope"}')
        self.assertEqual(code, 1)
        self.assertIn("error", payload)
        self.assertEqual(engine.load_readily_schedules(self.environ), before)

    def test_side_store_file_mode_is_0600_dir_0700(self):
        sid = "f" * 32
        self.cli(["schedule-set", sid], stdin_text='{"kind":"interval","seconds":120}')
        dir_mode = stat.S_IMODE(os.stat(os.path.dirname(self.store_path)).st_mode)
        file_mode = stat.S_IMODE(os.stat(self.store_path).st_mode)
        self.assertEqual(dir_mode, 0o700)
        self.assertEqual(file_mode, 0o600)


if __name__ == "__main__":
    unittest.main()
