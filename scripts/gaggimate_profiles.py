#!/usr/bin/env python3
"""
List, inspect, and delete Gaggimate profiles over the device WebSocket API.

Use when the web UI profiles page fails to load (corrupt profile JSON, invalid
type, empty phases, etc.). No extra Python packages required.

Examples:
  python3 scripts/gaggimate_profiles.py list
  python3 scripts/gaggimate_profiles.py list --host 192.168.1.42
  python3 scripts/gaggimate_profiles.py show AZbmJRldaT
  python3 scripts/gaggimate_profiles.py delete JrHBMhpkl5 JuXn3QJ4Nf
  python3 scripts/gaggimate_profiles.py delete-broken --dry-run
  python3 scripts/gaggimate_profiles.py delete-broken --yes
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time
from base64 import b64encode
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse


DEFAULT_HOST = "gaggimate.local"
DEFAULT_PORT = 80
WS_PATH = "/ws"


@dataclass
class ProfileSummary:
    id: str
    label: str
    type: str | None
    phases: int
    selected: bool
    favorite: bool
    utility: bool

    @property
    def issues(self) -> list[str]:
        problems: list[str] = []
        if not self.id:
            problems.append("missing-id")
        if self.label in ("", "null", None):
            problems.append("bad-label")
        if self.type in ("", "null", None):
            problems.append("bad-type")
        if self.phases == 0 and not self.utility:
            problems.append("no-phases")
        return problems


class GaggimateWsClient:
    def __init__(self, host: str, port: int = DEFAULT_PORT, timeout: float = 15.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None

    def __enter__(self) -> GaggimateWsClient:
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def connect(self) -> None:
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        key = b64encode(os.urandom(16)).decode()
        request = (
            f"GET {WS_PATH} HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self._sock.sendall(request.encode())
        response = b""
        while b"\r\n\r\n" not in response:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket handshake closed early")
            response += chunk
        status_line = response.split(b"\r\n", 1)[0]
        if b"101" not in status_line:
            raise ConnectionError(f"WebSocket handshake failed: {status_line.decode(errors='replace')}")

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _send_text(self, text: str) -> None:
        assert self._sock is not None
        data = text.encode()
        mask = os.urandom(4)
        frame = bytearray([0x81])
        length = len(data)
        if length < 126:
            frame.append(0x80 | length)
        elif length < 65536:
            frame.append(0x80 | 126)
            frame.extend(struct.pack(">H", length))
        else:
            frame.append(0x80 | 127)
            frame.extend(struct.pack(">Q", length))
        frame.extend(mask)
        frame.extend(bytes(byte ^ mask[index % 4] for index, byte in enumerate(data)))
        self._sock.sendall(frame)

    def _recv_text(self) -> str:
        assert self._sock is not None
        parts: list[bytes] = []
        while True:
            header = self._recv_exact(2)
            byte1, byte2 = header
            fin = bool(byte1 & 0x80)
            opcode = byte1 & 0x0F
            masked = bool(byte2 & 0x80)
            length = byte2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            payload = self._recv_exact(length)
            if masked and mask is not None:
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
            if opcode in (0x1, 0x0):
                parts.append(payload)
            if fin:
                return b"".join(parts).decode()

    def _recv_exact(self, size: int) -> bytes:
        assert self._sock is not None
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            chunk = self._sock.recv(remaining)
            if not chunk:
                raise ConnectionError("WebSocket connection closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def request(self, message_type: str, timeout: float = 60.0, **payload: Any) -> dict[str, Any]:
        request_id = f"r{time.time_ns()}"
        body = {"tp": message_type, "rid": request_id, **payload}
        self._send_text(json.dumps(body))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = json.loads(self._recv_text())
            if message.get("rid") == request_id:
                return message
        raise TimeoutError(f"Timed out waiting for response to {message_type}")

    def list_profiles(self) -> list[ProfileSummary]:
        response = self.request("req:profiles:list")
        if response.get("error"):
            raise RuntimeError(response["error"])
        profiles: list[ProfileSummary] = []
        for item in response.get("profiles") or []:
            profiles.append(
                ProfileSummary(
                    id=item.get("id") or "",
                    label=item.get("label") or "",
                    type=item.get("type"),
                    phases=len(item.get("phases") or []),
                    selected=bool(item.get("selected")),
                    favorite=bool(item.get("favorite")),
                    utility=bool(item.get("utility")),
                )
            )
        return profiles

    def load_profile(self, profile_id: str) -> dict[str, Any]:
        response = self.request("req:profiles:load", id=profile_id)
        if response.get("error"):
            raise RuntimeError(response["error"])
        profile = response.get("profile")
        if not profile:
            raise RuntimeError("Profile not found")
        return profile

    def delete_profile(self, profile_id: str) -> None:
        response = self.request("req:profiles:delete", id=profile_id)
        if response.get("error"):
            raise RuntimeError(response["error"])


def parse_host(value: str) -> tuple[str, int]:
    if "://" in value:
        parsed = urlparse(value)
        host = parsed.hostname or DEFAULT_HOST
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port
    if ":" in value and value.rsplit(":", 1)[-1].isdigit():
        host, port_str = value.rsplit(":", 1)
        return host, int(port_str)
    return value, DEFAULT_PORT


def print_profiles(profiles: list[ProfileSummary], *, json_output: bool) -> None:
    if json_output:
        print(json.dumps([profile.__dict__ for profile in profiles], indent=2))
        return

    for profile in profiles:
        flags: list[str] = []
        if profile.selected:
            flags.append("SELECTED")
        if profile.favorite:
            flags.append("fav")
        if profile.utility:
            flags.append("utility")
        issue_text = f" ISSUES={','.join(profile.issues)}" if profile.issues else ""
        flag_text = f" [{' '.join(flags)}]" if flags else ""
        print(
            f"{profile.id}\t{profile.label!r}\t"
            f"type={profile.type!r}\tphases={profile.phases}{flag_text}{issue_text}"
        )


def cmd_list(client: GaggimateWsClient, args: argparse.Namespace) -> int:
    profiles = client.list_profiles()
    if args.broken_only:
        profiles = [profile for profile in profiles if profile.issues]
    print_profiles(profiles, json_output=args.json)
    if not args.json:
        print(f"\nTotal: {len(profiles)}", file=sys.stderr)
    return 0


def cmd_show(client: GaggimateWsClient, args: argparse.Namespace) -> int:
    profile = client.load_profile(args.id)
    print(json.dumps(profile, indent=2))
    return 0


def cmd_delete(client: GaggimateWsClient, args: argparse.Namespace) -> int:
    ids = args.ids
    if not ids:
        print("No profile ids given.", file=sys.stderr)
        return 1

    profiles = {profile.id: profile for profile in client.list_profiles()}
    for profile_id in ids:
        profile = profiles.get(profile_id)
        label = profile.label if profile else "?"
        if profile and profile.selected and not args.force:
            print(
                f"Refusing to delete selected profile {profile_id} ({label!r}). "
                "Select another profile on the machine first, or pass --force.",
                file=sys.stderr,
            )
            return 1

    for profile_id in ids:
        profile = profiles.get(profile_id)
        label = profile.label if profile else "?"
        if args.dry_run:
            print(f"would delete {profile_id} ({label!r})")
            continue
        client.delete_profile(profile_id)
        print(f"deleted {profile_id} ({label!r})")

    return 0


def cmd_delete_broken(client: GaggimateWsClient, args: argparse.Namespace) -> int:
    broken = [profile for profile in client.list_profiles() if profile.issues]
    if not broken:
        print("No broken profiles found.")
        return 0

    print_profiles(broken, json_output=False)
    selected_broken = [profile for profile in broken if profile.selected]
    if selected_broken and not args.force:
        print(
            "\nRefusing to delete: a broken profile is currently selected. "
            "Select another profile on the machine first, or pass --force.",
            file=sys.stderr,
        )
        return 1

    if not args.yes and not args.dry_run:
        answer = input(f"\nDelete {len(broken)} broken profile(s)? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 1

    for profile in broken:
        if args.dry_run:
            print(f"would delete {profile.id} ({profile.label!r})")
            continue
        client.delete_profile(profile.id)
        print(f"deleted {profile.id} ({profile.label!r})")

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List and delete Gaggimate profiles via WebSocket (no auth, local network).",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("GAGGIMATE_HOST", DEFAULT_HOST),
        help=f"Hostname or host:port (default: {DEFAULT_HOST} or $GAGGIMATE_HOST)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="TCP connect timeout in seconds (default: 15)",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON for list output")

    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List profiles")
    list_parser.add_argument(
        "--broken-only",
        action="store_true",
        help="Only show profiles with detectable issues",
    )
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser("show", help="Show one profile as JSON")
    show_parser.add_argument("id", help="Profile id")
    show_parser.set_defaults(func=cmd_show)

    delete_parser = subparsers.add_parser("delete", help="Delete profiles by id")
    delete_parser.add_argument("ids", nargs="+", help="Profile id(s)")
    delete_parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    delete_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow deleting the currently selected profile",
    )
    delete_parser.set_defaults(func=cmd_delete)

    broken_parser = subparsers.add_parser(
        "delete-broken",
        help="Delete profiles with bad type/label or zero phases (non-utility)",
    )
    broken_parser.add_argument("--dry-run", action="store_true", help="Print actions only")
    broken_parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    broken_parser.add_argument(
        "--force",
        action="store_true",
        help="Allow deleting if a broken profile is selected",
    )
    broken_parser.set_defaults(func=cmd_delete_broken)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    host, port = parse_host(args.host)

    try:
        with GaggimateWsClient(host, port, timeout=args.timeout) as client:
            return args.func(client, args)
    except (TimeoutError, ConnectionError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        print(
            f"Could not reach Gaggimate at {host}:{port}. "
            "Check Wi‑Fi, try --host with the device IP, or AP mode at 4.4.4.1.",
            file=sys.stderr,
        )
        return 2
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
