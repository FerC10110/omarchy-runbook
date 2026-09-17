import io
import json
import os
import stat
import tempfile
import unittest

from _load import load
from fakes import FakeRun

engine = load()

DEFAULT = {"version": 1, "readily": {"enabled": False}}


class ConfigFilePathTest(unittest.TestCase):
    def test_uses_xdg_config_home_when_set(self):
        path = engine.config_file_path({"XDG_CONFIG_HOME": "/x/cfg", "HOME": "/h"})
        self.assertEqual(path, "/x/cfg/runbook/config.json")

    def test_falls_back_to_home_dot_config(self):
        path = engine.config_file_path({"HOME": "/h"})
        self.assertEqual(path, "/h/.config/runbook/config.json")


class LoadSaveConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environ = {"XDG_CONFIG_HOME": self.tmp.name, "HOME": "/h"}
        self.path = os.path.join(self.tmp.name, "runbook", "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_is_the_default_config(self):
        self.assertEqual(engine.load_config(self.environ), DEFAULT)

    def test_default_config_is_not_shared_mutable_state(self):
        first = engine.load_config(self.environ)
        first["readily"]["enabled"] = True
        second = engine.load_config(self.environ)
        self.assertEqual(second, DEFAULT)

    def test_save_then_load_round_trips(self):
        engine.save_config(self.environ, {"version": 1, "readily": {"enabled": True}})
        self.assertEqual(engine.load_config(self.environ), {"version": 1, "readily": {"enabled": True}})

    def test_save_creates_private_dir_and_file(self):
        engine.save_config(self.environ, engine.default_config())
        dir_mode = stat.S_IMODE(os.stat(os.path.dirname(self.path)).st_mode)
        file_mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(dir_mode, 0o700)
        self.assertEqual(file_mode, 0o600)

    def test_malformed_json_repairs_to_default(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("not json")
        self.assertEqual(engine.load_config(self.environ), DEFAULT)

    def test_top_level_list_repairs_to_default(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            fh.write("[]")
        self.assertEqual(engine.load_config(self.environ), DEFAULT)

    def test_readily_wrong_type_repairs_to_default(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            json.dump({"readily": "x"}, fh)
        self.assertEqual(engine.load_config(self.environ), DEFAULT)

    def test_enabled_wrong_type_repairs_to_default(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as fh:
            json.dump({"readily": {"enabled": "yes"}}, fh)
        self.assertEqual(engine.load_config(self.environ), DEFAULT)


class ConfigDispatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environ = {"HOME": "/h", "XDG_CONFIG_HOME": self.tmp.name}
        self.config_path = os.path.join(self.tmp.name, "runbook", "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, argv, stdin_text=""):
        fake = FakeRun()
        out = io.StringIO()
        code = engine.main(argv, environ=self.environ, run=fake, stdin=io.StringIO(stdin_text),
                           stdout=out, kill=lambda pid, sig: None, sleep=lambda s: None,
                           clock=lambda: 0.0)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1, "exactly one JSON document")
        return code, json.loads(lines[0]), fake

    def test_config_returns_default_when_absent(self):
        code, payload, fake = self.cli(["config"])
        self.assertEqual((code, payload), (0, DEFAULT))
        self.assertEqual(fake.calls, [])

    def test_set_config_persists_and_returns_merged_config(self):
        code, payload, fake = self.cli(["set-config"], stdin_text='{"readily":{"enabled":true}}')
        self.assertEqual((code, payload), (0, {"version": 1, "readily": {"enabled": True}}))
        self.assertEqual(fake.calls, [])
        self.assertEqual(engine.load_config(self.environ), payload)

    def test_config_reflects_a_previous_set_config(self):
        self.cli(["set-config"], stdin_text='{"readily":{"enabled":true}}')
        code, payload, _ = self.cli(["config"])
        self.assertEqual((code, payload), (0, {"version": 1, "readily": {"enabled": True}}))

    def test_set_config_ignores_unknown_top_level_keys(self):
        code, payload, _ = self.cli(["set-config"], stdin_text='{"bogus": 1, "readily": {"enabled": true}}')
        self.assertEqual((code, payload), (0, {"version": 1, "readily": {"enabled": True}}))

    def test_set_config_ignores_non_dict_readily(self):
        code, payload, _ = self.cli(["set-config"], stdin_text='{"readily": "nope"}')
        self.assertEqual((code, payload), (0, DEFAULT))

    def test_set_config_with_malformed_stdin_is_an_engine_error(self):
        code, payload, _ = self.cli(["set-config"], stdin_text="nope")
        self.assertEqual((code, payload), (1, {"error": "Expected a JSON object on stdin"}))
        self.assertFalse(os.path.exists(self.config_path))

    def test_set_config_file_mode_matches_convention(self):
        self.cli(["set-config"], stdin_text='{"readily":{"enabled":true}}')
        dir_mode = stat.S_IMODE(os.stat(os.path.dirname(self.config_path)).st_mode)
        file_mode = stat.S_IMODE(os.stat(self.config_path).st_mode)
        self.assertEqual(dir_mode, 0o700)
        self.assertEqual(file_mode, 0o600)


if __name__ == "__main__":
    unittest.main()
