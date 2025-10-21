#!/usr/bin/env python3
"""Command-line helper for managing Loxone user PIN codes via CloudDNS."""

from __future__ import annotations

import argparse
import base64
import sys
import urllib.error
import urllib.parse
import urllib.request

SERVER_BASE = "https://dns.loxonecloud.com/YOUR-MINISERVER-SERIAL"
AUTH = "USERNAME:PASSWORD"

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


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["resolve", "list", "show", "set", "clear"], help="Action to perform")
    parser.add_argument("uuid", nargs="?", help="Target user UUID")
    parser.add_argument("pin", nargs="?", help="PIN value (2-8 digits)")
    parser.add_argument("--server-base", default=SERVER_BASE, help="CloudDNS base URL")
    parser.add_argument("--auth", default=AUTH, help="Credentials in the form user:password")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    server_base = args.server_base
    auth = args.auth

    if args.command == "resolve":
        print(resolve_target_base(server_base, auth))
        return 0

    target_base = resolve_target_base(server_base, auth)

    if args.command == "list":
        print(list_users(target_base, auth))
    elif args.command == "show":
        if not args.uuid:
            raise SystemExit("UUID is required for the show command")
        print(show_user(target_base, auth, args.uuid))
    elif args.command == "set":
        if not args.uuid or not args.pin:
            raise SystemExit("UUID and PIN are required for the set command")
        print(update_pin(target_base, auth, args.uuid, args.pin))
    elif args.command == "clear":
        if not args.uuid:
            raise SystemExit("UUID is required for the clear command")
        print(update_pin(target_base, auth, args.uuid, None))
    else:
        raise SystemExit(f"Unsupported command: {args.command}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
