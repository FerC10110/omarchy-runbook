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
        """Each recorded tmux argv without the fixed 'tmux -L sock -f conf' prefix.
        Non-tmux calls (e.g. the systemctl calls that add/update/remove now also
        issue via sync_schedules) are not tmux invocations and are excluded."""
        return [call[5:] for call in self.calls if call[:1] == ["tmux"]]


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
