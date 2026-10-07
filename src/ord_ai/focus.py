"""Press the loading-screen key in the background, never taking focus from the user."""

import ctypes
from ctypes import wintypes
from typing import Final, cast

GAME_WINDOW: Final = "Warcraft III"
KEY_DOWN: Final = 0x0100
KEY_UP: Final = 0x0101
SPACE: Final = 0x20
_SPACE_SCAN: Final = 0x39
_KEY_UP_FLAGS: Final = 0xC0 << 24

_user32: Final = ctypes.WinDLL("user32", use_last_error=True)
_find_window = ctypes.WINFUNCTYPE(wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)(
    ("FindWindowW", _user32)
)
_post = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)(("PostMessageW", _user32))
_is_iconic = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND)(("IsIconic", _user32))
_show_window = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_int)(
    ("ShowWindow", _user32)
)
SHOW_NO_ACTIVATE: Final = 4


def press_loading_screen_key() -> None:
    """Post one space press to the Warcraft III window, if it exists.

    The single-player "press any key" screen accepts a posted key while another window has
    focus (checked in the real game), so focus never moves. Keys posted while the map is
    still loading are ignored; the caller repeats until the map answers. A minimized window
    ignores them too, so it is restored first without being activated (SW_SHOWNOACTIVATE).
    """
    window = cast("int | None", _find_window(None, GAME_WINDOW))
    if window is None:
        return
    if cast("int", _is_iconic(window)):
        _ = cast("int", _show_window(window, SHOW_NO_ACTIVATE))
    _ = cast("int", _post(window, KEY_DOWN, SPACE, 1 | _SPACE_SCAN << 16))
    _ = cast("int", _post(window, KEY_UP, SPACE, 1 | _SPACE_SCAN << 16 | _KEY_UP_FLAGS))
