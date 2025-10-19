#!/usr/bin/env python3

import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Tuple


class RegistrationHandler(BaseHTTPRequestHandler):
    approval_threshold: int = 3
    poll_after_seconds: int = 5
    approved: bool = False
    register_calls: int = 0

    def _set_headers(self, status_code: int = 200) -> None:
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/mobile/devices/register":
            self._set_headers(404)
            self.wfile.write(b'{"error":"not_found"}')
            return

        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length:
            # Read and discard the request body to keep the socket clean.
            self.rfile.read(content_length)

        RegistrationHandler.register_calls += 1
        if not RegistrationHandler.approved:
            RegistrationHandler.approved = (
                RegistrationHandler.approval_threshold <= 0
                or RegistrationHandler.register_calls >= RegistrationHandler.approval_threshold
            )

        status = "APPROVED" if RegistrationHandler.approved else "PENDING"
        message = (
            "Device approved"
            if RegistrationHandler.approved
            else "Awaiting administrator approval"
        )

        payload = {
            "status": status,
            "message": message,
        }
        if not RegistrationHandler.approved and RegistrationHandler.poll_after_seconds > 0:
            payload["pollAfterSeconds"] = RegistrationHandler.poll_after_seconds

        response = json.dumps(payload).encode("utf-8")
        self._set_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: Tuple[object, ...]) -> None:  # noqa: A003
        # Suppress default stdout logging; callers should watch the wrapper script instead.
        return


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Lightweight mock backend for Ringdown registration polling."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface to bind (default: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8899,
        help="Port to bind the mock server (default: 8899).",
    )
    parser.add_argument(
        "--approval-threshold",
        type=int,
        default=3,
        help="Number of register polls before approval (default: 3). Use 0 to approve immediately.",
    )
    parser.add_argument(
        "--poll-after-seconds",
        type=int,
        default=5,
        help="Seconds to communicate to the client for the next poll when pending (default: 5).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    RegistrationHandler.approval_threshold = args.approval_threshold
    RegistrationHandler.poll_after_seconds = args.poll_after_seconds
    RegistrationHandler.approved = False
    RegistrationHandler.register_calls = 0

    server = HTTPServer((args.host, args.port), RegistrationHandler)

    print(
        json.dumps(
            {
                "event": "mock_server_started",
                "host": args.host,
                "port": args.port,
                "approval_threshold": args.approval_threshold,
                "poll_after_seconds": args.poll_after_seconds,
            }
        ),
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
