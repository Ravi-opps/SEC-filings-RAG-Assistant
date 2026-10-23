import io

from rich.console import Console

from terminal_ui import DownloadUI, progress_enabled


class TtyBuffer(io.StringIO):
    def isatty(self):
        return True


def test_progress_is_disabled_for_non_tty_streams():
    ui = DownloadUI("AAPL 10-K", stream=io.StringIO())

    assert ui.enabled is False
    ui.start()
    ui.update(10, 100)
    ui.finish("done")
    ui.close()


def test_no_progress_env_overrides_a_tty(monkeypatch):
    monkeypatch.setenv("NO_PROGRESS", "1")

    assert progress_enabled(TtyBuffer()) is False


def test_progress_renders_one_final_line_with_percent():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    ui = DownloadUI("AAPL 10-K", enabled=True, console=console)

    ui.start()
    for done in range(0, 1001, 100):
        ui.update(done, 1000)
    ui.finish("0000320193-25-000079 - 1,000 bytes")

    output = buffer.getvalue()
    assert output.count("\n") == 1
    assert "AAPL 10-K" in output
    assert "100%" in output
    assert "0000320193-25-000079" in output


def test_progress_finish_without_content_length_still_completes():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    ui = DownloadUI("AAPL 10-K", enabled=True, console=console)

    ui.start()
    ui.update(512)
    ui.update(2048)
    ui.finish()

    output = buffer.getvalue()
    assert output.count("\n") == 1
    assert "AAPL 10-K" in output
    assert "100%" in output


def test_progress_fail_marks_the_line_and_stops():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    ui = DownloadUI("AAPL 10-K", enabled=True, console=console)

    ui.start()
    ui.update(500, 1000)
    ui.fail()
    ui.close()

    output = buffer.getvalue()
    assert "failed" in output
    assert output.count("\n") == 1


def test_progress_finish_completes_when_wire_total_is_slightly_larger():
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)
    ui = DownloadUI("AAPL 10-K", enabled=True, console=console)

    ui.start()
    ui.update(111520, 111657)
    ui.finish("0000320193-25-000079 - 1,520,323 bytes")

    output = buffer.getvalue()
    assert output.count("\n") == 1
    assert "100%" in output
