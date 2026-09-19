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
import time
import unittest
from unittest import mock

from _load import load

engine = load()


def expected_id(title, section="commands"):
    """Recomputed independently of engine._readily_id, per the spec formula.
    section defaults to "commands", whose namespace equals the original formula."""
    return hashlib.sha256(
        b"readily\0" + section.encode("utf-8") + b"\0" + title.encode("utf-8")).hexdigest()[:32]


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


class CombinedFakeRun:
    """subprocess.run stand-in for dispatch-level tests that exercise both
    the readily binary (where/list, answered like FakeRun above) and tmux
    (argv[0] == "tmux", answered in order like tests/fakes.FakeRun)."""

    def __init__(self, where=(0, '{"folder": "/vault", "exists": true}', ""),
                 listing=(0, '{"sections": []}', ""), tmux_answers=()):
        self.calls = []
        self.where = where
        self.listing = listing
        self.tmux_answers = list(tmux_answers)

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[0] == "tmux":
            code, out, err = self.tmux_answers.pop(0) if self.tmux_answers else (0, "", "")
            return subprocess.CompletedProcess(argv, code, out, err)
        if len(argv) > 1 and argv[1] == "where":
            code, out, err = self.where
        elif len(argv) > 1 and argv[1] == "list":
            code, out, err = self.listing
        else:
            code, out, err = (1, "", "unexpected call: " + " ".join(argv))
        return subprocess.CompletedProcess(argv, code, out, err)

    def tails(self):
        return [call[5:] for call in self.calls if call[:1] == ["tmux"]]


GONE = (1, "", "can't find session: x\n")
OK = (0, "", "")


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
NULL_DESCRIPTION_ITEM = {
    "index": 4, "kind": "text", "title": "No description",
    "description": None,
    "tags": ["runbook"], "inheritedTags": [],
    "preview": "echo hi", "lineCount": 1,
    "search": "echo hi",
    "image": None, "missing": False, "hash": "pqr678",
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

    def test_maps_good_text_items_from_all_notes(self):
        run = FakeRun(listing=(0, LIST_PAYLOAD, ""))
        result = engine.read_readily_commands(self.environ, run)

        self.assertIsNone(result["warning"])
        self.assertEqual(result["skipped"], 3)  # image, empty, truncated (all in "commands")
        self.assertTrue(result["ok"])  # a genuine, successful read

        by_name = {s["name"]: s for s in result["scripts"]}
        # The "commands" note item, with its original (back-compatible) id...
        self.assertIn("Backup home", by_name)
        self.assertEqual(by_name["Backup home"]["command"], "rsync -a ~/ /backup/")
        self.assertEqual(by_name["Backup home"]["help"], "Nightly backup of home dir")
        self.assertEqual(by_name["Backup home"]["id"], expected_id("Backup home"))
        self.assertEqual(by_name["Backup home"]["source"], "readily")
        self.assertIs(by_name["Backup home"]["readonly"], True)
        # ...and the item from the OTHER note ("docker") is now included too,
        # with a section-namespaced id.
        self.assertIn("Docker prune", by_name)
        self.assertEqual(by_name["Docker prune"]["command"], "docker system prune -f")
        self.assertEqual(by_name["Docker prune"]["id"], expected_id("Docker prune", "docker"))

        # Calls happened in the documented order: where, then list.
        self.assertEqual(run.calls[0][1:], ["where", "--json"])
        self.assertEqual(run.calls[1][1:], ["list", "--tag", "runbook", "--json"])

    def test_null_description_maps_to_empty_string_help(self):
        payload = json.dumps({"sections": [
            {"name": "commands", "error": None, "tags": ["runbook"],
             "items": [NULL_DESCRIPTION_ITEM]},
        ]})
        run = FakeRun(listing=(0, payload, ""))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(len(result["scripts"]), 1)
        self.assertEqual(result["scripts"][0]["help"], "")
        self.assertIsInstance(result["scripts"][0]["help"], str)

    def test_where_not_exists_gives_empty_result_with_warning(self):
        run = FakeRun(where=(0, '{"folder": "", "exists": false}', ""))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertIsInstance(result["warning"], str)
        self.assertTrue(result["warning"])
        self.assertFalse(result["ok"])  # unconfigured, not "zero commands"

    def test_list_nonzero_returncode_gives_empty_result_with_warning(self):
        run = FakeRun(listing=(1, "", "boom"))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertIsInstance(result["warning"], str)
        self.assertTrue(result["warning"])
        self.assertFalse(result["ok"])  # transient failure, not "zero commands"

    def test_where_nonzero_returncode_gives_empty_result_with_warning(self):
        run = FakeRun(where=(1, "", "boom"))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertIsInstance(result["warning"], str)
        self.assertFalse(result["ok"])

    def test_unparseable_json_gives_empty_result_with_warning_not_raise(self):
        run = FakeRun(listing=(0, "not json", ""))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertIsInstance(result["warning"], str)
        self.assertFalse(result["ok"])

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
        self.assertFalse(result["ok"])

    def test_items_from_a_non_commands_note_are_included(self):
        payload = json.dumps({"sections": [
            {"name": "docker", "error": None, "tags": ["runbook"], "items": [DOCKER_ITEM]},
        ]})
        run = FakeRun(listing=(0, payload, ""))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(len(result["scripts"]), 1)
        self.assertEqual(result["scripts"][0]["name"], "Docker prune")
        self.assertEqual(result["scripts"][0]["id"], expected_id("Docker prune", "docker"))
        self.assertIsNone(result["warning"])
        self.assertTrue(result["ok"])

    def test_no_runbook_items_in_any_note_gives_a_gentle_warning(self):
        payload = json.dumps({"sections": [
            {"name": "commands", "error": None, "tags": [], "items": []},
            {"name": "docker", "error": None, "tags": [], "items": []},
        ]})
        run = FakeRun(listing=(0, payload, ""))
        result = engine.read_readily_commands(self.environ, run)
        self.assertEqual(result["scripts"], [])
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(result["warning"], "No #runbook items found in Readily")
        self.assertTrue(result["ok"])  # a genuine read that found nothing


class ReadilyIdTest(unittest.TestCase):
    def test_formula_matches_sha256_of_the_namespaced_section_and_title(self):
        section, title = "commands", "Backup home"
        want = hashlib.sha256(
            b"readily\0" + section.encode("utf-8") + b"\0" + title.encode("utf-8")).hexdigest()[:32]
        self.assertEqual(engine._readily_id(section, title), want)
        self.assertRegex(engine._readily_id(section, title), r"^[0-9a-f]{32}$")
        self.assertEqual(len(engine._readily_id(section, title)), 32)

    def test_commands_section_id_is_unchanged_from_the_original_formula(self):
        # Back-compat: the "commands" note keeps the ids it had before Runbook
        # read other notes, so schedules created earlier never orphan.
        title = "Backup home"
        legacy = hashlib.sha256(b"readily\0commands\0" + title.encode("utf-8")).hexdigest()[:32]
        self.assertEqual(engine._readily_id("commands", title), legacy)

    def test_different_titles_give_different_ids(self):
        self.assertNotEqual(engine._readily_id("commands", "A"), engine._readily_id("commands", "B"))

    def test_same_title_in_different_notes_gives_different_ids(self):
        self.assertNotEqual(engine._readily_id("commands", "Deploy"),
                            engine._readily_id("docker", "Deploy"))


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

    def _write_raw(self, obj):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(obj, fh)

    def test_drops_a_non_id_key_but_keeps_a_valid_entry(self):
        """Fix round 2: the side-store is a user-editable file. A hand-edited
        key that isn't 32-hex must never reach _unit_base -- that's a
        path-traversal unit write waiting to happen."""
        valid = "a" * 32
        self._write_raw({
            "../../evil": {"kind": "interval", "seconds": 120},
            "xyz": {"kind": "interval", "seconds": 120},
            valid: {"kind": "interval", "seconds": 300},
        })
        self.assertEqual(engine.load_readily_schedules(self.environ),
                         {valid: {"kind": "interval", "seconds": 300}})

    def test_drops_malformed_or_null_values_but_keeps_valid_entries(self):
        """Fix round 2: a malformed value must never reach render_timer --
        that's an uncaught KeyError crashing sync_schedules."""
        valid = "a" * 32
        self._write_raw({
            valid: {"kind": "interval", "seconds": 300},
            "b" * 32: {},                        # no "kind" at all
            "c" * 32: {"kind": "interval"},       # interval missing "seconds"
            "d" * 32: {"kind": "bogus"},          # unknown kind
            "e" * 32: None,                       # "no schedule": has no business persisting
        })
        self.assertEqual(engine.load_readily_schedules(self.environ),
                         {valid: {"kind": "interval", "seconds": 300}})


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


class ResolveReadilyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_bin = os.path.join(self.tmp.name, "readily")
        with open(self.fake_bin, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(self.fake_bin, 0o755)
        self.environ = {"RUNBOOK_READILY_BIN": self.fake_bin}

    def tearDown(self):
        self.tmp.cleanup()

    def test_match_returns_the_mapped_script(self):
        run = FakeRun(listing=(0, LIST_PAYLOAD, ""))
        script = engine.resolve_readily(expected_id("Backup home"), self.environ, run)
        self.assertIsNotNone(script)
        self.assertEqual(script["name"], "Backup home")
        self.assertEqual(script["command"], "rsync -a ~/ /backup/")
        self.assertEqual(script["source"], "readily")

    def test_miss_returns_none(self):
        run = FakeRun(listing=(0, LIST_PAYLOAD, ""))
        script = engine.resolve_readily("f" * 32, self.environ, run)
        self.assertIsNone(script)


class ReadilyDispatchCase(unittest.TestCase):
    """Base for list/run dispatch tests that need both a resolvable readily
    binary (env override) and a config/side-store on disk under XDG_CONFIG_HOME."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_bin = os.path.join(self.tmp.name, "readily")
        with open(self.fake_bin, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(self.fake_bin, 0o755)
        self.environ = {"HOME": "/h", "PATH": "/usr/bin", "XDG_CONFIG_HOME": self.tmp.name,
                        "RUNBOOK_TMUX_SOCKET": "sock", "RUNBOOK_TMUX_CONF": "/plug/tmux.conf",
                        "RUNBOOK_READILY_BIN": self.fake_bin}

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, argv, run, stdin_text=""):
        out = io.StringIO()
        code = engine.main(argv, environ=self.environ, run=run, stdin=io.StringIO(stdin_text),
                           stdout=out, kill=lambda pid, sig: None, sleep=lambda s: None,
                           clock=lambda: 0.0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "exactly one JSON document")
        return code, json.loads(lines[0])


class ListDispatchReadilyMergeTest(ReadilyDispatchCase):
    def test_list_merges_readily_scripts_when_enabled(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        run = CombinedFakeRun(listing=(0, LIST_PAYLOAD, ""))

        code, payload = self.cli(["list"], run)

        self.assertEqual(code, 0)
        names = {s["name"]: s for s in payload["scripts"]}
        self.assertIn("Backup home", names)
        readily_script = names["Backup home"]
        self.assertEqual(readily_script["id"], rid)
        self.assertEqual(readily_script["source"], "readily")
        self.assertIs(readily_script["readonly"], True)
        self.assertEqual(readily_script["schedule"], {"kind": "interval", "seconds": 120})
        self.assertEqual(payload["readily_skipped"], 3)  # image, empty, truncated
        self.assertNotIn("readily_warning", payload)

    def test_list_merged_script_has_no_schedule_key_when_side_store_is_empty(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        run = CombinedFakeRun(listing=(0, LIST_PAYLOAD, ""))
        code, payload = self.cli(["list"], run)
        readily_script = next(s for s in payload["scripts"] if s["name"] == "Backup home")
        self.assertNotIn("schedule", readily_script)

    def test_list_surfaces_readily_warning_and_omits_skipped_when_zero(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        run = CombinedFakeRun(where=(1, "", "boom"))
        code, payload = self.cli(["list"], run)
        self.assertEqual(payload["scripts"], [])
        self.assertIn("readily_warning", payload)
        self.assertIsInstance(payload["readily_warning"], str)
        self.assertNotIn("readily_skipped", payload)

    def test_list_is_native_only_and_makes_no_calls_when_disabled(self):
        # readily.enabled defaults to False: no config.json has been written.
        run = CombinedFakeRun(listing=(0, LIST_PAYLOAD, ""))
        code, payload = self.cli(["list"], run)
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"version": 1, "view": {"width": 960, "height": 540},
                          "tabs": [{"id": "main", "name": "Mine"}], "scripts": []})
        self.assertEqual(run.calls, [])


class RunDispatchReadilyResolutionTest(ReadilyDispatchCase):
    def test_run_resolves_and_launches_a_readily_script_when_enabled(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        run = CombinedFakeRun(listing=(0, LIST_PAYLOAD, ""), tmux_answers=[GONE, OK])

        code, payload = self.cli(["run", rid, "--cols", "80", "--rows", "24"], run)

        self.assertEqual((code, payload), (0, {"ok": True, "already": False}))
        argv = run.tails()[1]
        self.assertEqual(argv[:9], ["new-session", "-d", "-s", rid, "-x", "80", "-y", "24", "-c"])
        self.assertIn("RUNBOOK_COMMAND=rsync -a ~/ /backup/", argv)

    def test_run_unknown_or_renamed_readily_id_is_an_error(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        run = CombinedFakeRun(listing=(0, LIST_PAYLOAD, ""))
        code, payload = self.cli(["run", "f" * 32], run)
        self.assertEqual((code, payload), (1, {"error": "No such script"}))

    def test_run_readily_id_with_flag_disabled_is_an_error_and_makes_no_calls(self):
        rid = expected_id("Backup home")
        run = CombinedFakeRun(listing=(0, LIST_PAYLOAD, ""))
        code, payload = self.cli(["run", rid], run)
        self.assertEqual((code, payload), (1, {"error": "No such script"}))
        self.assertEqual(run.calls, [])


class ScheduleSyncFakeRun:
    """Answers both systemctl/systemd-analyze calls (as tests/test_schedule.py's
    FakeRun) and the readily binary's where/list calls (as FakeRun above), for
    sync_schedules/schedules_report tests that exercise the Readily
    side-store union. Routed by argv[0]: "systemctl"/"systemd-analyze" go to
    the systemd side, anything else (the resolved readily binary path) goes
    to the where/list side, keyed on argv[1]."""

    def __init__(self, where=(0, '{"folder": "/vault", "exists": true}', ""),
                 listing=(0, '{"sections": []}', ""), responses=None, available=True):
        self.calls = []
        self.where = where
        self.listing = listing
        self.responses = responses or {}
        self.available = available

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if argv[0] not in ("systemctl", "systemd-analyze"):
            if len(argv) > 1 and argv[1] == "where":
                code, out, err = self.where
            elif len(argv) > 1 and argv[1] == "list":
                code, out, err = self.listing
            else:
                code, out, err = (1, "", "unexpected call: " + " ".join(argv))
            return subprocess.CompletedProcess(argv, code, out, err)

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


class SyncSchedulesReadilySideStoreTest(unittest.TestCase):
    """Task 5: sync_schedules unions the Readily side-store into the
    scheduled set (config-gated), prunes orphaned side entries whose Readily
    command is gone, and leaves the store untouched (but its timers swept)
    when the toggle is disabled."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_bin = os.path.join(self.tmp.name, "readily")
        with open(self.fake_bin, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(self.fake_bin, 0o755)
        self.environ = {"HOME": "/h", "XDG_CONFIG_HOME": self.tmp.name,
                        "RUNBOOK_READILY_BIN": self.fake_bin}
        os.makedirs(engine.unit_dir(self.environ))

    def tearDown(self):
        self.tmp.cleanup()

    def lib(self, scripts=()):
        return {"version": 1, "view": {"width": 960, "height": 540}, "scripts": list(scripts)}

    def timer_path(self, sid):
        return os.path.join(engine.unit_dir(self.environ), "runbook-" + sid + ".timer")

    def touch_unit(self, sid):
        d = engine.unit_dir(self.environ)
        for ext in ("timer", "service"):
            open(os.path.join(d, "runbook-" + sid + "." + ext), "w").close()

    def test_enabled_side_entry_in_readily_set_is_scheduled(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))

        out = engine.sync_schedules(self.lib(), self.environ, run)

        self.assertEqual(out, {})
        self.assertTrue(os.path.exists(self.timer_path(rid)))
        self.assertTrue(any("enable" in c and ("runbook-" + rid + ".timer") in c for c in run.calls))

    def test_native_and_readily_both_scheduled_both_timers_enabled(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        native_sid = "n" * 32
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))

        out = engine.sync_schedules(
            self.lib([{"id": native_sid, "name": "n", "command": "c", "help": "",
                      "schedule": {"kind": "interval", "seconds": 600}}]),
            self.environ, run)

        self.assertEqual(out, {})
        self.assertTrue(os.path.exists(self.timer_path(rid)))
        self.assertTrue(os.path.exists(self.timer_path(native_sid)))
        self.assertTrue(any("enable" in c and ("runbook-" + rid + ".timer") in c for c in run.calls))
        self.assertTrue(any("enable" in c and ("runbook-" + native_sid + ".timer") in c for c in run.calls))

    def test_native_schedule_is_not_overridden_by_same_id_side_entry(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 999}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))

        out = engine.sync_schedules(
            self.lib([{"id": rid, "name": "n", "command": "c", "help": "",
                      "schedule": {"kind": "interval", "seconds": 600}}]),
            self.environ, run)

        self.assertEqual(out, {})
        with open(self.timer_path(rid), encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("OnUnitActiveSec=600", content)
        self.assertNotIn("OnUnitActiveSec=999", content)
        enable_calls = [c for c in run.calls if "enable" in c and ("runbook-" + rid + ".timer") in c]
        self.assertEqual(len(enable_calls), 1)

    def test_enabled_side_entry_not_in_readily_set_is_pruned_and_swept(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        stale_rid = "f" * 32
        self.touch_unit(stale_rid)
        engine.save_readily_schedules(self.environ, {stale_rid: {"kind": "interval", "seconds": 120}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))

        out = engine.sync_schedules(self.lib(), self.environ, run)

        self.assertEqual(out, {})
        self.assertFalse(os.path.exists(self.timer_path(stale_rid)))
        self.assertTrue(any("disable" in c and ("runbook-" + stale_rid + ".timer") in c for c in run.calls))
        self.assertNotIn(stale_rid, engine.load_readily_schedules(self.environ))

    def test_disabled_side_entry_timer_swept_but_store_retained(self):
        # readily.enabled left at its default (False): no config.json saved.
        rid = expected_id("Backup home")
        self.touch_unit(rid)
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        native_sid = "n" * 32
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))

        out = engine.sync_schedules(
            self.lib([{"id": native_sid, "name": "n", "command": "c", "help": "",
                      "schedule": {"kind": "interval", "seconds": 600}}]),
            self.environ, run)

        self.assertEqual(out, {})
        self.assertFalse(os.path.exists(self.timer_path(rid)))
        self.assertTrue(any("disable" in c and ("runbook-" + rid + ".timer") in c for c in run.calls))
        self.assertTrue(os.path.exists(self.timer_path(native_sid)))
        self.assertTrue(any("enable" in c and ("runbook-" + native_sid + ".timer") in c for c in run.calls))
        self.assertIn(rid, engine.load_readily_schedules(self.environ))

    def test_bus_unavailable_still_returns_warning_with_union_in_place(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""), available=False)

        out = engine.sync_schedules(self.lib(), self.environ, run)

        self.assertIn("schedule_warning", out)

    def test_readily_read_failure_preserves_side_store_and_schedules_all_entries(self):
        """Fix round 1 / data-loss bug: read_readily_commands degrading to an
        empty scripts list on a TRANSIENT failure (Readily momentarily
        unreachable) must never be mistaken for "these ids are gone" -- that
        would silently prune and lose every Readily schedule the user set.
        ok: False means "couldn't check": preserve the store and keep
        scheduling every entry, don't treat it as an orphan sweep."""
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid_a, rid_b = "a" * 32, "b" * 32
        side = {rid_a: {"kind": "interval", "seconds": 120},
                rid_b: {"kind": "interval", "seconds": 300}}
        engine.save_readily_schedules(self.environ, side)
        run = ScheduleSyncFakeRun(listing=(1, "", "boom"))  # `readily list` fails -> ok: False

        out = engine.sync_schedules(self.lib(), self.environ, run)

        self.assertEqual(out, {})
        self.assertEqual(engine.load_readily_schedules(self.environ), side)  # nothing pruned
        self.assertTrue(os.path.exists(self.timer_path(rid_a)))
        self.assertTrue(os.path.exists(self.timer_path(rid_b)))
        self.assertTrue(any("enable" in c and ("runbook-" + rid_a + ".timer") in c for c in run.calls))
        self.assertTrue(any("enable" in c and ("runbook-" + rid_b + ".timer") in c for c in run.calls))

    def test_tampered_store_does_not_traverse_or_raise(self):
        """Fix round 2 / anti-traversal + anti-crash guard: a hand-edited
        side-store with a bad key (path-traversal-shaped) and a malformed
        value must not make sync_schedules write outside unit_dir, and must
        not raise -- only the well-formed entry gets scheduled.

        Uses a failing Readily read (ok: False) on purpose: under ok: True
        the round-1 orphan-prune already happens to drop any key that isn't
        a currently-live Readily id (garbage never matches), which would
        mask this load-time validation bug. The ok: False "preserve
        everything" branch iterates every raw side entry unconditionally,
        which is exactly where an unvalidated key/value would reach
        render_service/render_timer/_atomic_write."""
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        good_rid = "a" * 32
        bad_value_id = "f" * 32
        path = engine.readily_schedules_path(self.environ)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "../../evil": {"kind": "interval", "seconds": 120},  # bad key
                bad_value_id: {},                                    # bad value (no "kind")
                good_rid: {"kind": "interval", "seconds": 300},      # well-formed
            }, fh)
        run = ScheduleSyncFakeRun(listing=(1, "", "boom"))  # readily list fails -> ok: False

        out = engine.sync_schedules(self.lib(), self.environ, run)  # must not raise

        self.assertEqual(out, {})
        # No traversal write and no stray file from the bad key, anywhere
        # under XDG_CONFIG_HOME.
        stray = [name for _, dirs, files in os.walk(self.tmp.name) for name in dirs + files
                if "evil" in name]
        self.assertEqual(stray, [])
        # The malformed-value id was dropped on load, never scheduled.
        self.assertFalse(os.path.exists(self.timer_path(bad_value_id)))
        self.assertFalse(any(bad_value_id in arg for c in run.calls for arg in c))
        # Only the well-formed entry got a timer, written and enabled.
        self.assertTrue(os.path.exists(self.timer_path(good_rid)))
        self.assertTrue(any("enable" in c and ("runbook-" + good_rid + ".timer") in c for c in run.calls))

    def test_native_schedule_not_overridden_by_same_id_side_entry_when_readily_read_fails(self):
        """Review Minor #2: the ok: False "preserve everything" branch must
        keep the same setdefault protection as the ok: True path -- mirrors
        test_native_schedule_is_not_overridden_by_same_id_side_entry but
        with the Readily read failing."""
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 999}})
        run = ScheduleSyncFakeRun(listing=(1, "", "boom"))  # readily read fails -> ok: False

        out = engine.sync_schedules(
            self.lib([{"id": rid, "name": "n", "command": "c", "help": "",
                      "schedule": {"kind": "interval", "seconds": 600}}]),
            self.environ, run)

        self.assertEqual(out, {})
        with open(self.timer_path(rid), encoding="utf-8") as fh:
            content = fh.read()
        self.assertIn("OnUnitActiveSec=600", content)
        self.assertNotIn("OnUnitActiveSec=999", content)
        enable_calls = [c for c in run.calls if "enable" in c and ("runbook-" + rid + ".timer") in c]
        self.assertEqual(len(enable_calls), 1)


class SchedulesReportReadilySideStoreTest(unittest.TestCase):
    """Task 5: schedules_report gains the same enabled-gated union as
    sync_schedules, querying each in-scope id's timer the same way."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_bin = os.path.join(self.tmp.name, "readily")
        with open(self.fake_bin, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(self.fake_bin, 0o755)
        self.environ = {"HOME": "/h", "XDG_CONFIG_HOME": self.tmp.name,
                        "RUNBOOK_READILY_BIN": self.fake_bin}

    def tearDown(self):
        self.tmp.cleanup()

    def lib(self, scripts=()):
        return {"version": 1, "view": {"width": 960, "height": 540}, "scripts": list(scripts)}

    def show_argv(self, sid):
        return ("systemctl", "--user", "show", "runbook-" + sid + ".timer",
                "--property=NextElapseUSecRealtime", "--property=NextElapseUSecMonotonic")

    def future_monotonic_response(self):
        future = int((time.clock_gettime(time.CLOCK_MONOTONIC) + 300) * 1_000_000)
        return f"NextElapseUSecRealtime=0\nNextElapseUSecMonotonic={future}\n", 0

    def test_enabled_includes_side_id_present_in_readily_set(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""),
                                  responses={self.show_argv(rid): self.future_monotonic_response()})

        report = engine.schedules_report(self.lib(), self.environ, run)
        self.assertIn(rid, report)

    def test_disabled_excludes_side_ids_and_makes_no_readily_calls(self):
        # readily.enabled left at its default (False): no config.json saved.
        rid = expected_id("Backup home")
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""),
                                  responses={self.show_argv(rid): self.future_monotonic_response()})

        report = engine.schedules_report(self.lib(), self.environ, run)
        self.assertNotIn(rid, report)
        self.assertFalse(any(c[0] == self.fake_bin for c in run.calls))

    def test_side_id_not_in_readily_set_is_skipped(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        stale_rid = "f" * 32
        engine.save_readily_schedules(self.environ, {stale_rid: {"kind": "interval", "seconds": 120}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""),
                                  responses={self.show_argv(stale_rid): self.future_monotonic_response()})

        report = engine.schedules_report(self.lib(), self.environ, run)
        self.assertNotIn(stale_rid, report)

    def test_native_and_readily_both_reported(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        native_sid = "n" * 32
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""), responses={
            self.show_argv(rid): self.future_monotonic_response(),
            self.show_argv(native_sid): self.future_monotonic_response(),
        })

        report = engine.schedules_report(
            self.lib([{"id": native_sid, "name": "n", "command": "c", "help": "",
                      "schedule": {"kind": "interval", "seconds": 600}}]),
            self.environ, run)

        self.assertIn(rid, report)
        self.assertIn(native_sid, report)

    def test_readily_read_failure_still_reports_all_side_ids(self):
        """Mirrors the sync_schedules data-loss fix: a transient Readily
        failure must not silently drop every Readily id from the report
        either -- their timers are being preserved by sync, so the report
        should still be able to surface them."""
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid_a, rid_b = "a" * 32, "b" * 32
        engine.save_readily_schedules(self.environ, {rid_a: {"kind": "interval", "seconds": 120},
                                                      rid_b: {"kind": "interval", "seconds": 300}})
        run = ScheduleSyncFakeRun(listing=(1, "", "boom"), responses={
            self.show_argv(rid_a): self.future_monotonic_response(),
            self.show_argv(rid_b): self.future_monotonic_response(),
        })

        report = engine.schedules_report(self.lib(), self.environ, run)
        self.assertIn(rid_a, report)
        self.assertIn(rid_b, report)


class SetConfigReconciliationTest(unittest.TestCase):
    """Fix I1 (final-review blocker): set-config must reconcile timers, not
    just persist. sync_schedules is already config-gated on readily.enabled,
    so the headline behavior is the toggle itself: OFF tears down
    previously-scheduled Readily timers (via the stale-unit sweep) while
    retaining the side-store for a later re-enable; ON picks up existing
    side-store entries whose Readily command still exists."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake_bin = os.path.join(self.tmp.name, "readily")
        with open(self.fake_bin, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(self.fake_bin, 0o755)
        self.environ = {"HOME": "/h", "XDG_CONFIG_HOME": self.tmp.name,
                        "RUNBOOK_READILY_BIN": self.fake_bin}
        os.makedirs(engine.unit_dir(self.environ))

    def tearDown(self):
        self.tmp.cleanup()

    def timer_path(self, sid):
        return os.path.join(engine.unit_dir(self.environ), "runbook-" + sid + ".timer")

    def service_path(self, sid):
        return os.path.join(engine.unit_dir(self.environ), "runbook-" + sid + ".service")

    def touch_unit(self, sid):
        d = engine.unit_dir(self.environ)
        for ext in ("timer", "service"):
            open(os.path.join(d, "runbook-" + sid + "." + ext), "w").close()

    def cli(self, argv, run, stdin_text=""):
        out = io.StringIO()
        code = engine.main(argv, environ=self.environ, run=run, stdin=io.StringIO(stdin_text),
                           stdout=out, kill=lambda pid, sig: None, sleep=lambda s: None,
                           clock=lambda: 0.0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "exactly one JSON document")
        return code, json.loads(lines[0])

    def test_toggle_off_tears_down_readily_timers_but_retains_the_store(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        rid = expected_id("Backup home")
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        self.touch_unit(rid)
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))

        code, payload = self.cli(["set-config"], run, stdin_text='{"readily":{"enabled":false}}')

        self.assertEqual(code, 0)
        self.assertEqual(payload["readily"]["enabled"], False)
        self.assertFalse(os.path.exists(self.timer_path(rid)))
        self.assertFalse(os.path.exists(self.service_path(rid)))
        self.assertTrue(any("disable" in c and ("runbook-" + rid + ".timer") in c for c in run.calls))
        # The store survives the toggle for a later re-enable.
        self.assertIn(rid, engine.load_readily_schedules(self.environ))

    def test_toggle_on_schedules_an_existing_side_store_entry(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": False}})
        rid = expected_id("Backup home")
        engine.save_readily_schedules(self.environ, {rid: {"kind": "interval", "seconds": 120}})
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))

        code, payload = self.cli(["set-config"], run, stdin_text='{"readily":{"enabled":true}}')

        self.assertEqual(code, 0)
        self.assertEqual(payload["readily"]["enabled"], True)
        self.assertTrue(os.path.exists(self.timer_path(rid)))
        self.assertTrue(any("enable" in c and ("runbook-" + rid + ".timer") in c for c in run.calls))


class WriteCommandsReadilyMergeTest(ReadilyDispatchCase):
    """Regression: every command that returns a library to the UI must return
    the same native+Readily merge that `list` performs. Before this fix,
    set-view / add / update / remove returned the native-only library, so the
    UI -- which treats the reply as the whole list -- dropped the Readily rows
    until the next `list`. They vanished on window resize (set-view) and on any
    native add/edit/delete."""

    FIELDS = {"name": "Ports", "command": "echo hi", "help": "h"}

    def enable(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})

    def lib_path(self):
        return os.path.join(self.tmp.name, "runbook", "scripts.json")

    def seed_native(self):
        library = engine.empty_library()
        script = engine.add_script(library, self.FIELDS)
        engine.save_library(self.lib_path(), library)
        return script

    def readily_names(self, payload):
        return [s["name"] for s in payload["scripts"] if s.get("source") == "readily"]

    def test_set_view_keeps_readily_scripts_when_enabled(self):
        self.enable()
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))
        code, payload = self.cli(["set-view"], run, stdin_text='{"width": 1000, "height": 700}')
        self.assertEqual(code, 0)
        self.assertEqual(payload["view"], {"width": 1000, "height": 700})
        self.assertIn("Backup home", self.readily_names(payload))

    def test_add_keeps_readily_scripts_when_enabled(self):
        self.enable()
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))
        code, payload = self.cli(["add"], run, stdin_text=json.dumps(self.FIELDS))
        self.assertEqual(code, 0)
        self.assertIn("Ports", [s["name"] for s in payload["scripts"]])
        self.assertIn("Backup home", self.readily_names(payload))

    def test_update_keeps_readily_scripts_when_enabled(self):
        self.enable()
        script = self.seed_native()
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))
        code, payload = self.cli(["update", script["id"]], run,
                                 stdin_text=json.dumps(dict(self.FIELDS, help="x")))
        self.assertEqual(code, 0)
        self.assertEqual(next(s for s in payload["scripts"] if s["id"] == script["id"])["help"], "x")
        self.assertIn("Backup home", self.readily_names(payload))

    def test_remove_keeps_readily_scripts_when_enabled(self):
        self.enable()
        script = self.seed_native()
        run = ScheduleSyncFakeRun(listing=(0, LIST_PAYLOAD, ""))
        code, payload = self.cli(["remove", script["id"]], run)
        self.assertEqual(code, 0)
        self.assertEqual([s for s in payload["scripts"] if s.get("source") != "readily"], [])
        self.assertIn("Backup home", self.readily_names(payload))


if __name__ == "__main__":
    unittest.main()
