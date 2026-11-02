from collections.abc import Callable
from shutil import get_terminal_size
from threading import Event, Lock, Thread
from time import monotonic
from typing import TextIO
import sys


StageCallback = Callable[[str], None]
DetailCallback = Callable[[int, int, str], None]


class Progress:
    """Показывает завершённые этапы и активность операций неизвестной длины."""

    def __init__(self, total: int, *, enabled: bool = True, stream: TextIO | None = None):
        self.total = total
        self.enabled = enabled
        self.stream = stream if stream is not None else sys.stderr
        self.completed = 0
        self.label = ""
        self.detail = ""
        self.started = monotonic()
        self._interactive = enabled and self.stream.isatty()
        self._lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._width = 0

    def __enter__(self):
        if self._interactive:
            self._thread = Thread(target=self._animate, daemon=True)
            self._thread.start()
        return self

    def start_stage(self, label: str) -> None:
        with self._lock:
            if self.label:
                self.completed += 1
            self.label = label
            self.detail = ""
            self.started = monotonic()
            if self.enabled and not self._interactive:
                self.stream.write(f"[{self.completed + 1}/{self.total}] {label}\n")
                self.stream.flush()

    def update_detail(self, completed: int, total: int, label: str) -> None:
        with self._lock:
            self.detail = f"{completed}/{total}: {label}"

    def _animate(self) -> None:
        frame = 0
        while not self._stop.wait(0.1):
            with self._lock:
                if self.label:
                    self._render("|/-\\"[frame % 4])
            frame += 1

    def _render(self, marker: str) -> None:
        width = max(40, get_terminal_size(fallback=(100, 24)).columns - 1)
        size = 20
        filled = size * self.completed // self.total
        bar = "=" * filled + " " * (size - filled)
        elapsed = int(monotonic() - self.started)
        detail = f" — {self.detail}" if self.detail else ""
        prefix = f"[{bar}] {self.completed}/{self.total} {marker} "
        suffix = f" ({elapsed // 60:02d}:{elapsed % 60:02d})"
        message = self.label + detail
        available = max(1, width - len(prefix) - len(suffix))
        if len(message) > available:
            message = message[:available - 1] + "…"
        line = prefix + message + suffix
        self.stream.write("\r" + line.ljust(min(self._width, width)))
        self.stream.flush()
        self._width = len(line)

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        with self._lock:
            if exc_type is None and self.label:
                self.completed += 1
            if self._interactive:
                self._render("✓" if exc_type is None else "×")
                self.stream.write("\n")
                self.stream.flush()
