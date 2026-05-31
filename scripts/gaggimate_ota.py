"""Shared OTA helpers for deploy and update-device scripts."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gaggimate_ws import GaggimateWsClient

OTA_PHASE_FINISHED = 4


def normalize_version(version: str) -> str:
    """Return semver tag with v prefix, e.g. v1.9.5."""
    stripped = (version or "").strip().lstrip("v")
    if not stripped:
        return ""
    return f"v{stripped}"


def version_equal(left: str, right: str) -> bool:
    return normalize_version(left) == normalize_version(right)


def wait_for_github_latest(
    client: GaggimateWsClient,
    expected: str,
    *,
    timeout: float = 600.0,
    poll_interval: float = 3.0,
) -> dict:
    """Poll OTA settings until device latestVersion matches expected release."""
    target = normalize_version(expected)
    if not target:
        raise ValueError("expected version is empty")

    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = client.fetch_ota_settings(refresh=True)
        latest = normalize_version(str(last.get("latestVersion") or ""))
        if latest and version_equal(latest, target):
            return last
        print(
            f"  Waiting for GitHub latest on device: {latest or '(unknown)'} "
            f"(want {target})..."
        )
        time.sleep(poll_interval)

    latest = normalize_version(str(last.get("latestVersion") or ""))
    raise TimeoutError(
        f"Device GitHub latest is {latest or '(unknown)'} after {timeout:.0f}s; "
        f"expected {target}. Wait and retry, or open /ota and Save & Refresh."
    )


def wait_for_device_versions(
    client: GaggimateWsClient,
    target: str,
    *,
    timeout: float = 600.0,
    poll_interval: float = 3.0,
) -> dict:
    """Poll OTA settings until display and controller both match target."""
    target_norm = normalize_version(target)
    if not target_norm:
        raise ValueError("target version is empty")

    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        last = client.fetch_ota_settings(refresh=True)
        display = normalize_version(str(last.get("displayVersion") or ""))
        controller = normalize_version(str(last.get("controllerVersion") or ""))
        if display == target_norm and controller == target_norm:
            return last
        print(
            f"  Waiting for OTA versions: display={last.get('displayVersion')!r} "
            f"controller={last.get('controllerVersion')!r} (want {target_norm})..."
        )
        time.sleep(poll_interval)

    raise RuntimeError(
        "OTA verify failed: "
        f"display={last.get('displayVersion')!r} "
        f"controller={last.get('controllerVersion')!r} "
        f"(expected {target_norm} after {timeout:.0f}s)"
    )


def versions_behind(settings: dict, target: str) -> tuple[bool, bool]:
    """Return (need_display, need_controller) compared to target tag."""
    target_norm = normalize_version(target)
    display = normalize_version(str(settings.get("displayVersion") or ""))
    controller = normalize_version(str(settings.get("controllerVersion") or ""))
    return (
        bool(target_norm) and display != target_norm,
        bool(target_norm) and controller != target_norm,
    )


def ota_flash_plan(
    settings: dict,
    artifacts: dict,
    *,
    flash_built: bool,
) -> tuple[bool, bool, list[str]]:
    """Decide what to OTA; return (display, controller, explanation lines)."""
    built_display = bool(artifacts.get("display-firmware") and artifacts.get("display-filesystem"))
    built_controller = bool(artifacts.get("board-firmware"))
    device_wants_display = bool(settings.get("displayUpdateAvailable"))
    device_wants_controller = bool(settings.get("controllerUpdateAvailable"))

    lines: list[str] = []
    display_version = settings.get("displayVersion") or "?"
    controller_version = settings.get("controllerVersion") or "?"
    latest = settings.get("latestVersion") or ""
    if latest:
        lines.append(f"GitHub latest (on device): {normalize_version(str(latest))}")
    else:
        lines.append(
            "GitHub latest (on device): not loaded — open Updates and Save & Refresh first"
        )
    lines.append(f"Display firmware now:      {display_version}")
    lines.append(f"Controller firmware now:   {controller_version}")

    if flash_built:
        update_display = built_display
        update_controller = built_controller
        lines.append("Mode: flash all components built in out/ (ignore *UpdateAvailable)")
    else:
        update_display = built_display and device_wants_display
        update_controller = built_controller and device_wants_controller
        lines.append("Mode: device *UpdateAvailable flags only")

    lines.append("")
    lines.append("Display update will run:" + (" yes" if update_display else " no"))
    lines.append(f"  built display-firmware + display-filesystem in out/: {built_display}")
    lines.append(f"  device displayUpdateAvailable:                         {device_wants_display}")

    lines.append("Controller update will run:" + (" yes" if update_controller else " no"))
    lines.append(f"  built board-firmware in out/:              {built_controller}")
    lines.append(f"  device controllerUpdateAvailable:           {device_wants_controller}")

    return update_display, update_controller, lines


def verify_device_versions(settings: dict, target: str) -> None:
    """Raise RuntimeError if display or controller version does not match target."""
    target_norm = normalize_version(target)
    display = normalize_version(str(settings.get("displayVersion") or ""))
    controller = normalize_version(str(settings.get("controllerVersion") or ""))
    problems: list[str] = []
    if display != target_norm:
        problems.append(f"display={settings.get('displayVersion')!r} (expected {target_norm})")
    if controller != target_norm:
        problems.append(f"controller={settings.get('controllerVersion')!r} (expected {target_norm})")
    if problems:
        raise RuntimeError("OTA verify failed: " + ", ".join(problems))


def wait_for_reboot(
    host: str,
    port: int,
    connect_timeout: float,
    wait_timeout: float,
    *,
    client_cls: type,
) -> None:
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        try:
            with client_cls(host, port, timeout=connect_timeout):
                print("  Device back online.")
                return
        except (TimeoutError, ConnectionError, OSError):
            time.sleep(5)
    raise TimeoutError(f"Device did not come back within {wait_timeout:.0f}s after reboot")


def run_single_ota(
    host: str,
    port: int,
    component: str,
    timeout: float,
    connect_timeout: float,
    *,
    client_cls: type,
) -> None:
    print(f"Starting OTA: {component}")
    with client_cls(host, port, timeout=connect_timeout) as client:
        client.start_ota(component)
        for message in client.iter_messages(timeout=timeout):
            if message.get("tp") != "evt:ota-progress":
                continue
            phase = int(message.get("phase", 0))
            progress = int(message.get("progress", 0))
            label = {
                1: "display firmware",
                2: "display filesystem",
                3: "controller firmware",
                4: "finished",
            }.get(phase, f"phase {phase}")
            print(f"  OTA {label}: {progress}%")
            if phase >= OTA_PHASE_FINISHED:
                print(f"  {component} OTA finished.")
                return
    raise TimeoutError(f"{component} OTA did not finish within {timeout:.0f}s")


def run_ota_sequence(
    host: str,
    port: int,
    *,
    update_display: bool,
    update_controller: bool,
    timeout: float,
    connect_timeout: float,
    dry_run: bool,
    client_cls: type,
) -> None:
    if not update_display and not update_controller:
        print("\nOTA: nothing to update (already on target or no components selected).")
        return

    components: list[str] = []
    if update_display:
        components.append("display")
    if update_controller:
        components.append("controller")

    print(f"\nOTA: will update {', '.join(components)} on {host} (sequential)")

    if dry_run:
        return

    for index, component in enumerate(components):
        if index > 0:
            print("Waiting for device reboot...")
            wait_for_reboot(
                host,
                port,
                connect_timeout,
                timeout,
                client_cls=client_cls,
            )
        run_single_ota(
            host,
            port,
            component,
            timeout,
            connect_timeout,
            client_cls=client_cls,
        )
