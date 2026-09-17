import os
import tempfile
import unittest

from _load import load

engine = load()


class FakeRun:
    """Sustituto de subprocess.run que registra los argv y responde scriptado."""
    def __init__(self, responses=None, available=True):
        self.calls = []
        self.responses = responses or {}
        self.available = available

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
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


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.env = {"XDG_CONFIG_HOME": "/home/u/.config",
                    "RUNBOOK_TMUX_SOCKET": "runbook"}

    def test_service_runs_run_id_not_the_command(self):
        text = engine.render_service("a" * 32, self.env)
        self.assertIn("ExecStart=", text)
        self.assertIn(" run " + "a" * 32, text)
        self.assertIn("Type=oneshot", text)
        self.assertIn("Environment=RUNBOOK_TMUX_SOCKET=runbook", text)
        self.assertNotIn("$RUNBOOK_COMMAND", text)  # el comando no viaja en el unit

    def test_timer_interval(self):
        text = engine.render_timer({"kind": "interval", "seconds": 1800})
        self.assertIn("OnUnitActiveSec=1800", text)
        self.assertIn("WantedBy=timers.target", text)

    def test_timer_calendar(self):
        text = engine.render_timer({"kind": "calendar", "oncalendar": "*-*-* 08:00:00"})
        self.assertIn("OnCalendar=*-*-* 08:00:00", text)
        self.assertIn("Persistent=true", text)


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.dir = self.enterContext(tempfile.TemporaryDirectory())
        self.env = {"XDG_CONFIG_HOME": self.dir, "RUNBOOK_TMUX_SOCKET": "runbook"}
        os.makedirs(engine.unit_dir(self.env))

    def lib(self, scripts):
        return {"version": 1, "view": {"width": 960, "height": 540}, "scripts": scripts}

    def test_scheduled_script_writes_and_enables(self):
        run = FakeRun()
        sid = "a" * 32
        lib = self.lib([{"id": sid, "name": "n", "command": "c", "help": "",
                         "schedule": {"kind": "interval", "seconds": 600}}])
        out = engine.sync_schedules(lib, self.env, run)
        self.assertEqual(out, {})
        self.assertTrue(os.path.exists(os.path.join(engine.unit_dir(self.env), "runbook-" + sid + ".timer")))
        self.assertTrue(any(c[:2] == ["systemctl", "--user"] and "daemon-reload" in c for c in run.calls))
        self.assertTrue(any("enable" in c and "runbook-" + sid + ".timer" in c for c in run.calls))

    def test_unscheduled_removes_stale_unit(self):
        run = FakeRun()
        sid = "b" * 32
        d = engine.unit_dir(self.env)
        for ext in ("timer", "service"):
            open(os.path.join(d, "runbook-" + sid + "." + ext), "w").close()
        engine.sync_schedules(self.lib([{"id": sid, "name": "n", "command": "c", "help": ""}]), self.env, run)
        self.assertFalse(os.path.exists(os.path.join(d, "runbook-" + sid + ".timer")))
        self.assertTrue(any("disable" in c and "runbook-" + sid + ".timer" in c for c in run.calls))

    def test_no_systemd_returns_warning_and_does_not_raise(self):
        run = FakeRun(available=False)
        sid = "c" * 32
        out = engine.sync_schedules(self.lib([{"id": sid, "name": "n", "command": "c", "help": "",
                                               "schedule": {"kind": "interval", "seconds": 600}}]), self.env, run)
        self.assertIn("schedule_warning", out)


class NormalizeCalendarTest(unittest.TestCase):
    def test_normalizes_via_systemd_analyze(self):
        run = FakeRun()
        self.assertEqual(engine.normalize_calendar("*-*-* 08:00:00", run), "*-*-* 08:00:00")

    def test_invalid_raises(self):
        run = FakeRun(responses={("systemd-analyze", "calendar", "bad"): ("", 1)})
        with self.assertRaises(engine.RunbookError):
            engine.normalize_calendar("bad", run)
