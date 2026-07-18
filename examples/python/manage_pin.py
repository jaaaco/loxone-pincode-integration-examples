#!/usr/bin/env python3
"""Command-line helper for managing Loxone user PIN codes via CloudDNS.

Two integration models:
  Model A (static user per door):  set-pin / clear-pin
  Model B (ephemeral user per reservation, in a group):
    list-groups / create-guest / delete-user
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER_BASE = "https://dns.loxonecloud.com/YOUR-MINISERVER-SERIAL"
AUTH = "USERNAME:PASSWORD"

# Loxone timestamps count seconds from 2009-01-01 00:00:00 UTC, not the Unix epoch.
LOXONE_EPOCH_OFFSET = 1230768000


def to_loxone_epoch(unix_seconds: int) -> int:
    return int(unix_seconds) - LOXONE_EPOCH_OFFSET


def resolve_target_base(server_base: str, auth: str) -> str:
    """Resolve the current Miniserver address without following redirects."""
    username, password = auth.split(":", 1)

    class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
            return None

    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, server_base, username, password)
    auth_handler = urllib.request.HTTPBasicAuthHandler(password_mgr)
    opener = urllib.request.build_opener(auth_handler, NoRedirectHandler())

    request = urllib.request.Request(server_base, method="GET")
    try:
        with opener.open(request) as response:  # pragma: no cover - expected redirect
            return response.geturl()
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location")
        if exc.code in (301, 302, 303, 307, 308) and location:
            return location.rstrip("/")
        raise


def _authorized_request(url: str, auth: str) -> urllib.request.Request:
    username, password = auth.split(":", 1)
    credentials = f"{username}:{password}".encode("utf-8")
    token = base64.b64encode(credentials).decode("ascii")
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Basic {token}")
    request.add_header("Accept", "application/json")
    return request


def get_json(url: str, auth: str) -> str:
    request = _authorized_request(url, auth)
    with urllib.request.urlopen(request) as response:
        return response.read().decode("utf-8")


def build_target_url(target_base: str, path: str) -> str:
    return urllib.parse.urljoin(target_base.rstrip("/") + "/", path.lstrip("/"))


def list_users(target_base: str, auth: str) -> str:
    return get_json(build_target_url(target_base, "/jdev/sps/getuserlist2"), auth)


def show_user(target_base: str, auth: str, uuid: str) -> str:
    return get_json(build_target_url(target_base, f"/jdev/sps/getuser/{uuid}"), auth)


def update_pin(target_base: str, auth: str, uuid: str, pin: str | None) -> str:
    pin_segment = pin or ""
    return get_json(
        build_target_url(target_base, f"/jdev/sps/updateuseraccesscode/{uuid}/{pin_segment}"),
        auth,
    )


def list_groups(target_base: str, auth: str) -> str:
    return get_json(build_target_url(target_base, "/jdev/sps/getgrouplist"), auth)


def create_guest(
    target_base: str,
    auth: str,
    group_uuid: str,
    name: str,
    pin: str,
    from_unix: int | None,
    until_unix: int | None,
) -> str:
    start = int(from_unix) if from_unix is not None else int(time.time())
    end = int(until_unix) if until_unix is not None else start + 86400
    user = {
        "name": name,
        "userState": 4,
        "usergroups": [group_uuid],
        "validFrom": to_loxone_epoch(start),
        "validUntil": to_loxone_epoch(end),
        "expirationAction": 1,
        "keycodes": [{"code": pin}],
    }
    encoded = urllib.parse.quote(json.dumps(user, separators=(",", ":")), safe="")
    return get_json(build_target_url(target_base, f"/jdev/sps/addoredituser/{encoded}"), auth)


def delete_user(target_base: str, auth: str, uuid: str) -> str:
    return get_json(build_target_url(target_base, f"/jdev/sps/deleteuser/{uuid}"), auth)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "resolve",
            "list-users",
            "show-user",
            "set-pin",
            "clear-pin",
            "list-groups",
            "create-guest",
            "delete-user",
        ],
        help="Action to perform",
    )
    parser.add_argument("args", nargs="*", help="Positional arguments for the command")
    parser.add_argument("--server-base", default=SERVER_BASE, help="CloudDNS base URL")
    parser.add_argument("--auth", default=AUTH, help="Credentials in the form user:password")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    server_base = args.server_base
    auth = args.auth
    rest = args.args

    if args.command == "resolve":
        print(resolve_target_base(server_base, auth))
        return 0

    target_base = resolve_target_base(server_base, auth)

    if args.command == "list-users":
        print(list_users(target_base, auth))
    elif args.command == "show-user":
        if len(rest) < 1:
            raise SystemExit("Usage: show-user <uuid>")
        print(show_user(target_base, auth, rest[0]))
    elif args.command == "set-pin":
        if len(rest) < 2:
            raise SystemExit("Usage: set-pin <uuid> <pin>")
        print(update_pin(target_base, auth, rest[0], rest[1]))
    elif args.command == "clear-pin":
        if len(rest) < 1:
            raise SystemExit("Usage: clear-pin <uuid>")
        print(update_pin(target_base, auth, rest[0], None))
    elif args.command == "list-groups":
        print(list_groups(target_base, auth))
    elif args.command == "create-guest":
        if len(rest) < 3:
            raise SystemExit(
                "Usage: create-guest <group-uuid> <name> <pin> [from-unix] [until-unix]"
            )
        from_unix = int(rest[3]) if len(rest) > 3 else None
        until_unix = int(rest[4]) if len(rest) > 4 else None
        print(create_guest(target_base, auth, rest[0], rest[1], rest[2], from_unix, until_unix))
    elif args.command == "delete-user":
        if len(rest) < 1:
            raise SystemExit("Usage: delete-user <uuid>")
        print(delete_user(target_base, auth, rest[0]))
    else:
        raise SystemExit(f"Unsupported command: {args.command}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
