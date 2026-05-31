#!/usr/bin/env python3
"""
Back up device profiles and shots, run a local release, and OTA-update the machine.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from device_http import device_web_urls
from gaggimate_ws import DEFAULT_HOST, GaggimateWsClient, parse_host

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "out" / "release-manifest.json"
GITHUB_RELEASES = "https://github.com/phamhanh/gaggimate/releases"
OTA_PHASE_FINISHED = 4


def print_device_links(host: str, port: int) -> None:
    ip, home_url, ota_url = device_web_urls(host, port)
    print(f"Device IP:        {ip}")
    print(f"Web UI:           {home_url}")
    print(f"System & Updates: {ota_url}")


def ota_update_plan(
    settings: dict,
    artifacts: dict,
    *,
    flash_built: bool,
) -> tuple[bool, bool, list[str]]:
    """Decide what deploy will try to OTA; return (display, controller, explanation lines)."""
    built_display = bool(artifacts.get("display-firmware") and artifacts.get("display-filesystem"))
    built_controller = bool(artifacts.get("board-firmware"))
    device_wants_display = bool(settings.get("displayUpdateAvailable"))
    device_wants_controller = bool(settings.get("controllerUpdateAvailable"))

    lines: list[str] = []
    display_version = settings.get("displayVersion") or "?"
    latest = settings.get("latestVersion") or ""
    if latest:
        lines.append(f"GitHub latest (on device): v{latest.lstrip('v')}")
    else:
        lines.append(
            "GitHub latest (on device): not loaded yet — open the Updates page and "
            "click Save & Refresh first"
        )
    lines.append(f"Display firmware now:      {display_version}")

    if flash_built:
        update_display = built_display
        update_controller = built_controller
        lines.append("Mode: --ota-flash-built (attempt every component built in out/)")
    else:
        update_display = built_display and device_wants_display
        update_controller = built_controller and device_wants_controller
        lines.append("Mode: automatic (only if device reports an update is available)")

    lines.append("")
    lines.append("Display update will run:" + (" yes" if update_display else " no"))
    lines.append(f"  built display-firmware + display-filesystem in out/: {built_display}")
    lines.append(f"  device displayUpdateAvailable:                         {device_wants_display}")

    lines.append("Controller update will run:" + (" yes" if update_controller else " no"))
    lines.append(f"  built board-firmware in out/:              {built_controller}")
    lines.append(f"  device controllerUpdateAvailable:           {device_wants_controller}")

    if not flash_built and built_display and not device_wants_display:
        lines.append("")
        lines.append(
            "Note: display=False usually means the device thinks it is already on the "
            "latest version (or has not refreshed OTA info from GitHub). After you flash, "
            "this becomes False until a newer release exists."
        )

    return update_display, update_controller, lines


def assert_clean_git_tree() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    dirty = result.stdout.strip()
    if not dirty:
        return
    print("Uncommitted changes — commit or stash before deploy:\n", file=sys.stderr)
    print(dirty, file=sys.stderr)
    raise SystemExit(1)


def run_script(script: str, args: list[str], *, dry_run: bool) -> None:
    command = [str(ROOT / "scripts" / script), *args]
    print(f"\n→ {' '.join(command)}")
    if dry_run:
        return
    subprocess.run(command, cwd=ROOT, check=True)


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        raise FileNotFoundError(f"Missing {MANIFEST_PATH.relative_to(ROOT)} — run release or build first")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def wait_for_reboot(host: str, port: int, connect_timeout: float, wait_timeout: float) -> None:
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        try:
            with GaggimateWsClient(host, port, timeout=connect_timeout):
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
) -> None:
    print(f"Starting OTA: {component}")
    with GaggimateWsClient(host, port, timeout=connect_timeout) as client:
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


def run_ota(
    host: str,
    port: int,
    *,
    update_display: bool,
    update_controller: bool,
    timeout: float,
    connect_timeout: float,
    dry_run: bool,
) -> None:
    if not update_display and not update_controller:
        print("\nOTA: nothing to update (device already on target or no matching artifacts).")
        return

    components: list[str] = []
    if update_display:
        components.append("display")
    if update_controller:
        components.append("controller")

    print(f"\nOTA: will update {', '.join(components)} on {host} (sequential)")

    if dry_run:
        return

    per_component_timeout = timeout / max(len(components), 1)

    for index, component in enumerate(components):
        if index > 0:
            print("Waiting for device reboot...")
            wait_for_reboot(host, port, connect_timeout, per_component_timeout)
        run_single_ota(host, port, component, per_component_timeout, connect_timeout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Back up device data, release firmware, and OTA-update the machine.",
        epilog=(
            "Examples:\n"
            "  ./scripts/deploy.sh\n"
            "  ./scripts/deploy.sh --no-backup\n"
            "  ./scripts/deploy.sh --no-backup --release-only\n"
            "  ./scripts/deploy.sh --update-only\n"
            "  ./scripts/deploy.sh --no-backup -- --build-only"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=os.environ.get("GAGGIMATE_HOST", DEFAULT_HOST))
    parser.add_argument("--release-only", action="store_true", help="Back up + release; skip OTA")
    parser.add_argument("--update-only", action="store_true", help="OTA only (use existing out/)")
    parser.add_argument("--no-backup", action="store_true", help="Skip device backup (use existing data/p, data/h)")
    parser.add_argument(
        "--ota-flash-built",
        action="store_true",
        help="Attempt OTA for every component in out/ (ignore device *UpdateAvailable flags)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show plan only")
    parser.add_argument("--timeout", type=float, default=600.0, help="OTA wait timeout (seconds)")
    parser.add_argument("--timeout-connect", type=float, default=15.0, help="WebSocket connect timeout")
    parser.add_argument("--http-timeout", type=float, default=60.0, help="HTTP download timeout (seconds)")
    parser.add_argument(
        "release_args",
        nargs=argparse.REMAINDER,
        help="Arguments passed to release.sh after --",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.release_args and args.release_args[0] == "--":
        args.release_args = args.release_args[1:]

    if args.release_only and args.update_only:
        print("Error: --release-only and --update-only are mutually exclusive", file=sys.stderr)
        return 1

    if not args.dry_run:
        assert_clean_git_tree()

    host, port = parse_host(args.host)
    print_device_links(host, port)

    do_backup = not args.no_backup and not args.update_only
    do_release = not args.update_only
    do_ota = not args.release_only

    plan_lines = ["Deploy plan", "==========="]
    step = 1
    if do_backup:
        plan_lines.append(f"{step}. Back up profiles + shots from Gaggimate at {host}")
        step += 1
    elif not args.update_only and args.no_backup:
        plan_lines.append(
            f"{step}. Skip device backup — use existing data/p and data/h "
            "(or profiles/seed at build time if empty)"
        )
        step += 1
    if do_release:
        plan_lines.append(f"{step}. Release ({' '.join(args.release_args) or 'default flags'})")
        step += 1
    if do_ota:
        plan_lines.append(f"{step}. Intelligent OTA")

    print("\n".join(plan_lines))

    if do_backup:
        backup_args = ["--host", host, "--http-timeout", str(args.http_timeout)]
        if args.dry_run:
            backup_args.append("--dry-run")
        run_script("backup-spiffs-data.sh", backup_args, dry_run=args.dry_run)

    if do_release:
        release_args = list(args.release_args)
        if "--yes" not in release_args:
            release_args = ["--yes", *release_args]
        if args.dry_run and "--dry-run" not in release_args:
            release_args = ["--dry-run", *release_args]
        run_script("release.sh", release_args, dry_run=args.dry_run)

    if do_ota:
        manifest = load_manifest() if not args.dry_run else {
            "artifacts": {
                "display-firmware": True,
                "display-filesystem": True,
                "board-firmware": True,
            }
        }
        artifacts = manifest.get("artifacts", {})
        manifest_version = manifest.get("version")
        if manifest_version:
            print(f"\nGitHub release: {GITHUB_RELEASES}/tag/{manifest_version}")

        _, _, ota_url = device_web_urls(host, port)
        print(f"Update in browser: {ota_url}\n")

        try:
            with GaggimateWsClient(host, port, timeout=args.timeout_connect) as client:
                settings = client.fetch_ota_settings(refresh=not args.dry_run)
        except (TimeoutError, ConnectionError, OSError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2

        update_display, update_controller, explanation = ota_update_plan(
            settings,
            artifacts,
            flash_built=args.ota_flash_built,
        )
        print("\n".join(explanation))
        print(
            f"\nOTA decision: display={update_display} controller={update_controller}"
        )

        try:
            run_ota(
                host,
                port,
                update_display=update_display,
                update_controller=update_controller,
                timeout=args.timeout,
                connect_timeout=args.timeout_connect,
                dry_run=args.dry_run,
            )
        except TimeoutError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
