"""In-place terminal progress display for the loader worker.

The widget renders one line and refreshes it in place (no per-chunk prints).
When stdout is not a TTY (redirected output, pytest, CI) or ``NO_PROGRESS=1``
is set, it writes nothing and the normal log lines remain the only output.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

_TRUTHY = {"1", "true", "yes", "on"}


def progress_enabled(stream: TextIO | None = None) -> bool:
    if os.getenv("NO_PROGRESS", "").strip().lower() in _TRUTHY:
        return False
    target = stream if stream is not None else sys.stdout
    isatty = getattr(target, "isatty", None)
    return bool(isatty and isatty())


class DownloadUI:
    """A single, self-refreshing download line."""

    def __init__(
        self,
        label: str,
        *,
        enabled: bool | None = None,
        stream: TextIO | None = None,
        console: Console | None = None,
    ) -> None:
        self.label = label
        self.enabled = progress_enabled(stream) if enabled is None else enabled
        self._console = console
        self._progress: Progress | None = None
        self._task: TaskID | None = None

    def start(self) -> None:
        if not self.enabled or self._progress is not None:
            return
        self._progress = Progress(
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=32),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TextColumn("[bold]{task.percentage:>3.0f}%"),
            console=self._console or Console(),
            transient=False,
        )
        self._task = self._progress.add_task(self.label, total=None)
        self._progress.start()

    def status(self, text: str) -> None:
        if self._progress is None or self._task is None:
            return
        self._progress.update(self._task, description=f"{self.label} - {text}")

    def update(self, downloaded: int, total: int | None = None) -> None:
        if self._progress is None or self._task is None:
            return
        if total is not None:
            total = max(total, downloaded)
            self._progress.update(self._task, completed=downloaded, total=total)
        else:
            self._progress.update(self._task, completed=downloaded)

    def finish(self, detail: str = "") -> None:
        if self._progress is None or self._task is None:
            return
        task = self._progress.tasks[0]
        total = task.total if task.total is not None else task.completed
        if total == 0:
            total = None
        completed = task.completed if total is None else max(task.completed, total)
        description = f"{self.label} - {detail}" if detail else self.label
        self._progress.update(
            self._task,
            description=description,
            completed=completed,
            total=total,
        )
        self._progress.stop()
        self._progress = None
        self._task = None

    def fail(self, detail: str = "failed") -> None:
        if self._progress is None or self._task is None:
            return
        self._progress.update(
            self._task,
            description=f"[red]{self.label} - {detail}[/red]",
        )
        self._progress.stop()
        self._progress = None
        self._task = None

    def close(self) -> None:
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._task = None
