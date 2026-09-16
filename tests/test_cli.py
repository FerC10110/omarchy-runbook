import io
import json
import os
import tempfile
import unittest

from _load import load
from fakes import FakeRun

engine = load()

ID = "0123456789abcdef0123456789abcdef"
FIELDS = {"name": "Ports", "command": "sudo /home/fer/.local/bin/ports -a", "help": "All listeners."}
ALIVE = (0, "0\t\t\t4242\n", "")
DEAD_3 = (0, "1\t3\t\t4242\n", "")
GONE = (1, "", "can't find session: x\n")
OK = (0, "", "")


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environ = {"HOME": "/h", "PATH": "/usr/bin", "XDG_CONFIG_HOME": self.tmp.name,
                        "RUNBOOK_TMUX_SOCKET": "sock", "RUNBOOK_TMUX_CONF": "/plug/tmux.conf"}
        self.path = os.path.join(self.tmp.name, "runbook", "scripts.json")

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

    def seed(self):
        library = engine.empty_library()
        script = engine.add_script(library, FIELDS)
        engine.save_library(self.path, library)
        return script


class UsageTest(CliCase):
    def test_unknown_command_is_a_json_error_with_exit_1(self):
        code, payload, _ = self.cli(["bogus"])
        self.assertEqual((code, payload), (1, {"error": "Unknown command: bogus"}))

    def test_missing_command_is_a_json_error(self):
        code, payload, _ = self.cli([])
        self.assertEqual((code, payload), (1, {"error": engine.USAGE}))

    def test_commands_needing_an_id_reject_bad_ids_before_tmux(self):
        for cmd in ("update", "remove", "run", "screen", "send", "stop", "close", "resize"):
            code, payload, fake = self.cli([cmd, "bad-id"], stdin_text="{}")
            self.assertEqual((code, payload), (1, {"error": "Invalid script id"}), cmd)
            self.assertEqual(fake.calls, [], cmd)


class LibraryCommandsTest(CliCase):
    def test_list_returns_empty_library(self):
        code, payload, fake = self.cli(["list"])
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"version": 1, "view": {"width": 960, "height": 540}, "scripts": []})
        self.assertEqual(fake.calls, [])

    def test_add_reads_json_from_stdin_and_persists(self):
        code, payload, _ = self.cli(["add"], stdin_text=json.dumps(FIELDS))
        self.assertEqual(code, 0)
        self.assertEqual(len(payload["scripts"]), 1)
        self.assertEqual(payload["scripts"][0]["name"], "Ports")
        self.assertEqual(engine.load_library(self.path)["scripts"], payload["scripts"])

    def test_add_with_invalid_stdin(self):
        code, payload, _ = self.cli(["add"], stdin_text="nope")
        self.assertEqual((code, payload), (1, {"error": "Expected a JSON object on stdin"}))

    def test_add_validation_error_does_not_write(self):
        code, payload, _ = self.cli(["add"], stdin_text=json.dumps(dict(FIELDS, name="")))
        self.assertEqual((code, payload), (1, {"error": "Name must be 1-64 characters"}))
        self.assertFalse(os.path.exists(self.path))

    def test_update(self):
        script = self.seed()
        code, payload, _ = self.cli(["update", script["id"]], stdin_text=json.dumps(dict(FIELDS, help="x")))
        self.assertEqual(code, 0)
        self.assertEqual(payload["scripts"][0]["help"], "x")

    def test_remove_closes_the_session_and_persists(self):
        script = self.seed()
        code, payload, fake = self.cli(["remove", script["id"]])
        self.assertEqual(code, 0)
        self.assertEqual(payload["scripts"], [])
        self.assertEqual(fake.tails(), [["kill-session", "-t", script["id"]]])
        self.assertEqual(engine.load_library(self.path)["scripts"], [])

    def test_set_view(self):
        code, payload, _ = self.cli(["set-view"], stdin_text='{"width": 1000, "height": 700}')
        self.assertEqual(code, 0)
        self.assertEqual(payload["view"], {"width": 1000, "height": 700})
        self.assertEqual(engine.load_library(self.path)["view"], {"width": 1000, "height": 700})


class RunTest(CliCase):
    def test_run_starts_a_session_with_the_stored_command(self):
        script = self.seed()
        code, payload, fake = self.cli(["run", script["id"], "--cols", "100", "--rows", "30"], answers=[GONE, OK])
        self.assertEqual((code, payload), (0, {"ok": True, "already": False}))
        argv = fake.tails()[1]
        self.assertEqual(argv[:9], ["new-session", "-d", "-s", script["id"], "-x", "100", "-y", "30", "-c"])
        self.assertIn("RUNBOOK_COMMAND=" + FIELDS["command"], argv)

    def test_run_on_live_session_does_nothing(self):
        script = self.seed()
        code, payload, fake = self.cli(["run", script["id"], "--cols", "80", "--rows", "24"], answers=[ALIVE])
        self.assertEqual((code, payload), (0, {"ok": True, "already": True}))
        self.assertEqual(len(fake.calls), 1)

    def test_run_on_dead_session_restarts(self):
        script = self.seed()
        code, payload, fake = self.cli(["run", script["id"], "--cols", "80", "--rows", "24"], answers=[DEAD_3, OK, OK])
        self.assertEqual((code, payload), (0, {"ok": True, "already": False}))
        self.assertEqual(fake.tails()[1], ["kill-session", "-t", script["id"]])
        self.assertEqual(fake.tails()[2][0], "new-session")

    def test_run_clamps_size_and_defaults(self):
        script = self.seed()
        _, _, fake = self.cli(["run", script["id"], "--cols", "5000", "--rows", "1"], answers=[GONE, OK])
        self.assertEqual(fake.tails()[1][4:8], ["-x", "500", "-y", "5"])
        _, _, fake = self.cli(["run", script["id"]], answers=[GONE, OK])
        self.assertEqual(fake.tails()[1][4:8], ["-x", "80", "-y", "24"])

    def test_run_unknown_script(self):
        code, payload, fake = self.cli(["run", ID])
        self.assertEqual((code, payload), (1, {"error": "No such script"}))
        self.assertEqual(fake.calls, [])


class SessionCommandsTest(CliCase):
    def test_status(self):
        out = f"{ID}\t0\t\t\n{'b' * 32}\t1\t3\t\n"
        code, payload, _ = self.cli(["status"], answers=[(0, out, "")])
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"sessions": {ID: {"dead": False, "exit": None},
                                                "b" * 32: {"dead": True, "exit": 3}}})

    def test_status_without_server(self):
        code, payload, _ = self.cli(["status"], answers=[(1, "", "no server running on /tmp/tmux-1000/sock\n")])
        self.assertEqual((code, payload), (0, {"sessions": {}}))

    def test_screen(self):
        code, payload, _ = self.cli(["screen", ID], answers=[ALIVE, (0, "[sudo] password for fer: \n\n", "")])
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"text": "[sudo] password for fer: ", "dead": False, "exit": None,
                                   "prompt": "password"})

    def test_screen_dead(self):
        code, payload, _ = self.cli(["screen", ID], answers=[DEAD_3, (0, "done\n", "")])
        self.assertEqual(payload, {"text": "done", "dead": True, "exit": 3, "prompt": None})

    def test_screen_without_session(self):
        code, payload, _ = self.cli(["screen", ID], answers=[GONE])
        self.assertEqual((code, payload), (1, {"error": "No session for this script"}))

    def test_send(self):
        code, payload, fake = self.cli(["send", ID], stdin_text='{"text": "hunter2"}', answers=[ALIVE, OK, OK])
        self.assertEqual((code, payload), (0, {"ok": True}))
        self.assertEqual(fake.tails()[1], ["send-keys", "-t", ID, "-l", "--", "hunter2"])

    def test_send_requires_text_field(self):
        code, payload, _ = self.cli(["send", ID], stdin_text='{"nope": 1}')
        self.assertEqual((code, payload), (1, {"error": "Expected a \"text\" string"}))

    def test_stop(self):
        code, payload, _ = self.cli(["stop", ID], answers=[ALIVE, OK, (0, "1\t\t2\t4242\n", "")])
        self.assertEqual((code, payload), (0, {"ok": True, "dead": True, "exit": 130}))

    def test_close_is_ok_even_without_session(self):
        code, payload, fake = self.cli(["close", ID], answers=[(1, "", "no server running on x\n")])
        self.assertEqual((code, payload), (0, {"ok": True}))
        self.assertEqual(fake.tails(), [["kill-session", "-t", ID]])

    def test_resize(self):
        code, payload, fake = self.cli(["resize", ID, "--cols", "90", "--rows", "40"], answers=[ALIVE, OK])
        self.assertEqual((code, payload), (0, {"ok": True}))
        self.assertEqual(fake.tails()[1], ["resize-window", "-t", ID, "-x", "90", "-y", "40"])

    def test_resize_requires_numbers(self):
        code, payload, _ = self.cli(["resize", ID, "--cols", "x", "--rows", "40"])
        self.assertEqual((code, payload), (1, {"error": "--cols and --rows must be integers"}))


class TmuxWiringTest(CliCase):
    def test_socket_and_conf_come_from_environment_with_defaults(self):
        _, _, fake = self.cli(["status"], answers=[(0, "", "")])
        self.assertEqual(fake.calls[0][:5], ["tmux", "-L", "sock", "-f", "/plug/tmux.conf"])
        del self.environ["RUNBOOK_TMUX_SOCKET"]
        del self.environ["RUNBOOK_TMUX_CONF"]
        _, _, fake = self.cli(["status"], answers=[(0, "", "")])
        self.assertEqual(fake.calls[0][:3], ["tmux", "-L", "runbook"])
        self.assertEqual(fake.calls[0][4], os.path.join(engine.PLUGIN_DIR, "tmux.conf"))
