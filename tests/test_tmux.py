import unittest

from _load import load
from fakes import FakeRun

engine = load()

ID = "0123456789abcdef0123456789abcdef"
PREFIX = ["tmux", "-L", "sock", "-f", "/plug/tmux.conf"]
STATE_FMT = "#{pane_dead}\t#{pane_dead_status}\t#{pane_dead_signal}\t#{pane_pid}"
LIST_FMT = "#{session_name}\t#{pane_dead}\t#{pane_dead_status}\t#{pane_dead_signal}"


def tmux(*answers):
    fake = FakeRun(answers)
    return engine.Tmux("sock", "/plug/tmux.conf", run=fake), fake


class CallTest(unittest.TestCase):
    def test_prefixes_socket_and_config(self):
        t, fake = tmux((0, "x\n", ""))
        self.assertEqual(t.call("list-sessions"), (0, "x\n", ""))
        self.assertEqual(fake.calls, [PREFIX + ["list-sessions"]])

    def test_timeout_is_an_engine_error(self):
        import subprocess

        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        t = engine.Tmux("sock", "/plug/tmux.conf", run=slow)
        with self.assertRaises(engine.RunbookError) as ctx:
            t.call("status")
        self.assertEqual(str(ctx.exception), "tmux did not answer within 5 seconds")

    def test_missing_binary_is_an_engine_error(self):
        def missing(argv, **kwargs):
            raise FileNotFoundError("tmux")
        t = engine.Tmux("sock", "/plug/tmux.conf", run=missing)
        with self.assertRaises(engine.RunbookError) as ctx:
            t.call("status")
        self.assertEqual(str(ctx.exception), "Cannot run tmux: tmux")


class HelpersTest(unittest.TestCase):
    def test_check_id_accepts_hex32(self):
        self.assertEqual(engine.check_id(ID), ID)

    def test_check_id_rejects_others(self):
        for bad in ("", "abc", "A" * 32, ID + "0", "../x"):
            with self.assertRaises(engine.RunbookError) as ctx:
                engine.check_id(bad)
            self.assertEqual(str(ctx.exception), "Invalid script id")

    def test_clamp_size(self):
        self.assertEqual(engine.clamp_size(80, 24), (80, 24))
        self.assertEqual(engine.clamp_size(1, 1), (20, 5))
        self.assertEqual(engine.clamp_size(9999, 9999), (500, 200))

    def test_path_prepends_local_bin_once(self):
        env = {"HOME": "/h", "PATH": "/usr/bin:/bin"}
        self.assertEqual(engine.path_with_local_bin(env), "/h/.local/bin:/usr/bin:/bin")
        env = {"HOME": "/h", "PATH": "/usr/bin:/h/.local/bin"}
        self.assertEqual(engine.path_with_local_bin(env), "/usr/bin:/h/.local/bin")

    def test_trim_trailing_blank(self):
        self.assertEqual(engine.trim_trailing_blank("a\n\nb\n \n\n"), "a\n\nb")
        self.assertEqual(engine.trim_trailing_blank("\n\n"), "")

    def test_detect_prompt(self):
        self.assertEqual(engine.detect_prompt("x\n[sudo] password for fer: \n\n"), "password")
        self.assertEqual(engine.detect_prompt("Contraseña:"), "password")
        self.assertIsNone(engine.detect_prompt("Password was wrong\n$ "))
        self.assertIsNone(engine.detect_prompt(""))


class ListSessionsTest(unittest.TestCase):
    def test_parses_alive_exited_and_signalled(self):
        out = f"{ID}\t0\t\t\n{'b' * 32}\t1\t3\t\n{'c' * 32}\t1\t\t15\nnot-an-id\t1\t0\t\n"
        t, fake = tmux((0, out, ""))
        self.assertEqual(engine.list_sessions(t), {
            ID: {"dead": False, "exit": None},
            "b" * 32: {"dead": True, "exit": 3},
            "c" * 32: {"dead": True, "exit": 143},
        })
        self.assertEqual(fake.tails(), [["list-sessions", "-F", LIST_FMT]])

    def test_no_server_means_no_sessions(self):
        t, _ = tmux((1, "", "no server running on /tmp/tmux-1000/sock\n"))
        self.assertEqual(engine.list_sessions(t), {})

    def test_other_failures_raise(self):
        t, _ = tmux((1, "", "something broke\n"))
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.list_sessions(t)
        self.assertEqual(str(ctx.exception), "tmux: something broke")


class SessionStateTest(unittest.TestCase):
    def test_alive(self):
        t, fake = tmux((0, "0\t\t\t4242\n", ""))
        self.assertEqual(engine.session_state(t, ID), {"dead": False, "exit": None, "pid": 4242})
        self.assertEqual(fake.tails(), [["display", "-p", "-t", ID, STATE_FMT]])

    def test_dead_by_status_and_by_signal(self):
        t, _ = tmux((0, "1\t3\t\t4242\n", ""), (0, "1\t\t9\t4242\n", ""))
        self.assertEqual(engine.session_state(t, ID)["exit"], 3)
        self.assertEqual(engine.session_state(t, ID)["exit"], 137)

    def test_empty_output_or_error_is_no_session(self):
        t, _ = tmux((0, "\n", ""), (1, "", "can't find session: x\n"),
                    (1, "", "no server running on /tmp/tmux-1000/sock\n"))
        for _ in range(3):
            with self.assertRaises(engine.NoSession) as ctx:
                engine.session_state(t, ID)
            self.assertEqual(str(ctx.exception), "No session for this script")


class StartSessionTest(unittest.TestCase):
    def test_argv_passes_command_through_environment(self):
        t, fake = tmux()
        env = {"HOME": "/h", "PATH": "/usr/bin"}
        engine.start_session(t, ID, "echo 'a' \"b\" $C", 120, 30, env)
        self.assertEqual(fake.tails(), [[
            "new-session", "-d", "-s", ID, "-x", "120", "-y", "30", "-c", "/h",
            "-e", "RUNBOOK_COMMAND=echo 'a' \"b\" $C",
            "-e", "PATH=/h/.local/bin:/usr/bin",
            "--", 'exec bash -c "$RUNBOOK_COMMAND"']])

    def test_failure_raises(self):
        t, _ = tmux((1, "", "duplicate session: x\n"))
        with self.assertRaises(engine.RunbookError) as ctx:
            engine.start_session(t, ID, "true", 80, 24, {"HOME": "/h"})
        self.assertEqual(str(ctx.exception), "tmux: duplicate session: x")


class CaptureSendResizeKillTest(unittest.TestCase):
    def test_capture_trims_and_keeps_first_line(self):
        t, fake = tmux((0, "$ ls\na\n\n\n", ""))
        self.assertEqual(engine.capture(t, ID), "$ ls\na")
        self.assertEqual(fake.tails(), [["capture-pane", "-p", "-t", ID]])

    def test_capture_without_session(self):
        t, _ = tmux((1, "", "can't find pane: x\n"))
        with self.assertRaises(engine.NoSession):
            engine.capture(t, ID)

    def test_send_line_sends_literal_then_enter(self):
        t, fake = tmux((0, "0\t\t\t1\n", ""))
        engine.send_line(t, ID, "-n hello")
        self.assertEqual(fake.tails()[1:], [["send-keys", "-t", ID, "-l", "--", "-n hello"],
                                            ["send-keys", "-t", ID, "Enter"]])

    def test_send_empty_line_is_only_enter(self):
        t, fake = tmux((0, "0\t\t\t1\n", ""))
        engine.send_line(t, ID, "")
        self.assertEqual(fake.tails()[1:], [["send-keys", "-t", ID, "Enter"]])

    def test_send_without_session(self):
        t, _ = tmux((0, "\n", ""))
        with self.assertRaises(engine.NoSession):
            engine.send_line(t, ID, "x")

    def test_resize(self):
        t, fake = tmux((0, "0\t\t\t1\n", ""))
        engine.resize_session(t, ID, 100, 40)
        self.assertEqual(fake.tails()[1:], [["resize-window", "-t", ID, "-x", "100", "-y", "40"]])

    def test_kill_ignores_missing_session(self):
        t, fake = tmux((1, "", "no server running on /tmp/tmux-1000/sock\n"))
        engine.kill_session(t, ID)
        self.assertEqual(fake.tails(), [["kill-session", "-t", ID]])
