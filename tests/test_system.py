"""End-to-end tests on a private tmux server (socket runbook-test-<pid>).

Skipped when tmux is not installed. The server is killed at the end.
"""
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

from _load import load

engine = load()

SOCKET = f"runbook-test-{os.getpid()}"
CONF = os.path.join(engine.PLUGIN_DIR, "tmux.conf")


@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class SystemTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        # A PATH without ~/.local/bin, so the engine's prepend is observable.
        cls.environ = dict(os.environ, XDG_CONFIG_HOME=cls.tmp.name, PATH="/usr/bin:/bin",
                           RUNBOOK_TMUX_SOCKET=SOCKET, RUNBOOK_TMUX_CONF=CONF)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(["tmux", "-L", SOCKET, "kill-server"], capture_output=True)
        cls.tmp.cleanup()

    def cli(self, argv, stdin_text=""):
        out = io.StringIO()
        code = engine.main(argv, environ=self.environ, stdin=io.StringIO(stdin_text), stdout=out)
        return code, json.loads(out.getvalue())

    def add(self, name, command):
        code, library = self.cli(["add"], json.dumps({"name": name, "command": command, "help": ""}))
        self.assertEqual(code, 0, library)
        return library["scripts"][-1]["id"]

    def wait_for(self, script_id, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while True:
            code, screen = self.cli(["screen", script_id])
            self.assertEqual(code, 0, screen)
            if predicate(screen):
                return screen
            if time.monotonic() > deadline:
                self.fail(f"timed out after {timeout:g}s waiting for {script_id}; last screen: {screen!r}")
            time.sleep(0.1)

    def run_script(self, script_id, cols=60, rows=10):
        code, payload = self.cli(["run", script_id, "--cols", str(cols), "--rows", str(rows)])
        self.assertEqual((code, payload), (0, {"ok": True, "already": False}))

    def test_output_and_exit_code_are_captured(self):
        sid = self.add("exit3", "printf 'a\\nb\\n'; exit 3")
        self.run_script(sid)
        screen = self.wait_for(sid, lambda s: s["dead"])
        self.assertEqual(screen["text"], "a\nb")
        self.assertEqual(screen["exit"], 3)
        self.assertIsNone(screen["prompt"])
        self.cli(["close", sid])

    def test_send_reaches_the_command(self):
        sid = self.add("reader", "read -r x; echo got:$x")
        self.run_script(sid)
        code, payload = self.cli(["send", sid], '{"text": "hello"}')
        self.assertEqual((code, payload), (0, {"ok": True}))
        screen = self.wait_for(sid, lambda s: s["dead"])
        self.assertIn("got:hello", screen["text"])
        self.assertEqual(screen["exit"], 0)
        self.cli(["close", sid])

    def test_password_prompt_is_detected(self):
        sid = self.add("prompt", "printf '[sudo] password for fer: '; read -r -s p; echo; echo done")
        self.run_script(sid)
        screen = self.wait_for(sid, lambda s: s["prompt"] == "password")
        self.assertEqual(screen["prompt"], "password")
        self.cli(["send", sid], '{"text": "x"}')
        self.wait_for(sid, lambda s: s["dead"])
        self.cli(["close", sid])

    def test_stop_interrupts_and_keeps_the_screen(self):
        sid = self.add("sleeper", "echo before; sleep 100")
        self.run_script(sid)
        self.wait_for(sid, lambda s: "before" in s["text"])
        code, payload = self.cli(["stop", sid])
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["dead"], True)
        self.assertEqual(payload["exit"], 130)
        code, screen = self.cli(["screen", sid])
        self.assertIn("before", screen["text"])
        self.cli(["close", sid])

    def test_stop_escalates_to_sigkill(self):
        sid = self.add("stubborn", "trap '' INT TERM; echo armed; sleep 100")
        self.run_script(sid)
        self.wait_for(sid, lambda s: "armed" in s["text"])
        started = time.monotonic()
        code, payload = self.cli(["stop", sid])
        self.assertEqual(payload, {"ok": True, "dead": True, "exit": 137})
        self.assertLess(time.monotonic() - started, 6.0)
        self.cli(["close", sid])

    def test_close_removes_the_session(self):
        sid = self.add("closer", "sleep 100")
        self.run_script(sid)
        self.assertEqual(self.cli(["close", sid]), (0, {"ok": True}))
        code, payload = self.cli(["screen", sid])
        self.assertEqual((code, payload), (1, {"error": "No session for this script"}))
        code, status = self.cli(["status"])
        self.assertNotIn(sid, status["sessions"])

    def test_resize_changes_the_pane(self):
        sid = self.add("sizer", "sleep 100")
        self.run_script(sid, cols=60, rows=10)
        self.assertEqual(self.cli(["resize", sid, "--cols", "100", "--rows", "30"]), (0, {"ok": True}))
        out = subprocess.run(["tmux", "-L", SOCKET, "display", "-p", "-t", sid, "#{pane_width} #{pane_height}"],
                             capture_output=True, text=True).stdout.split()
        self.assertEqual(out, ["100", "30"])
        self.cli(["close", sid])

    def test_quotes_and_dollars_survive(self):
        sid = self.add("quotes", "X=1; echo \"d'q\" 'single \"in\"' $X '$X'")
        self.run_script(sid)
        screen = self.wait_for(sid, lambda s: s["dead"])
        self.assertEqual(screen["text"], "d'q single \"in\" 1 $X")
        self.cli(["close", sid])

    def test_run_on_live_session_reports_already(self):
        sid = self.add("twice", "sleep 100")
        self.run_script(sid)
        code, payload = self.cli(["run", sid, "--cols", "60", "--rows", "10"])
        self.assertEqual((code, payload), (0, {"ok": True, "already": True}))
        code, status = self.cli(["status"])
        self.assertEqual(status["sessions"][sid], {"dead": False, "exit": None})
        self.cli(["close", sid])

    def test_local_bin_is_on_path(self):
        sid = self.add("path", "echo \"$PATH\" | tr ':' '\\n' | head -1")
        self.run_script(sid)
        screen = self.wait_for(sid, lambda s: s["dead"])
        self.assertEqual(screen["text"], os.path.join(self.environ["HOME"], ".local", "bin"))
        self.cli(["close", sid])
