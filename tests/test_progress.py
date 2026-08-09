"""Tests for the batch progress line.

Two things matter here. First, the reporter must be a transparent
pass-through: a batch's results, and their order, cannot depend on whether
progress is being drawn -- that would put reporting in the way of the
determinism guarantee. Second, the numbers on the line (percent, counts,
rate, elapsed, ETA) have to be right, so they are asserted from a fake clock
rather than from wall time.
"""

import io

import pytest

import main
from runner.progress import (
    ProgressReporter,
    format_duration,
    format_rate,
    render_line,
)


class FakeClock:
    """Monotonic clock that only moves when a test says so."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_reporter(total, clock=None, enabled=True, **kwargs):
    stream = io.StringIO()
    reporter = ProgressReporter(
        total,
        stream=stream,
        enabled=enabled,
        clock=clock or FakeClock(),
        min_interval=0,
        **kwargs,
    )
    return reporter, stream


# --- duration and rate formatting ----------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "00:00"),
        (9.4, "00:09"),
        (59.6, "01:00"),
        (125, "02:05"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
        (-5, "00:00"),
        (None, "--:--"),
        (float("inf"), "--:--"),
        (float("nan"), "--:--"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        (1523.4, "1,523"),
        (100, "100"),
        (42.55, "42.5"),
        (1.234, "1.23"),
        (0, "0.00"),
        (None, "--"),
        (float("inf"), "--"),
    ],
)
def test_format_rate(rate, expected):
    assert format_rate(rate) == expected


# --- the rendered line ----------------------------------------------------


def test_render_line_reports_every_requested_metric():
    line = render_line(completed=250, total=1000, elapsed=10.0, bar_width=10)
    # 25% of 1000 runs in 10s -> 25 sim/s, 750 left -> 30s remaining.
    assert line == "[██░░░░░░░░]  25.0% |   250/1,000 | 25.0 sim/s | 00:10 elapsed | 00:30 left"


def test_render_line_bar_fills_with_progress():
    empty = render_line(0, 100, 0.0, bar_width=10)
    half = render_line(50, 100, 1.0, bar_width=10)
    full = render_line(100, 100, 1.0, bar_width=10)
    assert empty.startswith("[░░░░░░░░░░]")
    assert half.startswith("[█████░░░░░]")
    assert full.startswith("[██████████]")


def test_render_line_shows_no_eta_before_the_first_result():
    assert "--:-- left" in render_line(0, 100, 0.0, bar_width=4)


def test_render_line_zeroes_eta_at_completion():
    assert "00:00 left" in render_line(100, 100, 5.0, bar_width=4)


def test_render_line_counts_are_width_aligned():
    """Counts are padded so the line does not jitter as the number grows."""
    assert "     7/10,000" in render_line(7, 10_000, 1.0, bar_width=4)
    assert " 9,999/10,000" in render_line(9_999, 10_000, 1.0, bar_width=4)


def test_percent_is_floored_so_it_only_reads_100_when_done():
    assert " 99.9% " in render_line(9_999, 10_000, 1.0, bar_width=4)
    assert "100.0% " in render_line(10_000, 10_000, 1.0, bar_width=4)


def test_render_line_truncates_to_terminal_width():
    assert len(render_line(5, 10, 1.0, width=30, bar_width=28)) == 30


def test_render_line_can_avoid_block_glyphs():
    line = render_line(50, 100, 1.0, bar_width=10, ascii_only=True)
    assert line.startswith("[#####-----]")


def test_render_line_handles_an_empty_total():
    assert "100.0%" in render_line(0, 0, 1.0, bar_width=4)


# --- pass-through behavior ------------------------------------------------


def test_track_yields_every_item_unchanged_and_in_order():
    reporter, _ = make_reporter(5)
    items = [{"run": i} for i in range(5)]
    tracked = list(reporter.track(iter(items)))
    assert tracked == items
    assert all(a is b for a, b in zip(tracked, items, strict=True))


def test_track_streams_lazily_rather_than_materializing():
    """The reporter must not defeat run_batch's bounded-memory streaming."""
    produced = []

    def source():
        for i in range(4):
            produced.append(i)
            yield i

    reporter, _ = make_reporter(4)
    stream = reporter.track(source())
    assert produced == []
    next(stream)
    assert produced == [0]


def test_track_counts_each_result():
    reporter, _ = make_reporter(3)
    list(reporter.track(iter([1, 2, 3])))
    assert reporter.completed == 3


def test_disabled_reporter_writes_nothing_but_still_counts():
    reporter, stream = make_reporter(3, enabled=False)
    assert list(reporter.track(iter([1, 2, 3]))) == [1, 2, 3]
    assert stream.getvalue() == ""
    assert reporter.completed == 3


def test_track_finishes_the_line_when_the_source_raises():
    def exploding():
        yield 1
        raise RuntimeError("batch run failed")

    reporter, stream = make_reporter(2)
    with pytest.raises(RuntimeError):
        list(reporter.track(exploding()))
    assert stream.getvalue().endswith("\n")


# --- timing ---------------------------------------------------------------


def test_elapsed_and_rate_track_the_clock():
    clock = FakeClock()
    reporter, _ = make_reporter(10, clock=clock)
    reporter.start()
    clock.advance(4.0)
    reporter.advance(8)
    assert reporter.elapsed == 4.0
    assert reporter.rate == 2.0


def test_rate_is_unknown_before_the_first_result():
    reporter, _ = make_reporter(10)
    reporter.start()
    assert reporter.rate is None


def test_final_timing_survives_finish():
    """cmd_batch reports elapsed/rate after the stream is exhausted."""
    clock = FakeClock()
    reporter, _ = make_reporter(4, clock=clock)

    def source():
        for i in range(4):
            clock.advance(0.5)
            yield i

    list(reporter.track(source()))
    clock.advance(60.0)  # time spent writing reports must not count
    assert reporter.elapsed == 2.0
    assert reporter.rate == 2.0


# --- drawing --------------------------------------------------------------


def test_output_redraws_in_place_on_one_line():
    reporter, stream = make_reporter(3)
    list(reporter.track(iter([1, 2, 3])))
    output = stream.getvalue()
    assert output.count("\n") == 1
    assert output.endswith("\n")
    assert output.startswith("\r")


def test_final_draw_shows_completion():
    reporter, stream = make_reporter(3)
    list(reporter.track(iter([1, 2, 3])))
    last_line = stream.getvalue().rstrip("\n").split("\r")[-1]
    assert "100.0%" in last_line
    assert "3/3" in last_line


def test_redraws_are_throttled_by_min_interval():
    clock = FakeClock()
    stream = io.StringIO()
    reporter = ProgressReporter(100, stream=stream, enabled=True, clock=clock, min_interval=1.0)
    reporter.start()
    for _ in range(50):
        clock.advance(0.01)
        reporter.advance()
    # One draw at start, none of the 50 throttled advances, one at finish.
    drawn = stream.getvalue().count("\r")
    reporter.finish()
    assert drawn == 1
    assert stream.getvalue().count("\r") == 2


def test_shorter_lines_do_not_leave_stale_characters():
    """A redraw pads to the previous width instead of clearing the line."""
    reporter, stream = make_reporter(1_000_000)
    reporter.start()
    long_line_width = len(stream.getvalue().lstrip("\r"))
    reporter.advance(1_000_000)
    reporter.finish()
    for chunk in stream.getvalue().split("\r")[1:]:
        assert len(chunk.rstrip("\n")) >= long_line_width


def test_auto_enable_follows_the_stream():
    class NotATerminal(io.StringIO):
        def isatty(self):
            return False

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    assert ProgressReporter(10, stream=NotATerminal()).enabled is False
    assert ProgressReporter(10, stream=Terminal()).enabled is True


def test_non_utf8_stream_falls_back_to_ascii_glyphs():
    class AsciiStream(io.StringIO):
        encoding = "ascii"

    stream = AsciiStream()
    reporter = ProgressReporter(10, stream=stream, enabled=True, min_interval=0)
    reporter.start()
    reporter.advance(5)
    assert "#" in stream.getvalue()
    assert "█" not in stream.getvalue()


# --- CLI wiring -----------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], None),  # auto: drawn only when stderr is a terminal
        (["--progress"], True),
        (["--no-progress"], False),
    ],
)
def test_batch_progress_flag(argv, expected):
    args = main.build_parser().parse_args(["batch", *argv])
    assert args.progress is expected
