import signal
import unittest

from _load import load
from fakes import FakeClock, FakeRun

engine = load()

ID = "0123456789abcdef0123456789abcdef"
ALIVE = (0, "0\t\t\t4242\n", "")
DEAD_130 = (0, "1\t\t2\t4242\n", "")
DEAD_143 = (0, "1\t\t15\t4242\n", "")
DEAD_137 = (0, "1\t\t9\t4242\n", "")
OK = (0, "", "")


def stop(answers):
    fake = FakeRun(answers)
    clock = FakeClock()
    kills = []
    tmux = engine.Tmux("sock", "/plug/tmux.conf", run=fake)
    result = engine.stop_session(tmux, ID, kill=lambda pid, sig: kills.append((pid, sig)),
                                 sleep=clock.sleep, clock=clock, grace=2.0, poll=0.5)
    return result, fake, kills, clock


class StopTest(unittest.TestCase):
    def test_ctrl_c_is_enough(self):
        # state (alive) → send C-c → poll: alive, dead(130)
        result, fake, kills, _ = stop([ALIVE, OK, ALIVE, DEAD_130])
        self.assertEqual(result, {"dead": True, "exit": 130})
        self.assertEqual(fake.tails()[1], ["send-keys", "-t", ID, "C-c"])
        self.assertEqual(kills, [])

    def test_escalates_to_sigterm_on_process_group(self):
        # C-c ignored for 2 s (4 polls of 0.5 s), then SIGTERM works
        # polls at t = 0, 0.5, 1.0, 1.5, 2.0 all alive → grace exhausted → SIGTERM → dead
        result, _, kills, _ = stop([ALIVE, OK] + [ALIVE] * 5 + [DEAD_143])
        self.assertEqual(result, {"dead": True, "exit": 143})
        self.assertEqual(kills, [(-4242, signal.SIGTERM)])

    def test_escalates_to_sigkill(self):
        # two exhausted grace periods (5 polls each) → SIGTERM → SIGKILL → dead
        result, _, kills, clock = stop([ALIVE, OK] + [ALIVE] * 10 + [DEAD_137])
        self.assertEqual(result, {"dead": True, "exit": 137})
        self.assertEqual(kills, [(-4242, signal.SIGTERM), (-4242, signal.SIGKILL)])
        self.assertLessEqual(clock.now, 4.6)

    def test_already_dead_returns_state_without_signals(self):
        result, fake, kills, _ = stop([(0, "1\t3\t\t4242\n", "")])
        self.assertEqual(result, {"dead": True, "exit": 3})
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(kills, [])

    def test_missing_session_raises(self):
        with self.assertRaises(engine.NoSession):
            stop([(1, "", "can't find session: x\n")])

    def test_process_already_gone_is_not_an_error(self):
        # kill raises ProcessLookupError; the pane reports dead on the next poll
        fake = FakeRun([ALIVE, OK] + [ALIVE] * 5 + [DEAD_143])
        clock = FakeClock()

        def kill(pid, sig):
            raise ProcessLookupError()
        tmux = engine.Tmux("sock", "/plug/tmux.conf", run=fake)
        result = engine.stop_session(tmux, ID, kill=kill, sleep=clock.sleep, clock=clock, grace=2.0, poll=0.5)
        self.assertEqual(result, {"dead": True, "exit": 143})
