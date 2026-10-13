from __future__ import annotations

from typing import Callable


class StepProgress:
    """Report measured progress as "Step k/N: label", never a made-up percentage.

    `step()` starts the next stage with no bar (its length is unknown). Inside a stage,
    `count()` reports finished units (e.g. 3/9 chunks) and `fraction()` reports a measured
    share of the work (e.g. seconds encoded / video length). Wraps a gr.Progress or any
    callable with the same signature; with none it does nothing.
    """

    def __init__(self, progress: Callable | None = None, steps: int = 1):
        self._progress = progress
        self.steps = max(1, int(steps))
        self.index = 0
        self.label = ""

    def step(self, label: str) -> None:
        self.index = min(self.index + 1, self.steps)
        self.label = label
        self._emit(None)

    def count(self, done: int, total: int, unit: str = "steps") -> None:
        self._emit((int(done), int(total)), unit)

    def fraction(self, value: float) -> None:
        self._emit(max(0.0, min(1.0, float(value))))

    def _emit(self, value, unit: str = "steps") -> None:
        if self._progress is None:
            return
        desc = f"Step {self.index}/{self.steps}: {self.label}" if self.steps > 1 else self.label
        self._progress(value, desc=desc, unit=unit)


NO_PROGRESS = StepProgress()
