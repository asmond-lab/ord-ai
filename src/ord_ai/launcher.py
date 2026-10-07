"""Start Warcraft III through Battle.net so the launcher's login is reused."""

import ctypes
import json
import os
import shutil
import struct
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from types import TracebackType
from typing import Final, Self, cast, final

from ord_ai.focus import press_loading_screen_key

BATTLE_NET: Final = Path(r"C:\Program Files (x86)\Battle.net\Battle.net.exe")
ARGUMENTS_KEY: Final = "AdditionalLaunchArguments"
GAME: Final = "warcraft iii.exe"
LAUNCHER: Final = "battle.net.exe"
# The Battle.net arguments every session sets, followed by the quoted map path.
LAUNCH_PREFIX: Final = "-launch -nowfpause -windowmode windowed -loadfile "
_PROCESS_SNAPSHOT: Final = 0x2
_PROCESS_TERMINATE: Final = 0x0001
_WM_CLOSE: Final = 0x0010
_WM_LBUTTONDOWN: Final = 0x0201
_WM_LBUTTONUP: Final = 0x0202
_MK_LBUTTON: Final = 0x0001
_WM_MOUSEMOVE: Final = 0x0200
_SMTO_ABORTIFHUNG: Final = 0x0002
_SEND_MS: Final = 2000
# WM_CLOSE opens the in-game quit prompt (seen 1.1 s later); look for it after this long.
PROMPT_SECONDS: Final = 3.0
PROMPT_MATCH: Final = 10.0
QUIT_PROMPT: Final = Path(__file__).with_name("quit_prompt.bin")
_PROMPT_COLUMNS: Final = 48
_PROMPT_ROWS: Final = 24
# How long a window close request may take before the game is terminated instead.
GRACEFUL_CLOSE_SECONDS: Final = 20.0
_SHOW_NO_ACTIVATE: Final = 7
_SHELL_EXECUTE_ERROR_LIMIT: Final = 32


class LaunchError(RuntimeError):
    """Battle.net could not start the requested Warcraft III session."""

    def __init__(self, detail: str) -> None:
        """Retain the failed launch step."""
        self.detail: str = detail
        super().__init__(detail)


@final
class _ProcessEntry(ctypes.Structure):
    _fields_ = (
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    )


_kernel32: Final = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32: Final = ctypes.WinDLL("shell32", use_last_error=True)
_user32: Final = ctypes.WinDLL("user32", use_last_error=True)
_WindowVisitor: Final = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_enum_windows: Final = ctypes.WINFUNCTYPE(wintypes.BOOL, _WindowVisitor, wintypes.LPARAM)(
    ("EnumWindows", _user32)
)
_window_pid: Final = ctypes.WINFUNCTYPE(
    wintypes.DWORD, wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
)(("GetWindowThreadProcessId", _user32))
_post: Final = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)(("PostMessageW", _user32))
_gdi32: Final = ctypes.WinDLL("gdi32")
_create_dc: Final = ctypes.WINFUNCTYPE(wintypes.HDC, wintypes.HDC)(("CreateCompatibleDC", _gdi32))
_create_bitmap: Final = ctypes.WINFUNCTYPE(
    wintypes.HBITMAP, wintypes.HDC, ctypes.c_int, ctypes.c_int
)(("CreateCompatibleBitmap", _gdi32))
_select: Final = ctypes.WINFUNCTYPE(wintypes.HGDIOBJ, wintypes.HDC, wintypes.HGDIOBJ)(
    ("SelectObject", _gdi32)
)
_dib_bits: Final = ctypes.WINFUNCTYPE(
    ctypes.c_int,
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
)(("GetDIBits", _gdi32))
_delete_object: Final = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HGDIOBJ)(
    ("DeleteObject", _gdi32)
)
_delete_dc: Final = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HDC)(("DeleteDC", _gdi32))
_PW_RENDERFULLCONTENT: Final = 3
_client_rect: Final = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, ctypes.POINTER(wintypes.RECT)
)(("GetClientRect", _user32))
_print_window: Final = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HWND, wintypes.HDC, wintypes.UINT
)(("PrintWindow", _user32))
_get_dc: Final = ctypes.WINFUNCTYPE(wintypes.HDC, wintypes.HWND)(("GetDC", _user32))
_release_dc: Final = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.HDC)(
    ("ReleaseDC", _user32)
)
_send_timeout: Final = ctypes.WINFUNCTYPE(
    wintypes.LPARAM,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_size_t),
)(("SendMessageTimeoutW", _user32))
_visible: Final = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND)(("IsWindowVisible", _user32))
_title: Final = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)(
    ("GetWindowTextW", _user32)
)
_snapshot: Final = ctypes.WINFUNCTYPE(wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)(
    ("CreateToolhelp32Snapshot", _kernel32)
)
_first: Final = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, ctypes.c_void_p)(
    ("Process32FirstW", _kernel32)
)
_next: Final = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, ctypes.c_void_p)(
    ("Process32NextW", _kernel32)
)
_close: Final = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE)(("CloseHandle", _kernel32))
_open: Final = ctypes.WINFUNCTYPE(wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)(
    ("OpenProcess", _kernel32)
)
_terminate: Final = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.UINT)(
    ("TerminateProcess", _kernel32)
)
_execute: Final = ctypes.WINFUNCTYPE(
    ctypes.c_void_p,
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.c_int,
)(("ShellExecuteW", _shell32))


def _pids(image: str) -> list[int]:
    handle = cast("int | None", _snapshot(_PROCESS_SNAPSHOT, 0))
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise LaunchError(detail="Windows process snapshot failed")
    entry = _ProcessEntry()
    entry.dwSize = ctypes.sizeof(_ProcessEntry)
    found: list[int] = []
    try:
        more = cast("int", _first(handle, ctypes.byref(entry)))
        while more:
            if cast("str", entry.szExeFile).lower() == image:
                found.append(cast("int", entry.th32ProcessID))
            more = cast("int", _next(handle, ctypes.byref(entry)))
    finally:
        _ = cast("int", _close(handle))
    return found


def _count(image: str) -> int:
    return len(_pids(image))


def _kill_pids(pids: set[int]) -> None:
    for pid in pids:
        handle = cast("int | None", _open(_PROCESS_TERMINATE, wintypes.BOOL(0), pid))
        if handle is None:
            continue
        try:
            _ = cast("int", _terminate(handle, 1))
        finally:
            _ = cast("int", _close(handle))


def _request_close(pids: set[int]) -> list[dict[str, object]]:
    """Post WM_CLOSE to every top-level window of these processes; one record per window."""
    asked: list[dict[str, object]] = []

    def visit(window: int, _: int) -> bool:
        owner = wintypes.DWORD()
        _ = cast("int", _window_pid(window, ctypes.byref(owner)))
        if owner.value in pids:
            posted = bool(cast("int", _post(window, _WM_CLOSE, 0, 0)))
            error = 0 if posted else ctypes.get_last_error()
            asked.append({"hwnd": window, "pid": owner.value, "posted": posted, "error": error})
        return True

    _ = cast("int", _enum_windows(_WindowVisitor(visit), 0))
    return asked


def _windows(pids: set[int]) -> list[dict[str, object]]:
    """The visible top-level windows of these processes with their titles."""
    found: list[dict[str, object]] = []

    def visit(window: int, _: int) -> bool:
        owner = wintypes.DWORD()
        _ = cast("int", _window_pid(window, ctypes.byref(owner)))
        if owner.value in pids and cast("int", _visible(window)):
            title = ctypes.create_unicode_buffer(256)
            _ = cast("int", _title(window, title, 256))
            found.append({"hwnd": window, "pid": owner.value, "title": cast("str", title.value)})
        return True

    _ = cast("int", _enum_windows(_WindowVisitor(visit), 0))
    return found


def _capture(window: int) -> tuple[int, int, bytes] | None:
    """Width, height and top-down 32-bit BGRX pixels of this window's client area."""
    rect = wintypes.RECT()
    if not cast("int", _client_rect(window, ctypes.byref(rect))):
        return None
    width, height = rect.right, rect.bottom
    if width <= 0 or height <= 0:
        return None
    screen = cast("int", _get_dc(None))
    memory = cast("int", _create_dc(screen))
    bitmap = cast("int", _create_bitmap(screen, width, height))
    try:
        _ = cast("int", _select(memory, bitmap))
        printed = bool(cast("int", _print_window(window, memory, _PW_RENDERFULLCONTENT)))
        pixels = ctypes.create_string_buffer(width * height * 4)
        info = ctypes.create_string_buffer(_bitmap_header(width, height), 44)
        rows = cast("int", _dib_bits(memory, bitmap, 0, height, pixels, info, 0))
    finally:
        _ = cast("int", _delete_object(bitmap))
        _ = cast("int", _delete_dc(memory))
        _ = cast("int", _release_dc(None, screen))
    if not printed or rows != height:
        return None
    return width, height, pixels.raw


def _bitmap_header(width: int, height: int) -> bytes:
    # BITMAPINFOHEADER: 40 bytes, negative height = top-down rows, 32 bits, BI_RGB.
    return struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)


def _snapshot_window(window: int, path: Path) -> bool:
    """Save what this window shows (PrintWindow of its client area) as a 32-bit BMP."""
    captured = _capture(window)
    if captured is None:
        return False
    width, height, pixels = captured
    file_header = struct.pack("<2sIHHI", b"BM", 14 + 40 + len(pixels), 0, 0, 14 + 40)
    _ = path.write_bytes(file_header + _bitmap_header(width, height) + pixels)
    return True


def prompt_distance(width: int, height: int, pixels: bytes) -> float:
    """Mean grey difference of the prompt box area from the recorded quit prompt (0 = same).

    The box is centred and scales with the window height (1920x1044 client: x 695..1225,
    y 278..558). The reference (quit_prompt.bin, 48x24 grey samples) was taken from the
    2026-09-30 close-observer frames of game 62; those frames measured 0, the frame before
    the prompt 29.4 and ten in-game battlefield frames 43.5 to 95.8.
    """
    reference = QUIT_PROMPT.read_bytes()
    left, right = width / 2 - 265 / 1044 * height, width / 2 + 265 / 1044 * height
    top, bottom = 278 / 1044 * height, 558 / 1044 * height
    total = 0
    for row in range(_PROMPT_ROWS):
        for column in range(_PROMPT_COLUMNS):
            x = int(left + (column + 0.5) * (right - left) / _PROMPT_COLUMNS)
            y = int(top + (row + 0.5) * (bottom - top) / _PROMPT_ROWS)
            offset = (y * width + x) * 4
            grey = (pixels[offset] + pixels[offset + 1] + pixels[offset + 2]) // 3
            total += abs(grey - reference[row * _PROMPT_COLUMNS + column])
    return total / len(reference)


def _prompt_state(window: int) -> float | None:
    captured = _capture(window)
    return None if captured is None else prompt_distance(*captured)


def _click_quit(window: int) -> dict[str, object]:
    """Click the prompt's [종료] (left) button in this window only, with sent messages.

    WM_MOUSEMOVE first puts the game's cursor there, then the button pair; each is sent
    synchronously with a bounded wait (SendMessageTimeout) and its acknowledgement kept.
    A posted button pair alone was ignored on 2026-09-30 (runs/close-smoke-1).
    """
    rect = wintypes.RECT()
    _ = cast("int", _client_rect(window, ctypes.byref(rect)))
    x = int(rect.right / 2 - 118 / 1044 * rect.bottom)
    y = int(504 / 1044 * rect.bottom)
    point = (y << 16) | x
    sent: list[dict[str, object]] = []
    for name, message, keys in (
        ("move", _WM_MOUSEMOVE, 0),
        ("down", _WM_LBUTTONDOWN, _MK_LBUTTON),
        ("up", _WM_LBUTTONUP, 0),
    ):
        result = ctypes.c_size_t()
        ok = bool(
            cast(
                "int",
                _send_timeout(
                    window, message, keys, point, _SMTO_ABORTIFHUNG, _SEND_MS, ctypes.byref(result)
                ),
            )
        )
        error = 0 if ok else ctypes.get_last_error()
        sent.append({"message": name, "ok": ok, "result": result.value, "error": error})
    return {"hwnd": window, "x": x, "y": y, "sent": sent}


def understands_exit(training_map: Path) -> bool:
    """Whether the installed map's bridge answers control action -2 (the diagnostic build)."""
    script = training_map.parent / "war3map.j"
    return script.is_file() and '"ORD_AI|1|CTL|"' in script.read_text("utf-8")


def game_alive(pid: int) -> bool:
    """Whether this Warcraft III process is still running."""
    return pid in _pids(GAME)


def snapshot_game_windows(pid: int, folder: Path, prefix: str) -> list[dict[str, object]]:
    """Save each visible window of this Warcraft III process as ``<prefix>-<hwnd>.bmp``."""
    taken: list[dict[str, object]] = []
    for window in _windows({pid}):
        path = folder / f"{prefix}-{window['hwnd']}.bmp"
        ok = _snapshot_window(cast("int", window["hwnd"]), path)
        taken.append(
            {"hwnd": window["hwnd"], "title": window["title"], "file": path.name, "ok": ok}
        )
    return taken


def _answer_prompts(pids: set[int]) -> list[dict[str, object]]:
    """Click [종료] in each window of these processes that shows the recognised quit prompt.

    The prompt area is read again PROMPT_SECONDS after the clicks (or once the game ended)
    so the audit shows whether the click was taken.
    """
    prompts: list[dict[str, object]] = []
    for window in _windows(pids):
        handle = cast("int", window["hwnd"])
        distance = _prompt_state(handle)
        prompt = distance is not None and distance <= PROMPT_MATCH
        record: dict[str, object] = {"hwnd": handle, "distance": distance, "prompt": prompt}
        if prompt:
            record["click"] = _click_quit(handle)
        prompts.append(record)

    def gone() -> bool:
        return not pids & set(_pids(GAME))

    if any(item["prompt"] for item in prompts) and not _wait(gone, PROMPT_SECONDS):
        for record in prompts:
            if record["prompt"]:
                record["distance_after_click"] = _prompt_state(cast("int", record["hwnd"]))
    return prompts


def _close_game(pids: set[int], snapshots: Path | None = None) -> tuple[str, dict[str, object]]:
    """Close these Warcraft III processes: ask their windows, terminate only on timeout.

    Returns the method, ``graceful`` (they exited after WM_CLOSE), ``forced`` (they were
    terminated, so War3Log.txt may miss lines not written yet) or ``none`` (none of them
    ran), and an audit: the windows asked (handle, pid, posted, Win32 error), the quit
    prompt found in each window after PROMPT_SECONDS and the [종료] click sent, the visible
    windows before and, when the wait ran out, right before termination; with ``snapshots``
    each of those last windows is saved there as a BMP first (a confirmation prompt drawn
    inside the game shows only there).
    """
    alive = pids & set(_pids(GAME))
    audit: dict[str, object] = {"alive": sorted(alive)}
    if not alive:
        return "none", audit

    def gone() -> bool:
        return not alive & set(_pids(GAME))

    audit["windows_before"] = _windows(alive)
    asked = _request_close(alive)
    audit["asked"] = asked
    audit["closed_by"] = None
    if any(item["posted"] for item in asked):
        if _wait(gone, PROMPT_SECONDS):
            audit["closed_by"] = "wm_close"
            return "graceful", audit
        prompts = _answer_prompts(alive & set(_pids(GAME)))
        audit["prompts"] = prompts
        if any(item["prompt"] for item in prompts) and _wait(gone, GRACEFUL_CLOSE_SECONDS):
            audit["closed_by"] = "prompt_quit"
            return "graceful", audit
    remaining = _windows(alive & set(_pids(GAME)))
    audit["windows_at_fallback"] = remaining
    if snapshots is not None:
        snapshots.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        taken: list[dict[str, object]] = []
        for window in remaining:
            path = snapshots / f"close-fallback-{stamp}-{window['hwnd']}.bmp"
            ok = _snapshot_window(cast("int", window["hwnd"]), path)
            taken.append({"hwnd": window["hwnd"], "file": path.name, "ok": ok})
        audit["fallback_snapshots"] = taken
    _kill_pids(alive)
    if not _wait(gone, 30):
        raise LaunchError(detail="Warcraft III did not close")
    return "forced", audit


def game_running() -> bool:
    """Whether any Warcraft III process is running."""
    return _count(GAME) > 0


def _run_launcher(argument: str | None) -> None:
    # Show-without-activate: starting Battle.net must not take the user's focus.
    result = cast(
        "int | None",
        _execute(None, "open", str(BATTLE_NET), argument, None, _SHOW_NO_ACTIVATE),
    )
    if result is None or result <= _SHELL_EXECUTE_ERROR_LIMIT:
        raise LaunchError(detail=f"Battle.net could not be started (ShellExecute {result})")


def battle_net_config() -> Path:
    """Locate the per-user Battle.net settings file."""
    return Path(os.environ["APPDATA"]) / "Battle.net" / "Battle.net.config"


def _section(parent: dict[str, object], key: str) -> dict[str, object]:
    child = parent.setdefault(key, {})
    if not isinstance(child, dict):
        raise LaunchError(detail=f"Battle.net.config field {key} is not an object")
    return cast("dict[str, object]", child)


def set_launch_arguments(config: Path, arguments: str | None) -> str | None:
    """Replace Warcraft III's extra launcher arguments; return the previous value."""
    document = cast("dict[str, object]", json.loads(config.read_text("utf-8-sig")))
    game = _section(_section(document, "Games"), "w3")
    previous = game.get(ARGUMENTS_KEY)
    if arguments is None:
        _ = game.pop(ARGUMENTS_KEY, None)
    else:
        game[ARGUMENTS_KEY] = arguments
    _ = config.write_text(
        json.dumps(document, indent=4, ensure_ascii=False), encoding="utf-8", newline="\r\n"
    )
    return previous if isinstance(previous, str) else None


def _wait(done: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not done():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
    return True


def _shutdown_launcher() -> None:
    if _count(LAUNCHER) == 0:
        return
    _run_launcher("--exec=shutdown")
    if not _wait(lambda: _count(LAUNCHER) == 0, 30):
        raise LaunchError(detail="Battle.net did not close; settings unchanged")


class GameHost:
    """Hold Battle.net's Warcraft III arguments for one session and restart the game on demand.

    While the session is open, starting Warcraft III from the Battle.net app also opens the
    training map. Leaving the session restores the previous arguments.
    """

    def __init__(
        self,
        training_map: Path,
        config: Path | None = None,
        *,
        close_game: bool = False,
        war3log: tuple[Path, Path] | None = None,
    ) -> None:
        """Prepare the launch arguments; nothing is changed before entering.

        With ``close_game`` the Warcraft III this session started is closed on leaving.
        With ``war3log`` = (War3Log.txt, folder), every close of the session's game copies
        the log into the folder once the game has exited (the next launch replaces the
        file) and appends how it closed to ``closes.jsonl`` there. Only processes this
        session started are ever closed.
        """
        self.config: Path = battle_net_config() if config is None else config
        self.close_game: bool = close_game
        self.war3log: tuple[Path, Path] | None = war3log
        self.owned: set[int] = set()
        self.launched: bool = False
        self.arguments: str = f'{LAUNCH_PREFIX}"{training_map.resolve()}"'
        self.previous: str | None = None
        self.had_launcher: bool = False

    def __enter__(self) -> Self:
        """Apply the arguments; Battle.net reads them only when it starts."""
        self.had_launcher = _count(LAUNCHER) > 0
        _shutdown_launcher()
        previous = set_launch_arguments(self.config, self.arguments)
        # A killed earlier session (of any map) leaves its own arguments behind; never
        # restore those.
        ours = previous is not None and previous.startswith(LAUNCH_PREFIX)
        self.previous = None if ours else previous
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Restore the user's Battle.net settings; the game stays open unless ``close_game``."""
        try:
            if self.close_game and self.launched:
                self._close()
        finally:
            # Battle.net caches its settings and rewrites them on exit: close it first.
            _shutdown_launcher()
            _ = set_launch_arguments(self.config, self.previous)
            if self.had_launcher:
                _run_launcher(None)

    def close(self, exit_request: Callable[[], dict[str, object]] | None = None) -> None:
        """Close the game this session started now; ``exit_request`` first asks its bridge.

        ``exit_request`` (StateChannel.request_exit of the channel bound to this game) is
        used only while every running Warcraft III is one this session started.
        """
        self._close(exit_request)

    def _end_game(
        self, exit_request: Callable[[], dict[str, object]], folder: Path | None
    ) -> dict[str, object]:
        """Ask the bound bridge to end the game, with screens before and after (in folder)."""
        others = set(_pids(GAME)) - self.owned
        if others:
            return {
                "sent": False,
                "reason": f"Warcraft III not started by this session: {sorted(others)}",
            }
        alive = self.owned & set(_pids(GAME))
        if not alive:
            return {"sent": False, "reason": "no running game of this session"}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        shots: dict[str, object] = {}
        if folder is not None:
            folder.mkdir(parents=True, exist_ok=True)
            shots["before"] = [
                snapshot_game_windows(pid, folder, f"control-before-{stamp}")
                for pid in sorted(alive)
            ]
        control = exit_request()
        if control.get("ack"):

            def gone() -> bool:
                return not alive & set(_pids(GAME))

            control["exited_after_ack"] = _wait(gone, PROMPT_SECONDS)
            if folder is not None and not control["exited_after_ack"]:
                shots["after"] = [
                    snapshot_game_windows(pid, folder, f"control-after-{stamp}")
                    for pid in sorted(alive)
                ]
        control["snapshots"] = shots
        return control

    def _close(self, exit_request: Callable[[], dict[str, object]] | None = None) -> None:
        pids = sorted(self.owned)
        folder = None if self.war3log is None else self.war3log[1]
        control = None if exit_request is None else self._end_game(exit_request, folder)
        method, audit = _close_game(self.owned, folder)
        if control is not None:
            audit["control"] = control
            if method == "graceful" and control.get("ack"):
                audit["closed_by"] = f"bridge_endgame+{audit['closed_by']}"
            elif method == "none" and control.get("exited_after_ack"):
                method, audit["closed_by"] = "graceful", "bridge_endgame"
        self.owned = set()
        if method == "none" or self.war3log is None:
            return
        source, folder = self.war3log
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        copy = folder / f"War3Log-closed-{stamp}.txt"
        size = source.stat().st_size if source.exists() else None
        if size is not None:
            _ = shutil.copyfile(source, copy)
        record: dict[str, object] = {"closed": stamp, "method": method, "pids": pids}
        record["bytes"] = size
        record["copy"] = copy.name if size is not None else None
        record["audit"] = audit
        with (folder / "closes.jsonl").open("a", encoding="utf-8") as stream:
            _ = stream.write(json.dumps(record) + "\n")

    def relaunch(self) -> None:
        """Close this session's Warcraft III and start a fresh one with the training map.

        A Warcraft III this session did not start is left alone: the launch stops instead.
        """
        self._close()
        before = set(_pids(GAME))
        if before:
            raise LaunchError(detail=f"Warcraft III not started by this session runs: {before}")

        def started() -> bool:
            return bool(set(_pids(GAME)) - before)

        # The first requests only start the client; repeat until it launches the game.
        for _ in range(36):
            _run_launcher('"--exec=launch W3"')
            if _wait(started, 5):
                self.owned = set(_pids(GAME)) - before
                self.launched = True
                return
        raise LaunchError(detail="Warcraft III did not start; check the Battle.net login")

    def press_any_key(self) -> None:
        """Try to pass the loading screen without taking focus."""
        press_loading_screen_key()


def launch(training_map: Path, config: Path | None = None) -> None:
    """Open the map through the logged-in Battle.net client, then restore its settings."""
    with GameHost(training_map, config) as host:
        host.relaunch()
