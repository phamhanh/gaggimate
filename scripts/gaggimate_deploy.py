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

from gaggimate_ws import DEFAULT_HOST, GaggimateWsClient, parse_host

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "out" / "release-manifest.json"
OTA_PHASE_FINISHED = 4


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
        try:
            with GaggimateWsClient(host, port, timeout=args.timeout_connect) as client:
                settings = client.fetch_ota_settings(refresh=not args.dry_run)
        except (TimeoutError, ConnectionError, OSError) as error:
            print(f"Error: {error}", file=sys.stderr)
            return 2

        update_display = bool(
            artifacts.get("display-firmware")
            and artifacts.get("display-filesystem")
            and settings.get("displayUpdateAvailable")
        )
        update_controller = bool(
            artifacts.get("board-firmware") and settings.get("controllerUpdateAvailable")
        )

        print(
            f"\nOTA decision: display={update_display} controller={update_controller} "
            f"(device {settings.get('displayVersion')} → {settings.get('latestVersion')})"
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
