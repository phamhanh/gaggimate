#!/usr/bin/env python3
"""Shared WebSocket client for Gaggimate device APIs (stdlib only)."""

from __future__ import annotations

import json
import os
import socket
import struct
import time
from base64 import b64encode
from dataclasses import dataclass
from typing import Any, Iterator
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

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

    def _set_recv_timeout(self, seconds: float) -> None:
        if self._sock is not None:
            self._sock.settimeout(max(0.1, seconds))

    def request(self, message_type: str, timeout: float = 60.0, **payload: Any) -> dict[str, Any]:
        request_id = f"r{time.time_ns()}"
        body = {"tp": message_type, "rid": request_id, **payload}
        self._send_text(json.dumps(body))
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._set_recv_timeout(deadline - time.time())
            try:
                message = json.loads(self._recv_text())
            except socket.timeout:
                continue
            if message.get("rid") == request_id:
                return message
        raise TimeoutError(f"Timed out waiting for response to {message_type}")

    def iter_messages(self, timeout: float = 600.0) -> Iterator[dict[str, Any]]:
        """Yield all incoming JSON messages until timeout between reads."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if self._sock is not None:
                self._sock.settimeout(max(0.1, min(remaining, 30.0)))
            try:
                yield json.loads(self._recv_text())
            except socket.timeout:
                continue

    def wait_for_tp(self, message_type: str, timeout: float = 60.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if self._sock is not None:
                self._sock.settimeout(max(0.1, min(remaining, 30.0)))
            try:
                message = json.loads(self._recv_text())
            except socket.timeout:
                continue
            if message.get("tp") == message_type:
                return message
        raise TimeoutError(f"Timed out waiting for {message_type}")

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

    def list_history(self) -> list[dict[str, Any]]:
        response = self.request("req:history:list", timeout=120.0)
        if response.get("error"):
            raise RuntimeError(response["error"])
        return list(response.get("history") or [])

    def fetch_ota_settings(self, *, refresh: bool = False, channel: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"tp": "req:ota-settings"}
        if refresh:
            payload["update"] = True
            if channel is not None:
                payload["channel"] = channel
        request_id = f"r{time.time_ns()}"
        payload["rid"] = request_id
        self._send_text(json.dumps(payload))
        deadline = time.time() + 120.0
        while time.time() < deadline:
            self._set_recv_timeout(deadline - time.time())
            try:
                message = json.loads(self._recv_text())
            except socket.timeout:
                continue
            if message.get("tp") == "res:ota-settings":
                return message
        raise TimeoutError("Timed out waiting for res:ota-settings")

    def start_ota(self, component: str) -> None:
        """Fire-and-forget; firmware does not ack req:ota-start."""
        self._send_text(json.dumps({"tp": "req:ota-start", "cp": component}))

    def send_json(self, payload: dict[str, Any]) -> None:
        self._send_text(json.dumps(payload))

    def set_pid(
        self,
        kp: float,
        ki: float | None = None,
        kd: float | None = None,
        *,
        kff: float | None = None,
        persist: bool = False,
    ) -> None:
        """Apply PID via req:set-pid (see docs/pid-telemetry-api.md)."""
        body: dict[str, Any] = {"tp": "req:set-pid", "kp": kp, "persist": persist}
        if ki is not None:
            body["ki"] = ki
        if kd is not None:
            body["kd"] = kd
        if kff is not None:
            body["kff"] = kff
        self.send_json(body)

    def set_target_temp(self, temp: float) -> None:
        self.send_json({"tp": "req:set-target-temp", "temp": temp})

    def set_mode(self, mode: int) -> None:
        """MODE_STANDBY=0, MODE_BREW=1 (see src/display/core/constants.h)."""
        self.send_json({"tp": "req:change-mode", "mode": int(mode)})

    def stream_status(
        self,
        duration: float = 60.0,
        *,
        on_status: Any | None = None,
    ) -> list[dict[str, Any]]:
        """Collect evt:status messages for up to duration seconds."""
        ticks: list[dict[str, Any]] = []
        deadline = time.time() + duration
        while time.time() < deadline:
            remaining = deadline - time.time()
            if self._sock is not None:
                self._sock.settimeout(max(0.1, min(remaining, 2.0)))
            try:
                message = json.loads(self._recv_text())
            except socket.timeout:
                continue
            if message.get("tp") != "evt:status":
                continue
            ticks.append(message)
            if on_status is not None:
                on_status(message)
        return ticks

    def wait_status(self, timeout: float = 10.0) -> dict[str, Any]:
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            if self._sock is not None:
                self._sock.settimeout(max(0.1, min(remaining, 2.0)))
            try:
                message = json.loads(self._recv_text())
            except socket.timeout:
                continue
            if message.get("tp") == "evt:status":
                return message
        raise TimeoutError("Timed out waiting for evt:status")


def fetch_api_status(host: str, port: int = DEFAULT_PORT, timeout: float = 10.0) -> dict[str, Any]:
    ip_host = host
    try:
        socket.inet_pton(socket.AF_INET, host)
    except OSError:
        ip_host = socket.gethostbyname(host)
    url = f"http://{ip_host}:{port}/api/status" if port != 80 else f"http://{ip_host}/api/status"
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def fetch_api_settings(host: str, port: int = DEFAULT_PORT, timeout: float = 10.0) -> dict[str, Any]:
    ip_host = host
    try:
        socket.inet_pton(socket.AF_INET, host)
    except OSError:
        ip_host = socket.gethostbyname(host)
    url = f"http://{ip_host}:{port}/api/settings" if port != 80 else f"http://{ip_host}/api/settings"
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def post_api_settings(host: str, fields: dict[str, str], port: int = DEFAULT_PORT, timeout: float = 15.0) -> dict[str, Any]:
    """POST application/x-www-form-urlencoded to /api/settings (always persists)."""
    ip_host = host
    try:
        socket.inet_pton(socket.AF_INET, host)
    except OSError:
        ip_host = socket.gethostbyname(host)
    url = f"http://{ip_host}:{port}/api/settings" if port != 80 else f"http://{ip_host}/api/settings"
    data = urlencode(fields).encode()
    request = Request(url, data=data, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def parse_pid_csv(pid: str) -> tuple[float, float, float, float]:
    parts = [part.strip() for part in pid.split(",")]
    while len(parts) < 4:
        parts.append("0")
    return float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3])


def parse_status_tick(message: dict[str, Any], *, t: float | None = None) -> dict[str, Any]:
    """Normalize evt:status / GET /api/status to flat tick dict (docs/pid-telemetry-api.md)."""
    live = message.get("pidLive") or {}
    return {
        "t": time.time() if t is None else t,
        "ct": float(message.get("ct", 0)),
        "tt": float(message.get("tt", 0)),
        "p": float(live.get("p", 0)),
        "i": float(live.get("i", 0)),
        "d": float(live.get("d", 0)),
        "kff": float(live.get("kff", 0)),
        "out": float(live.get("out", message.get("heaterPower", message.get("out", 0)))),
        "frozen": int(live.get("frozen", 0)),
        "kp": float(message.get("kp", 0)),
        "ki": float(message.get("ki", 0)),
        "kd": float(message.get("kd", 0)),
        "kffGain": float(message.get("kffGain", message.get("kff", 0))),
    }


def status_has_pid_live(message: dict[str, Any]) -> bool:
    live = message.get("pidLive")
    return isinstance(live, dict) and "p" in live


def read_status_once(host: str, port: int = DEFAULT_PORT, timeout: float = 15.0) -> dict[str, Any]:
    """One status tick from HTTP /api/status or WebSocket evt:status."""
    try:
        status = fetch_api_status(host, port, timeout=timeout)
        if status_has_pid_live(status):
            return status
    except OSError:
        pass

    with GaggimateWsClient(host, port, timeout=timeout) as client:
        message = client.wait_status(timeout=timeout)
        if not status_has_pid_live(message):
            raise RuntimeError("evt:status missing pidLive — deploy Plan 1 display firmware")
        return message


def print_status_once(host: str, port: int = DEFAULT_PORT, timeout: float = 15.0) -> None:
    """Print one status line from HTTP or WS (for Plan 1 smoke test)."""
    try:
        status = read_status_once(host, port, timeout=timeout)
        print(_format_status_line(parse_status_tick(status)))
    except OSError as error:
        print(f"status read failed: {error}", file=__import__("sys").stderr)
        raise


def _format_status_line(tick: dict[str, Any]) -> str:
    return (
        f"t={tick['t']:.1f} ct={tick['ct']:.2f} tt={tick['tt']:.2f} "
        f"p={tick['p']:.1f} i={tick['i']:.1f} d={tick['d']:.1f} out={tick['out']:.0f} "
        f"frozen={tick['frozen']} kp={tick['kp']:.2f} ki={tick['ki']:.3f} kd={tick['kd']:.0f}"
    )
