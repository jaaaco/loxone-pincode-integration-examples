import base64
import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator
import http.server
import socketserver
import urllib.parse

import pytest

EXPECTED_AUTH = "Basic " + base64.b64encode(b"testuser:testpass").decode("ascii")


class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True


@dataclass
class FakeServer:
    base_url: str
    target_base: str
    server: ThreadedTCPServer
    thread: threading.Thread
    state: dict

    def close(self) -> None:
        self.server.shutdown()
        self.thread.join()


@contextmanager
def fake_loxone_server() -> Iterator[FakeServer]:
    state = {"pin": None}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _check_auth(self) -> bool:
            auth_header = self.headers.get("Authorization")
            if auth_header != EXPECTED_AUTH:
                self.send_response(401)
                self.send_header("WWW-Authenticate", "Basic realm=\"Test\"")
                self.end_headers()
                return False
            return True

        def _send_json(self, payload: str) -> None:
            body = payload.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            if self.path == "/cloud":
                location = f"{self.server.target_base}/"
                self.send_response(307)
                self.send_header("Location", location)
                self.end_headers()
                return

            if not self._check_auth():
                return

            parsed = urllib.parse.urlparse(self.path)
            segments = parsed.path.strip("/").split("/")

            if segments[:3] != ["miniserver", "jdev", "sps"]:
                self.send_error(404)
                return

            action = segments[3] if len(segments) > 3 else ""

            if action == "getuserlist2":
                value = json.dumps(
                    [
                        {
                            "name": "guest",
                            "uuid": "uuid-123",
                            "isAdmin": False,
                            "userState": 0,
                            "representsControl": False,
                        }
                    ]
                )
                body = json.dumps({"LL": {"value": value, "code": "200"}})
                self._send_json(body)
                return

            if action == "getuser" and len(segments) == 5:
                uuid = segments[4]
                if uuid != "uuid-123":
                    self.send_error(404)
                    return
                if state["pin"] is None:
                    keycodes = []
                else:
                    keycodes = [{"code": f"HASH-{state['pin']}"}]
                user_value = json.dumps(
                    {
                        "name": "guest",
                        "uuid": uuid,
                        "keycodes": keycodes,
                        "userState": 0,
                        "isAdmin": False,
                    }
                )
                body = json.dumps({"LL": {"value": user_value, "code": "200"}})
                self._send_json(body)
                return

            if action == "updateuseraccesscode" and len(segments) >= 5:
                uuid = segments[4]
                if uuid != "uuid-123":
                    self.send_error(404)
                    return
                pin = segments[5] if len(segments) > 5 else ""
                state["pin"] = pin or None
                body = json.dumps({"LL": {"value": True, "code": 200}})
                self._send_json(body)
                return

            self.send_error(404)

        def log_message(self, format: str, *args) -> None:  # noqa: A003 - required signature
            return

    with ThreadedTCPServer(("127.0.0.1", 0), Handler) as server:
        host, port = server.server_address
        target_base = f"http://{host}:{port}/miniserver"
        server.target_base = target_base  # type: ignore[attr-defined]

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        fake_server = FakeServer(
            base_url=f"http://{host}:{port}/cloud",
            target_base=target_base,
            server=server,
            thread=thread,
            state=state,
        )
        try:
            yield fake_server
        finally:
            fake_server.close()


def run_command(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )


def test_bash_workflow() -> None:
    with fake_loxone_server() as server:
        env = os.environ.copy()
        env.update({
            "SERVER_BASE": server.base_url,
            "AUTH": "testuser:testpass",
        })

        resolve = run_command(["bash", "examples/bash/pin_workflow.sh", "resolve"], env)
        assert resolve.stdout.strip() == server.target_base

        list_users = run_command(["bash", "examples/bash/pin_workflow.sh", "list-users"], env)
        list_payload = json.loads(list_users.stdout)
        users = json.loads(list_payload["LL"]["value"])
        assert users[0]["uuid"] == "uuid-123"

        show_empty = run_command(["bash", "examples/bash/pin_workflow.sh", "show-user", "uuid-123"], env)
        show_empty_payload = json.loads(show_empty.stdout)
        user_data = json.loads(show_empty_payload["LL"]["value"])
        assert user_data["keycodes"] == []

        run_command(["bash", "examples/bash/pin_workflow.sh", "set-pin", "uuid-123", "1234"], env)

        show_pin = run_command(["bash", "examples/bash/pin_workflow.sh", "show-user", "uuid-123"], env)
        show_pin_payload = json.loads(show_pin.stdout)
        user_with_pin = json.loads(show_pin_payload["LL"]["value"])
        assert user_with_pin["keycodes"] == [{"code": "HASH-1234"}]

        run_command(["bash", "examples/bash/pin_workflow.sh", "clear-pin", "uuid-123"], env)

        show_cleared = run_command(["bash", "examples/bash/pin_workflow.sh", "show-user", "uuid-123"], env)
        show_cleared_payload = json.loads(show_cleared.stdout)
        cleared_user = json.loads(show_cleared_payload["LL"]["value"])
        assert cleared_user["keycodes"] == []


def test_python_workflow() -> None:
    with fake_loxone_server() as server:
        resolve = run_command(
            [
                sys.executable,
                "examples/python/manage_pin.py",
                "resolve",
                "--server-base",
                server.base_url,
                "--auth",
                "testuser:testpass",
            ],
            os.environ.copy(),
        )
        assert resolve.stdout.strip() == server.target_base

        list_users = run_command(
            [
                sys.executable,
                "examples/python/manage_pin.py",
                "list",
                "--server-base",
                server.base_url,
                "--auth",
                "testuser:testpass",
            ],
            os.environ.copy(),
        )
        list_payload = json.loads(list_users.stdout)
        users = json.loads(list_payload["LL"]["value"])
        assert users[0]["uuid"] == "uuid-123"

        show_empty = run_command(
            [
                sys.executable,
                "examples/python/manage_pin.py",
                "show",
                "uuid-123",
                "--server-base",
                server.base_url,
                "--auth",
                "testuser:testpass",
            ],
            os.environ.copy(),
        )
        empty_payload = json.loads(show_empty.stdout)
        user_data = json.loads(empty_payload["LL"]["value"])
        assert user_data["keycodes"] == []

        run_command(
            [
                sys.executable,
                "examples/python/manage_pin.py",
                "set",
                "uuid-123",
                "1234",
                "--server-base",
                server.base_url,
                "--auth",
                "testuser:testpass",
            ],
            os.environ.copy(),
        )

        show_pin = run_command(
            [
                sys.executable,
                "examples/python/manage_pin.py",
                "show",
                "uuid-123",
                "--server-base",
                server.base_url,
                "--auth",
                "testuser:testpass",
            ],
            os.environ.copy(),
        )
        show_pin_payload = json.loads(show_pin.stdout)
        user_with_pin = json.loads(show_pin_payload["LL"]["value"])
        assert user_with_pin["keycodes"] == [{"code": "HASH-1234"}]

        run_command(
            [
                sys.executable,
                "examples/python/manage_pin.py",
                "clear",
                "uuid-123",
                "--server-base",
                server.base_url,
                "--auth",
                "testuser:testpass",
            ],
            os.environ.copy(),
        )

        show_cleared = run_command(
            [
                sys.executable,
                "examples/python/manage_pin.py",
                "show",
                "uuid-123",
                "--server-base",
                server.base_url,
                "--auth",
                "testuser:testpass",
            ],
            os.environ.copy(),
        )
        cleared_payload = json.loads(show_cleared.stdout)
        cleared_user = json.loads(cleared_payload["LL"]["value"])
        assert cleared_user["keycodes"] == []


def test_node_workflow() -> None:
    if not shutil.which("node"):
        pytest.skip("node executable not available")

    with fake_loxone_server() as server:
        env = os.environ.copy()
        env.update({
            "SERVER_BASE": server.base_url,
            "AUTH": "testuser:testpass",
        })

        resolve = run_command(["node", "examples/node/managePin.js", "resolve"], env)
        assert resolve.stdout.strip() == server.target_base

        list_users = run_command(["node", "examples/node/managePin.js", "list"], env)
        list_payload = json.loads(list_users.stdout)
        users = json.loads(list_payload["LL"]["value"])
        assert users[0]["uuid"] == "uuid-123"

        show_empty = run_command(["node", "examples/node/managePin.js", "show", "uuid-123"], env)
        empty_payload = json.loads(show_empty.stdout)
        user_data = json.loads(empty_payload["LL"]["value"])
        assert user_data["keycodes"] == []

        run_command(["node", "examples/node/managePin.js", "set", "uuid-123", "1234"], env)

        show_pin = run_command(["node", "examples/node/managePin.js", "show", "uuid-123"], env)
        show_pin_payload = json.loads(show_pin.stdout)
        user_with_pin = json.loads(show_pin_payload["LL"]["value"])
        assert user_with_pin["keycodes"] == [{"code": "HASH-1234"}]

        run_command(["node", "examples/node/managePin.js", "clear", "uuid-123"], env)

        show_cleared = run_command(["node", "examples/node/managePin.js", "show", "uuid-123"], env)
        cleared_payload = json.loads(show_cleared.stdout)
        cleared_user = json.loads(cleared_payload["LL"]["value"])
        assert cleared_user["keycodes"] == []
