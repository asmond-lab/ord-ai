"""Loopback HTTP server for the dashboard page, its assets and the summary document."""

import re
import socket
from collections.abc import Callable
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import BaseServer
from typing import Final, override
from urllib.parse import urlsplit

HOST: Final = "127.0.0.1"
WEB_ROOT: Final = Path(__file__).parent
SUMMARY_ROUTE: Final = "/api/summary"
# One flat file name with a page extension: no directories, no dot segments, no Python or docs.
ASSET: Final = re.compile(r"/[a-z0-9-]+\.(?:html|css|js|svg)")
CONTENT_TYPES: Final = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".json": "application/json",
}
LOCAL_HOSTS: Final = frozenset({"127.0.0.1", "localhost"})
HEADERS: Final = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    (
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    ),
)

type Summary = Callable[[], bytes]


class DashboardHandler(SimpleHTTPRequestHandler):
    """Answer GET for ``/``, the page's own assets and the summary; any other path is a 404.

    It extends SimpleHTTPRequestHandler only for its ``do_GET``/``do_HEAD`` hooks. Routes are an
    explicit allow-list, so no other file of the package is ever served.
    """

    def __init__(
        self,
        request: socket.socket,
        client_address: tuple[str, int],
        server: BaseServer,
        *,
        summary: Summary,
    ) -> None:
        """Keep the summary source; the base class handles the request inside ``__init__``."""
        self.summary: Final = summary
        super().__init__(request, client_address, server)

    @override
    def do_GET(self) -> None:
        if urlsplit(f"//{self.headers.get('Host', '')}").hostname not in LOCAL_HOSTS:
            # A foreign Host header means DNS rebinding: another web page reading this server.
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        route = urlsplit(self.path).path
        if route == SUMMARY_ROUTE:
            self._reply(self.summary(), CONTENT_TYPES[".json"])
            return
        if route == "/":
            route = "/index.html"
        file = WEB_ROOT / route.removeprefix("/")
        if ASSET.fullmatch(route) and file.is_file():
            self._reply(file.read_bytes(), CONTENT_TYPES[file.suffix])
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    @override
    def do_HEAD(self) -> None:
        self.send_error(HTTPStatus.NOT_IMPLEMENTED, "Only GET is supported")

    @override
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        """Log failed requests only: the page polls the summary every 5 seconds."""
        if isinstance(code, int) and code >= HTTPStatus.BAD_REQUEST:
            super().log_request(code, size)

    def _reply(self, body: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in HEADERS:
            self.send_header(name, value)
        self.end_headers()
        _ = self.wfile.write(body)


class _LoopbackServer(ThreadingHTTPServer):
    """A threading HTTP server that fails when its port is already taken."""

    # On Windows SO_REUSEADDR lets a second process bind a port in use without an error.
    allow_reuse_address: bool = False


def bind(port: int, summary: Summary) -> ThreadingHTTPServer:
    """Listen on 127.0.0.1 only, since the dashboard has no authentication; port 0 picks one."""
    return _LoopbackServer((HOST, port), partial(DashboardHandler, summary=summary))
