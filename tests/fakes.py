"""Test doubles for the engine: a subprocess.run that replays answers, and a clock."""
import subprocess


class FakeRun:
    def __init__(self, answers=None):
        self.calls = []
        self.answers = list(answers or [])

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        code, out, err = self.answers.pop(0) if self.answers else (0, "", "")
        return subprocess.CompletedProcess(argv, code, out, err)

    def tails(self):
        """Each recorded argv without the fixed 'tmux -L sock -f conf' prefix."""
        return [call[5:] for call in self.calls]


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
