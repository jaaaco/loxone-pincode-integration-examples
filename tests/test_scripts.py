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

# Must match LOXONE_EPOCH_OFFSET in every example script.
LOXONE_EPOCH_OFFSET = 1230768000

# Seeded access groups (name -> uuid), incl. one system group.
GROUPS = [
    {"name": "ap1", "uuid": "group-ap1"},
    {"name": "ap2", "uuid": "group-ap2"},
    {"name": "(admin)", "uuid": "sysgroup"},
]


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
    # users: uuid -> user dict (insertion order preserved). Seed one static user.
    state = {
        "users": {
            "uuid-123": {
                "name": "guest",
                "uuid": "uuid-123",
                "keycodes": [],
                "userState": 0,
                "isAdmin": False,
            }
        },
        "created_seq": 0,
    }

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

        def _send_ll(self, value) -> None:
            self._send_json(json.dumps({"LL": {"value": value, "code": "200"}}))

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
            users = state["users"]

            if action == "getuserlist2":
                value = json.dumps(
                    [
                        {
                            "name": u["name"],
                            "uuid": u["uuid"],
                            "isAdmin": u["isAdmin"],
                            "userState": u["userState"],
                            "representsControl": False,
                        }
                        for u in users.values()
                    ]
                )
                self._send_ll(value)
                return

            if action == "getgrouplist":
                self._send_ll(json.dumps(GROUPS))
                return

            if action == "getuser" and len(segments) == 5:
                uuid = segments[4]
                if uuid not in users:
                    self.send_error(404)
                    return
                self._send_ll(json.dumps(users[uuid]))
                return

            if action == "updateuseraccesscode" and len(segments) >= 5:
                uuid = segments[4]
                if uuid not in users:
                    self.send_error(404)
                    return
                pin = segments[5] if len(segments) > 5 else ""
                users[uuid]["keycodes"] = [{"code": f"HASH-{pin}"}] if pin else []
                self._send_json(json.dumps({"LL": {"value": True, "code": 200}}))
                return

            if action == "addoredituser" and len(segments) == 5:
                raw = urllib.parse.unquote(segments[4])
                obj = json.loads(raw)
                uuid = obj.get("uuid")
                if not uuid:
                    state["created_seq"] += 1
                    uuid = f"created-user-{state['created_seq']}"
                user = {
                    "name": obj.get("name", ""),
                    "uuid": uuid,
                    "keycodes": obj.get("keycodes", []),
                    "userState": obj.get("userState", 0),
                    "isAdmin": False,
                    "usergroups": obj.get("usergroups", []),
                    "validFrom": obj.get("validFrom"),
                    "validUntil": obj.get("validUntil"),
                    "expirationAction": obj.get("expirationAction"),
                }
                users[uuid] = user
                self._send_ll(json.dumps(user))
                return

            if action == "deleteuser" and len(segments) == 5:
                uuid = segments[4]
                if uuid not in users:
                    self.send_error(404)
                    return
                del users[uuid]
                self._send_json(json.dumps({"LL": {"value": True, "code": 200}}))
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


def ll_value(stdout: str):
    """Parse the double-encoded Loxone LL.value payload."""
    return json.loads(json.loads(stdout)["LL"]["value"])


# --- Bash -------------------------------------------------------------------

def _bash_env(server: FakeServer) -> dict[str, str]:
    env = os.environ.copy()
    env.update({"SERVER_BASE": server.base_url, "AUTH": "testuser:testpass"})
    return env


def _bash(server: FakeServer, *args: str) -> subprocess.CompletedProcess[str]:
    return run_command(["bash", "examples/bash/pin_workflow.sh", *args], _bash_env(server))


def test_bash_model_a() -> None:
    with fake_loxone_server() as server:
        assert _bash(server, "resolve").stdout.strip() == server.target_base

        users = ll_value(_bash(server, "list-users").stdout)
        assert users[0]["uuid"] == "uuid-123"

        assert ll_value(_bash(server, "show-user", "uuid-123").stdout)["keycodes"] == []

        _bash(server, "set-pin", "uuid-123", "1234")
        assert ll_value(_bash(server, "show-user", "uuid-123").stdout)["keycodes"] == [
            {"code": "HASH-1234"}
        ]

        _bash(server, "clear-pin", "uuid-123")
        assert ll_value(_bash(server, "show-user", "uuid-123").stdout)["keycodes"] == []


def test_bash_model_b() -> None:
    with fake_loxone_server() as server:
        groups = ll_value(_bash(server, "list-groups").stdout)
        ap1 = next(g["uuid"] for g in groups if g["name"] == "ap1")
        assert ap1 == "group-ap1"

        from_unix, until_unix = 1735689600, 1735776000  # 2025-01-01, 2025-01-02 UTC
        created = ll_value(
            _bash(
                server,
                "create-guest",
                ap1,
                "rez-1",
                "246813",
                str(from_unix),
                str(until_unix),
            ).stdout
        )
        uuid = created["uuid"]
        assert created["usergroups"] == [ap1]
        assert created["keycodes"] == [{"code": "246813"}]
        assert created["userState"] == 4
        assert created["expirationAction"] == 1
        # Scripts must convert Unix -> Loxone epoch (offset 2009-01-01 UTC).
        assert created["validFrom"] == from_unix - LOXONE_EPOCH_OFFSET
        assert created["validUntil"] == until_unix - LOXONE_EPOCH_OFFSET

        assert ll_value(_bash(server, "show-user", uuid).stdout)["uuid"] == uuid

        _bash(server, "delete-user", uuid)
        remaining = [u["uuid"] for u in ll_value(_bash(server, "list-users").stdout)]
        assert uuid not in remaining  # user is gone after delete


# --- Python -----------------------------------------------------------------

def _py(server: FakeServer, *args: str) -> subprocess.CompletedProcess[str]:
    return run_command(
        [
            sys.executable,
            "examples/python/manage_pin.py",
            *args,
            "--server-base",
            server.base_url,
            "--auth",
            "testuser:testpass",
        ],
        os.environ.copy(),
    )


def test_python_model_a() -> None:
    with fake_loxone_server() as server:
        assert _py(server, "resolve").stdout.strip() == server.target_base

        users = ll_value(_py(server, "list-users").stdout)
        assert users[0]["uuid"] == "uuid-123"

        assert ll_value(_py(server, "show-user", "uuid-123").stdout)["keycodes"] == []

        _py(server, "set-pin", "uuid-123", "1234")
        assert ll_value(_py(server, "show-user", "uuid-123").stdout)["keycodes"] == [
            {"code": "HASH-1234"}
        ]

        _py(server, "clear-pin", "uuid-123")
        assert ll_value(_py(server, "show-user", "uuid-123").stdout)["keycodes"] == []


def test_python_model_b() -> None:
    with fake_loxone_server() as server:
        groups = ll_value(_py(server, "list-groups").stdout)
        ap1 = next(g["uuid"] for g in groups if g["name"] == "ap1")

        from_unix, until_unix = 1735689600, 1735776000
        created = ll_value(
            _py(server, "create-guest", ap1, "rez-1", "246813", str(from_unix), str(until_unix)).stdout
        )
        uuid = created["uuid"]
        assert created["usergroups"] == [ap1]
        assert created["keycodes"] == [{"code": "246813"}]
        assert created["validFrom"] == from_unix - LOXONE_EPOCH_OFFSET
        assert created["validUntil"] == until_unix - LOXONE_EPOCH_OFFSET

        assert ll_value(_py(server, "show-user", uuid).stdout)["uuid"] == uuid
        _py(server, "delete-user", uuid)


# --- Node -------------------------------------------------------------------

def _node_env(server: FakeServer) -> dict[str, str]:
    env = os.environ.copy()
    env.update({"SERVER_BASE": server.base_url, "AUTH": "testuser:testpass"})
    return env


def _node(server: FakeServer, *args: str) -> subprocess.CompletedProcess[str]:
    return run_command(["node", "examples/node/managePin.js", *args], _node_env(server))


def test_node_model_a() -> None:
    if not shutil.which("node"):
        pytest.skip("node executable not available")
    with fake_loxone_server() as server:
        assert _node(server, "resolve").stdout.strip() == server.target_base

        users = ll_value(_node(server, "list-users").stdout)
        assert users[0]["uuid"] == "uuid-123"

        assert ll_value(_node(server, "show-user", "uuid-123").stdout)["keycodes"] == []

        _node(server, "set-pin", "uuid-123", "1234")
        assert ll_value(_node(server, "show-user", "uuid-123").stdout)["keycodes"] == [
            {"code": "HASH-1234"}
        ]

        _node(server, "clear-pin", "uuid-123")
        assert ll_value(_node(server, "show-user", "uuid-123").stdout)["keycodes"] == []


def test_node_model_b() -> None:
    if not shutil.which("node"):
        pytest.skip("node executable not available")
    with fake_loxone_server() as server:
        groups = ll_value(_node(server, "list-groups").stdout)
        ap1 = next(g["uuid"] for g in groups if g["name"] == "ap1")

        from_unix, until_unix = 1735689600, 1735776000
        created = ll_value(
            _node(server, "create-guest", ap1, "rez-1", "246813", str(from_unix), str(until_unix)).stdout
        )
        uuid = created["uuid"]
        assert created["usergroups"] == [ap1]
        assert created["keycodes"] == [{"code": "246813"}]
        assert created["validFrom"] == from_unix - LOXONE_EPOCH_OFFSET
        assert created["validUntil"] == until_unix - LOXONE_EPOCH_OFFSET

        assert ll_value(_node(server, "show-user", uuid).stdout)["uuid"] == uuid
        _node(server, "delete-user", uuid)
