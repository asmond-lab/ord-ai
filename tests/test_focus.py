import pytest

from ord_ai import focus

Posted = list[tuple[int, int, int]]


def fake_windows(
    monkeypatch: pytest.MonkeyPatch, window: int | None, minimized: bool = False
) -> Posted:
    posted: Posted = []

    def find(_class: str | None, title: str) -> int | None:
        return window if title == focus.GAME_WINDOW else None

    def post(target: int, message: int, key: int, _flags: int) -> int:
        posted.append((target, message, key))
        return 1

    def is_iconic(_target: int) -> int:
        return int(minimized)

    def show(target: int, command: int) -> int:
        posted.append((target, -1, command))
        return 1

    monkeypatch.setattr(focus, "_find_window", find)
    monkeypatch.setattr(focus, "_post", post)
    monkeypatch.setattr(focus, "_is_iconic", is_iconic)
    monkeypatch.setattr(focus, "_show_window", show)
    return posted


def test_the_key_is_posted_to_the_game_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    posted = fake_windows(monkeypatch, window=42)

    # When
    focus.press_loading_screen_key()

    # Then
    assert posted == [(42, focus.KEY_DOWN, focus.SPACE), (42, focus.KEY_UP, focus.SPACE)]


def test_a_minimized_game_window_is_restored_without_activation_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: the loading screen ignores posted keys while the window is minimized.
    posted = fake_windows(monkeypatch, window=42, minimized=True)

    # When
    focus.press_loading_screen_key()

    # Then
    assert posted == [
        (42, -1, focus.SHOW_NO_ACTIVATE),
        (42, focus.KEY_DOWN, focus.SPACE),
        (42, focus.KEY_UP, focus.SPACE),
    ]


def test_nothing_is_sent_without_a_game_window(monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    posted = fake_windows(monkeypatch, window=None)

    # When
    focus.press_loading_screen_key()

    # Then
    assert posted == []
