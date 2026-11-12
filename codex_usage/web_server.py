from __future__ import annotations

import argparse
import json
import logging
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from codex_usage import DEFAULT_DAYS, UsageError, read_account_usage, utc_today
from usage_store import (
    SnapshotError,
    compare_sources,
    load_snapshot,
    normalize_detailed_usage,
    save_snapshot,
)


HOST = "127.0.0.1"
DEFAULT_PORT = 12300
ASSET_DIR = Path(__file__).with_name("web")
STATIC_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/calculations.js": ("calculations.js", "text/javascript; charset=utf-8"),
}

logger = logging.getLogger(__name__)


def _combined_payload(
    app_snapshot: dict[str, object],
    detailed_snapshot: dict[str, object] | None,
) -> dict[str, object]:
    app_usage = app_snapshot.get("payload")
    if not isinstance(app_usage, dict):
        raise SnapshotError("saved app-server payload is invalid")
    detailed_usage: dict[str, object] | None = None
    comparison: dict[str, object] | None = None
    if detailed_snapshot is not None:
        detailed_usage = normalize_detailed_usage(detailed_snapshot.get("payload"))
        comparison = compare_sources(app_usage, detailed_usage)

    today = utc_today()
    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "accountSummary": app_usage["summary"],
        "defaultWindow": {
            "startDate": (today - timedelta(days=DEFAULT_DAYS)).isoformat(),
            "endDateInclusive": (today - timedelta(days=1)).isoformat(),
        },
        "dailyUsageBuckets": app_usage["dailyUsageBuckets"],
        "sources": {
            "appServer": {
                "capturedAt": app_snapshot["capturedAt"],
                "accountSummary": app_usage["summary"],
                "dailyUsageBuckets": app_usage["dailyUsageBuckets"],
            },
            "detailed": (
                None
                if detailed_snapshot is None
                else {
                    "capturedAt": detailed_snapshot["capturedAt"],
                    **(detailed_usage or {}),
                }
            ),
        },
        "comparison": comparison,
    }


def build_usage_payload() -> dict[str, object]:
    usage = read_account_usage()
    app_snapshot = save_snapshot("app_server", usage)
    detailed_snapshot = load_snapshot("detailed")
    return _combined_payload(app_snapshot, detailed_snapshot)


def import_detailed_payload(payload: object) -> dict[str, object]:
    normalize_detailed_usage(payload)
    detailed_snapshot = save_snapshot("detailed", payload)
    app_snapshot = load_snapshot("app_server")
    if app_snapshot is None:
        usage = read_account_usage()
        app_snapshot = save_snapshot("app_server", usage)
    return _combined_payload(app_snapshot, detailed_snapshot)


class UsageHandler(BaseHTTPRequestHandler):
    server_version = "CodexUsage/0.3"

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/api/usage":
            self._serve_usage()
            return
        asset = STATIC_ASSETS.get(path)
        if asset is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        filename, content_type = asset
        self._serve_asset(filename, content_type)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path != "/api/detailed-usage":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self._import_detailed_usage()

    def _serve_usage(self) -> None:
        try:
            payload = build_usage_payload()
        except (SnapshotError, UsageError, ValueError) as exc:
            logger.error("%s", exc)
            self._send_json(
                {"error": str(exc)},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        self._send_json(payload)

    def _import_detailed_usage(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(
                {"error": "invalid Content-Length"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        if not 1 <= content_length <= 10_000_000:
            self._send_json(
                {"error": "JSON body must be between 1 byte and 10 MB"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        try:
            payload = json.loads(self.rfile.read(content_length))
            result = import_detailed_payload(payload)
        except json.JSONDecodeError as exc:
            self._send_json(
                {"error": f"invalid JSON: {exc.msg}"},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        except (SnapshotError, UsageError, ValueError) as exc:
            logger.error("%s", exc)
            self._send_json(
                {"error": str(exc)},
                status=HTTPStatus.BAD_REQUEST,
            )
            return
        self._send_json(result, status=HTTPStatus.CREATED)

    def _serve_asset(self, filename: str, content_type: str) -> None:
        try:
            content = (ASSET_DIR / filename).read_bytes()
        except OSError as exc:
            logger.error("could not read web asset %s: %s", filename, exc)
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _send_json(
        self,
        payload: dict[str, object],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug(format, *args)


def create_server(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), UsageHandler)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the local Codex usage dashboard.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="do not open the dashboard in the default browser",
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _build_parser().parse_args()
    if not 1 <= args.port <= 65_535:
        logger.error("port must be between 1 and 65535")
        return 2

    try:
        server = create_server(args.port)
    except OSError as exc:
        logger.error("could not start dashboard: %s", exc)
        return 1

    url = f"http://{HOST}:{server.server_address[1]}/"
    logger.info("Codex usage dashboard: %s", url)
    if not args.no_open:
        threading.Timer(0.3, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("stopping dashboard")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
