"""Client for the ORD simulator server, fed with this repository's bridge and catalog.

The simulator (D:/OrandOverlay/local-tools, owned by the map-parity work) runs the original
war3map.j plus the bridge JASS in its own VM and answers the same state.txt snapshots as the
real game. Its own Python client imports a vendored copy of ``ord_ai``; this module speaks the
same JSON-lines protocol but parses with the live ``ord_ai`` so the policy vocabulary always
matches ``build/catalog.json``. The simulator folder is only read: each curriculum stage gets a
separate server root whose bridge files come from :func:`ord_ai.build.bridge_sources`.
"""

import _winapi
import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from types import TracebackType
from typing import Final, Self, TextIO, cast

from ord_ai.build import bridge_sources
from ord_ai.curriculum import Stage
from ord_ai.protocol import GameState, ProtocolError, Status, parse_snapshot

SIMULATOR: Final = Path("D:/OrandOverlay/local-tools")
_SERVER_BIN: Final = Path("OrandSimulator.Server/bin/Release/net10.0")
_BASE_DATA: Final = Path("OrandSimulator.Runtime/BaseData")
_BRIDGE: Final = Path("OrandSimulator.Runtime/BridgeSource/ord-picker-2323")
_MAPS: Final = Path("maps")
_BRIDGE_FILES: Final = ("bridge_globals.j", "bridge_actions.j", "bridge_functions.j")
_LINE_LIMIT: Final = 1 << 26
WINS: Final = "wins.jsonl"
_CLOSE_SECONDS: Final = 10.0


class SimServerError(RuntimeError):
    """The simulator process or protocol rejected a request."""

    def __init__(self, detail: str) -> None:
        """Keep the server's explanation."""
        self.detail: str = detail
        super().__init__(detail)


def _junction(link: Path, target: Path) -> None:
    if link.exists():
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    _winapi.CreateJunction(str(target), str(link))


def _copy_changed(source: Path, destination: Path) -> None:
    # A running server holds its DLLs open, so a file is replaced only when it differs.
    for path in source.rglob("*"):
        target = destination / path.relative_to(source)
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        wanted = path.stat()
        try:
            current = target.stat()
        except FileNotFoundError:
            current = None
        if current is None or (current.st_size, current.st_mtime_ns) != (
            wanted.st_size,
            wanted.st_mtime_ns,
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copy2(path, target)


def _write_changed(path: Path, text: str) -> None:
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        _ = path.write_text(text, encoding="utf-8")


def server_build(simulator: Path = SIMULATOR) -> str:
    """A short stamp of the simulator server build (file names, sizes, modification times).

    A running server locks its DLLs, so a rebuilt simulator cannot be copied into a root that
    another run's server still uses; roots are therefore kept per build.
    """
    digest = hashlib.sha256()
    binaries = simulator / _SERVER_BIN
    for path in sorted(binaries.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(
                f"{path.relative_to(binaries)}|{stat.st_size}|{stat.st_mtime_ns}\n".encode()
            )
    return digest.hexdigest()[:8]


def prepare_root(stage: Stage, destination: Path, simulator: Path = SIMULATOR) -> Path:
    """Lay out a server root for one stage and return its server DLL.

    The server finds map data and bridge files relative to its own folder, so the binaries
    are copied and the large read-only data is linked, never modified. Only files that differ
    are rewritten, so a root can be prepared again while another process's server runs in it.
    """
    server = destination / _SERVER_BIN
    _copy_changed(simulator / _SERVER_BIN, server)
    _junction(destination / _BASE_DATA, simulator / _BASE_DATA)
    _junction(destination / _MAPS, simulator / _MAPS)
    bridge = destination / _BRIDGE
    bridge.mkdir(parents=True, exist_ok=True)
    for name, text in zip(_BRIDGE_FILES, bridge_sources(stage), strict=True):
        _write_changed(bridge / name, text)
    return server / "OrandSimulator.Server.dll"


class SimServer:
    """Own one simulator process and exchange one JSON line per request."""

    def __init__(self, dll: Path) -> None:
        """Remember the server DLL; the process starts on enter."""
        self.dll: Path = dll
        self._loop: asyncio.AbstractEventLoop | None = None
        self._process: asyncio.subprocess.Process | None = None

    def __enter__(self) -> Self:
        """Start ``dotnet <dll>``; the server's stderr goes to this console."""
        program = shutil.which("dotnet")
        if program is None:
            raise SimServerError(detail="dotnet is not on PATH")
        self._loop = asyncio.new_event_loop()
        self._process = self._loop.run_until_complete(
            asyncio.create_subprocess_exec(
                program,
                str(self.dll),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                limit=_LINE_LIMIT,
            )
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Ask the server to close, killing it if it does not."""
        process, loop = self._process, self._loop
        self._process, self._loop = None, None
        if loop is None:
            return
        if process is not None and process.returncode is None:
            try:
                _ = loop.run_until_complete(self._exchange(process, {"op": "close"}))
                _ = loop.run_until_complete(asyncio.wait_for(process.wait(), _CLOSE_SECONDS))
            except (SimServerError, OSError, TimeoutError):
                process.kill()
                _ = loop.run_until_complete(process.wait())
        loop.close()

    def request(self, request: dict[str, object]) -> dict[str, object]:
        """Send one JSON request and return its successful response."""
        if self._process is None or self._loop is None:
            raise SimServerError(detail="server is not running")
        return self._loop.run_until_complete(self._exchange(self._process, request))

    @staticmethod
    async def _exchange(
        process: asyncio.subprocess.Process, request: dict[str, object]
    ) -> dict[str, object]:
        if process.stdin is None or process.stdout is None:
            raise SimServerError(detail="server pipes are closed")
        process.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode())
        await process.stdin.drain()
        line = await process.stdout.readline()
        if not line:
            raise SimServerError(detail=f"server exited with code {process.returncode}")
        response = cast("object", json.loads(line))
        if not isinstance(response, dict):
            raise SimServerError(detail="server response is not a JSON object")
        typed = cast("dict[str, object]", response)
        if typed.get("ok") is not True:
            raise SimServerError(detail=str(typed.get("error", "unknown server error")))
        return typed


def _state(result: dict[str, object]) -> GameState:
    text = result.get("state_text")
    if not isinstance(text, str):
        raise SimServerError(detail="response has no state_text")
    state = parse_snapshot(text)
    if state is None:
        raise ProtocolError(detail="simulator returned an incomplete snapshot")
    return state


def _results(response: dict[str, object]) -> list[dict[str, object]]:
    results = response.get("results")
    if not isinstance(results, list):
        raise SimServerError(detail="batch response has no results")
    return [cast("dict[str, object]", item) for item in cast("list[object]", results)]


def _file_sha256(path: Path) -> str | None:
    """The sha256 of ``path``, or None when there is no such file (a test's fake root)."""
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class WinTrace:
    """Append one ``wins.jsonl`` line per simulator WIN, enough to replay that game.

    A line holds the game's reset seed, every action wire code sent in it, the final STATE
    text and the identity of the simulator that played it. It only records what the caller
    already did: seeds, actions and states are unchanged.
    """

    def __init__(
        self, stream: TextIO, server: SimServer, dll: Path, context: dict[str, object]
    ) -> None:
        """Write to ``stream``; ``context`` (phase, stage) starts every line."""
        self.stream: TextIO = stream
        self.server: SimServer = server
        self.dll: Path = dll
        self.context: dict[str, object] = context
        self._identity: dict[str, object] | None = None

    def identity(self) -> dict[str, object]:
        """The server's map, catalog and combined-script hashes plus its DLL hashes (asked once)."""
        if self._identity is None:
            hello = self.server.request({"op": "hello"})
            runtime = self.dll.with_name("OrandSimulator.Runtime.dll")
            self._identity = {
                "map_sha256": hello.get("map_sha256"),
                "catalog_fingerprint": hello.get("catalog_fingerprint"),
                "bridge_sha256": hello.get("bridge_sha256"),
                "server_dll_sha256": _file_sha256(self.dll),
                "runtime_dll_sha256": _file_sha256(runtime),
            }
        return self._identity

    def write(self, env: int, seed: int, codes: list[int], state_text: str) -> None:
        """Record one won game of environment ``env``."""
        record = {
            **self.context,
            **self.identity(),
            "env": env,
            "seed": seed,
            "length": len(codes),
            "codes": codes,
            "final_state": state_text,
        }
        _ = self.stream.write(json.dumps(record, separators=(",", ":")) + "\n")
        self.stream.flush()


class SimVectorEnv:
    """Independent simulator environments stepped in parallel by one server."""

    def __init__(
        self, server: SimServer, count: int, base_seed: int, wins: WinTrace | None = None
    ) -> None:
        """Give environment ``i`` the seeds ``base_seed + i``, then ``+ count`` per reset.

        With ``wins``, every game that ends in a WIN is archived with its seed and actions.
        """
        if count <= 0:
            raise SimServerError(detail="count must be positive")
        self.server: SimServer = server
        self.count: int = count
        self.wins: WinTrace | None = wins
        self._seeds: list[int] = [base_seed + index for index in range(count)]
        self._game_seeds: list[int] = [0] * count
        self._codes: list[list[int]] = [[] for _ in range(count)]

    def _seed(self, index: int) -> int:
        seed = self._seeds[index]
        self._seeds[index] += self.count
        self._game_seeds[index] = seed
        self._codes[index] = []
        return seed

    def reset_all(self) -> list[GameState]:
        """Start a fresh episode in every environment."""
        resets = [{"env": index, "seed": self._seed(index)} for index in range(self.count)]
        response = self.server.request({"op": "reset_batch", "resets": resets})
        return [_state(result) for result in _results(response)]

    def reset(self, index: int) -> GameState:
        """Start a fresh episode in one environment."""
        return _state(self.server.request({"op": "reset", "env": index, "seed": self._seed(index)}))

    def step(self, codes: list[int]) -> list[GameState]:
        """Send one action wire code to every environment."""
        if len(codes) != self.count:
            raise SimServerError(detail="one action code per environment is required")
        steps = [{"env": index, "action": code} for index, code in enumerate(codes)]
        response = self.server.request({"op": "step_batch", "steps": steps})
        results = _results(response)
        states = [_state(result) for result in results]
        for index, (code, result, state) in enumerate(zip(codes, results, states, strict=True)):
            self._codes[index].append(code)
            if self.wins is not None and state.status is Status.WIN:
                text = cast("str", result["state_text"])
                self.wins.write(index, self._game_seeds[index], list(self._codes[index]), text)
        return states
