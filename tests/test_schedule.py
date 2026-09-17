import datetime
import os
import tempfile
import time
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

    def test_service_skips_env_values_with_newline(self):
        env = dict(self.env)
        env["RUNBOOK_TMUX_CONF"] = "bad\nDescription=Injected"
        text = engine.render_service("a" * 32, env)
        self.assertNotIn("Injected", text)
        self.assertNotIn("RUNBOOK_TMUX_CONF", text)

    def test_service_includes_readily_bin_when_set(self):
        """M4: a scheduled unit must carry RUNBOOK_READILY_BIN, or an install
        that resolves the readily binary solely via that env override breaks
        when the timer fires (no interactive shell env to inherit)."""
        env = dict(self.env)
        env["RUNBOOK_READILY_BIN"] = "/opt/readily/bin/readily"
        text = engine.render_service("a" * 32, env)
        self.assertIn("Environment=RUNBOOK_READILY_BIN=/opt/readily/bin/readily", text)

    def test_service_omits_readily_bin_when_unset(self):
        text = engine.render_service("a" * 32, self.env)
        self.assertNotIn("RUNBOOK_READILY_BIN", text)


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

    def test_bus_drop_on_second_daemon_reload_returns_warning(self):
        """F2: the first daemon-reload (before any write) can succeed while the
        bus later drops; every systemctl call must be checked, not just the
        first."""
        class DropsOnSecondReload:
            def __init__(self):
                self.calls = []

            def __call__(self, argv, **kwargs):
                self.calls.append(argv)

                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                r = R()
                if argv[:3] == ["systemctl", "--user", "daemon-reload"]:
                    reloads = sum(1 for c in self.calls if c[:3] == ["systemctl", "--user", "daemon-reload"])
                    if reloads == 2:
                        r.returncode = 1
                        r.stderr = "Failed to connect to bus"
                return r

        run = DropsOnSecondReload()
        sid = "d" * 32
        out = engine.sync_schedules(self.lib([{"id": sid, "name": "n", "command": "c", "help": "",
                                               "schedule": {"kind": "interval", "seconds": 600}}]), self.env, run)
        self.assertIn("schedule_warning", out)

    def test_bus_drop_on_enable_returns_warning(self):
        class DropsOnEnable:
            def __call__(self, argv, **kwargs):
                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                r = R()
                if argv[:3] == ["systemctl", "--user", "enable"]:
                    r.returncode = 1
                    r.stderr = "Failed to connect to bus"
                return r

        run = DropsOnEnable()
        sid = "e" * 32
        out = engine.sync_schedules(self.lib([{"id": sid, "name": "n", "command": "c", "help": "",
                                               "schedule": {"kind": "interval", "seconds": 600}}]), self.env, run)
        self.assertIn("schedule_warning", out)

    def test_bus_drop_on_disable_returns_warning(self):
        """The stale-unit sweep's `disable --now` result must be checked too,
        symmetric to the daemon-reload/enable checks above."""
        class DropsOnDisable:
            def __call__(self, argv, **kwargs):
                class R:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                r = R()
                if argv[:3] == ["systemctl", "--user", "disable"]:
                    r.returncode = 1
                    r.stderr = "Failed to connect to bus"
                return r

        run = DropsOnDisable()
        stale_sid = "f" * 32
        d = engine.unit_dir(self.env)
        for ext in ("timer", "service"):
            open(os.path.join(d, "runbook-" + stale_sid + "." + ext), "w").close()
        sid = "g" * 32
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


class ReportTest(unittest.TestCase):
    """F1: schedules_report(library, environ, run) queries each scheduled
    unit's NextElapseUSec* properties directly, instead of mis-parsing
    `systemctl list-timers` table output."""

    def setUp(self):
        self.env = {"XDG_CONFIG_HOME": "/home/u/.config"}

    def lib(self, scripts):
        return {"version": 1, "view": {"width": 960, "height": 540}, "scripts": scripts}

    def show_argv(self, sid):
        return ("systemctl", "--user", "show", "runbook-" + sid + ".timer",
                "--property=NextElapseUSecRealtime", "--property=NextElapseUSecMonotonic")

    def test_calendar_unit_with_future_realtime_is_reported(self):
        sid = "a" * 32
        future_usec = int((datetime.datetime.now(datetime.timezone.utc).timestamp() + 3661) * 1_000_000)
        run = FakeRun(responses={self.show_argv(sid): (
            f"NextElapseUSecRealtime={future_usec}\nNextElapseUSecMonotonic=0\n", 0)})
        lib = self.lib([{"id": sid, "name": "n", "command": "c", "help": "",
                         "schedule": {"kind": "calendar", "oncalendar": "*-*-* 08:00:00"}}])
        report = engine.schedules_report(lib, self.env, run)
        self.assertIn(sid, report)
        self.assertTrue(report[sid]["next"])

    def test_nonzero_returncode_skips_the_id(self):
        sid = "b" * 32
        run = FakeRun(responses={self.show_argv(sid): ("", 1)})
        lib = self.lib([{"id": sid, "name": "n", "command": "c", "help": "",
                         "schedule": {"kind": "interval", "seconds": 600}}])
        report = engine.schedules_report(lib, self.env, run)
        self.assertNotIn(sid, report)
        self.assertEqual(report, {})

    def test_interval_unit_with_future_monotonic_is_reported(self):
        sid = "c" * 32
        future_mono = int((time.clock_gettime(time.CLOCK_MONOTONIC) + 120) * 1_000_000)
        run = FakeRun(responses={self.show_argv(sid): (
            f"NextElapseUSecRealtime=0\nNextElapseUSecMonotonic={future_mono}\n", 0)})
        lib = self.lib([{"id": sid, "name": "n", "command": "c", "help": "",
                         "schedule": {"kind": "interval", "seconds": 600}}])
        report = engine.schedules_report(lib, self.env, run)
        self.assertIn(sid, report)
        self.assertTrue(report[sid]["next"])

    def test_past_realtime_is_skipped(self):
        sid = "f" * 32
        past_usec = int((datetime.datetime.now(datetime.timezone.utc).timestamp() - 3600) * 1_000_000)
        run = FakeRun(responses={self.show_argv(sid): (
            f"NextElapseUSecRealtime={past_usec}\nNextElapseUSecMonotonic=0\n", 0)})
        lib = self.lib([{"id": sid, "name": "n", "command": "c", "help": "",
                         "schedule": {"kind": "calendar", "oncalendar": "*-*-* 08:00:00"}}])
        report = engine.schedules_report(lib, self.env, run)
        self.assertIsInstance(report, dict)
        self.assertNotIn(sid, report)

    def test_non_numeric_property_is_skipped_without_raising(self):
        sid = "g" * 32
        run = FakeRun(responses={self.show_argv(sid): (
            "NextElapseUSecRealtime=garbage\nNextElapseUSecMonotonic=0\n", 0)})
        lib = self.lib([{"id": sid, "name": "n", "command": "c", "help": "",
                         "schedule": {"kind": "interval", "seconds": 600}}])
        report = engine.schedules_report(lib, self.env, run)
        self.assertIsInstance(report, dict)
        self.assertNotIn(sid, report)

    def test_unscheduled_scripts_are_not_queried(self):
        run = FakeRun()
        lib = self.lib([{"id": "d" * 32, "name": "n", "command": "c", "help": ""}])
        report = engine.schedules_report(lib, self.env, run)
        self.assertEqual(report, {})
        self.assertEqual(run.calls, [])

    def test_unexpected_error_returns_empty_dict_never_raises(self):
        def boom(argv, **kwargs):
            raise RuntimeError("kaboom")
        lib = self.lib([{"id": "e" * 32, "name": "n", "command": "c", "help": "",
                         "schedule": {"kind": "interval", "seconds": 600}}])
        report = engine.schedules_report(lib, self.env, boom)
        self.assertEqual(report, {})


class BusUnavailableTest(unittest.TestCase):
    """F3: _bus_unavailable must recognize the common ways systemd --user
    reports that there is no session bus / user instance, not just the
    literal word "bus"."""

    class Result:
        def __init__(self, returncode, stderr):
            self.returncode = returncode
            self.stderr = stderr

    def test_not_booted_message_is_recognized(self):
        result = self.Result(1, "System has not been booted with systemd as init system "
                                 "(PID 1). Can't operate.")
        self.assertTrue(engine._bus_unavailable(result))

    def test_failed_to_connect_message_is_recognized(self):
        result = self.Result(1, "Failed to connect to user scope bus via local transport")
        self.assertTrue(engine._bus_unavailable(result))

    def test_connection_refused_is_recognized(self):
        result = self.Result(1, "Connection refused")
        self.assertTrue(engine._bus_unavailable(result))

    def test_successful_result_is_not_unavailable(self):
        result = self.Result(0, "")
        self.assertFalse(engine._bus_unavailable(result))

    def test_unrelated_error_is_not_bus_unavailable(self):
        result = self.Result(1, "Unit runbook-x.timer not found.")
        self.assertFalse(engine._bus_unavailable(result))

    def test_sync_schedules_returns_warning_for_not_booted_message(self):
        class NotBooted:
            def __call__(self, argv, **kwargs):
                class R:
                    returncode = 1
                    stdout = ""
                    stderr = ("System has not been booted with systemd as init system "
                              "(PID 1). Can't operate.")
                return R()

        env = {"XDG_CONFIG_HOME": tempfile.mkdtemp()}
        sid = "f" * 32
        lib = {"version": 1, "view": {"width": 960, "height": 540},
               "scripts": [{"id": sid, "name": "n", "command": "c", "help": "",
                            "schedule": {"kind": "interval", "seconds": 600}}]}
        out = engine.sync_schedules(lib, env, NotBooted())
        self.assertIn("schedule_warning", out)
