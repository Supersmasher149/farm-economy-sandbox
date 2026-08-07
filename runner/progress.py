"""Live progress reporting for long batch runs.

A balance batch routinely runs tens of thousands of simulations, which for
minutes at a time looks indistinguishable from a hang. This renders a single
self-overwriting status line -- bar, percent, completed/total, throughput,
elapsed, and estimated time remaining -- while the batch streams results.

Two deliberate constraints:

* The line goes to **stderr**, so `batch > report.txt` and any piping of the
  report on stdout stay byte-for-byte clean.
* The reporter is a pure pass-through over the RunResult stream: it yields
  exactly the results it receives, in order, and never touches simulation
  state or the RNG. Turning progress on or off cannot change a batch's
  outcome for a given seed.

Throughput and ETA are computed from the overall average rate rather than an
instantaneous one. Results arrive from the process pool in bursts (one
dispatch window at a time), so a short-window rate would swing wildly while
the average converges quickly and stays readable.

Counting happens whether or not the line is being drawn, so callers can
report final timing stats even when output is not a terminal.
"""

import math
import shutil
import sys
import time

BAR_WIDTH = 28
MIN_REDRAW_SECONDS = 0.1

_BLOCKS = ("█", "░")  # full block, light shade
_ASCII_BLOCKS = ("#", "-")


def format_duration(seconds) -> str:
    """Render a duration as MM:SS, or H:MM:SS past an hour."""
    if seconds is None or seconds != seconds or seconds in (float("inf"), float("-inf")):
        return "--:--"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_rate(rate) -> str:
    """Render simulations/second, keeping precision only where it informs."""
    if rate is None or rate != rate or rate in (float("inf"), float("-inf")):
        return "--"
    if rate >= 100:
        return f"{rate:,.0f}"
    if rate >= 10:
        return f"{rate:.1f}"
    return f"{rate:.2f}"


def render_line(completed, total, elapsed, width=None, bar_width=BAR_WIDTH, ascii_only=False):
    """Build the status line for a given point in a batch.

    Kept free of I/O and of wall-clock reads so the exact rendering can be
    asserted in tests from plain numbers.
    """
    fraction = 1.0 if total <= 0 else min(max(completed / total, 0.0), 1.0)
    rate = completed / elapsed if elapsed > 0 and completed > 0 else None
    remaining = (total - completed) / rate if rate else None
    if completed >= total:
        remaining = 0.0

    filled, empty = _ASCII_BLOCKS if ascii_only else _BLOCKS
    bar_width = max(1, bar_width)
    filled_cells = int(fraction * bar_width)
    bar = filled * filled_cells + empty * (bar_width - filled_cells)

    total_text = f"{total:,}"
    counts = f"{completed:,}".rjust(len(total_text)) + f"/{total_text}"

    # Floored, not rounded: 9,999 of 10,000 runs must not read as 100%.
    percent = math.floor(fraction * 1000) / 10

    line = (
        f"[{bar}] {percent:5.1f}% | {counts} | "
        f"{format_rate(rate)} sim/s | {format_duration(elapsed)} elapsed | "
        f"{format_duration(remaining)} left"
    )
    if width and len(line) > width:
        line = line[:width]
    return line


class ProgressReporter:
    """Counts completed simulations and draws the status line.

    `enabled=None` means auto: draw only when the stream is a terminal, so
    redirected output and CI logs do not fill up with carriage returns.
    """

    def __init__(
        self,
        total,
        stream=None,
        enabled=None,
        min_interval=MIN_REDRAW_SECONDS,
        clock=time.monotonic,
        bar_width=BAR_WIDTH,
    ):
        self.total = max(0, int(total))
        self.stream = sys.stderr if stream is None else stream
        self.enabled = _is_interactive(self.stream) if enabled is None else bool(enabled)
        self.min_interval = min_interval
        self.bar_width = bar_width
        self._clock = clock
        self._ascii_only = not _supports_block_glyphs(self.stream)
        self.completed = 0
        self._started_at = None
        self._final_elapsed = None
        self._last_draw = None
        self._last_width = 0
        self._drew_anything = False

    @property
    def elapsed(self) -> float:
        """Seconds since start; frozen at the final total once finished."""
        if self._started_at is None:
            return self._final_elapsed or 0.0
        return max(0.0, self._clock() - self._started_at)

    @property
    def rate(self):
        """Average simulations per second, or None before anything finishes."""
        elapsed = self.elapsed
        if elapsed <= 0 or self.completed <= 0:
            return None
        return self.completed / elapsed

    def start(self):
        self._started_at = self._clock()
        self._final_elapsed = None
        self._draw(force=True)
        return self

    def advance(self, count=1):
        self.completed += count
        self._draw()

    def finish(self):
        """Draw the final state and release the line."""
        if self._started_at is None:
            return
        self._draw(force=True)
        if self._drew_anything:
            self.stream.write("\n")
            self._flush()
        self._final_elapsed = self.elapsed
        self._started_at = None

    def track(self, iterable):
        """Yield every item of `iterable`, counting each one as it passes."""
        self.start()
        try:
            for item in iterable:
                self.advance()
                yield item
        finally:
            self.finish()

    def _draw(self, force=False):
        if not self.enabled or self.total <= 0:
            return
        now = self._clock()
        if not force and self._last_draw is not None and now - self._last_draw < self.min_interval:
            return
        self._last_draw = now
        width = _terminal_width(self.stream)
        line = render_line(
            self.completed,
            self.total,
            self.elapsed,
            width=width - 1 if width else None,
            bar_width=self.bar_width,
            ascii_only=self._ascii_only,
        )
        # Pad to the previous line's width so a shorter line cannot leave
        # stale characters behind from the one it overwrites.
        self.stream.write("\r" + line.ljust(self._last_width))
        self._last_width = len(line)
        self._flush()
        self._drew_anything = True

    def _flush(self):
        flush = getattr(self.stream, "flush", None)
        if flush is not None:
            flush()


def _is_interactive(stream) -> bool:
    isatty = getattr(stream, "isatty", None)
    try:
        return bool(isatty and isatty())
    except ValueError:  # closed stream
        return False


def _supports_block_glyphs(stream) -> bool:
    encoding = getattr(stream, "encoding", None) or ""
    try:
        "".join(_BLOCKS).encode(encoding or "utf-8")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def _terminal_width(stream):
    if not _is_interactive(stream):
        return None
    try:
        return shutil.get_terminal_size().columns
    except (OSError, ValueError):
        return None
