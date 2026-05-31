#!/usr/bin/env python3
"""
Pull device SPIFFS data, run a local release, and OTA-update only needed components.
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


def run_script(script: str, args: list[str], *, dry_run: bool) -> None:
    command = [str(ROOT / "scripts" / script), *args]
    print(f"\n→ {' '.join(command)}")
    if dry_run:
        return
    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as error:
        if script == "release.sh":
            print(
                "\nRelease failed. If the tree is dirty only under data/p or data/h after pull, "
                "re-run deploy (it passes --allow-pulled-data). Otherwise commit or stash first.",
                file=sys.stderr,
            )
        raise SystemExit(error.returncode) from error


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        raise FileNotFoundError(f"Missing {MANIFEST_PATH.relative_to(ROOT)} — run release or build first")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def confirm_pull(profile_count: int, shot_count: int, host: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    reply = input(
        f"\nPull {profile_count} profile(s) and {shot_count} shot(s) from {host} "
        "and bake them into the next SPIFFS build? [y/N] "
    ).strip().lower()
    return reply in ("y", "yes")


def confirm_deploy(plan: str, *, assume_yes: bool, dry_run: bool) -> bool:
    print(plan)
    if dry_run or assume_yes:
        return True
    reply = input("\nProceed with deploy? [y/N] ").strip().lower()
    return reply in ("y", "yes")


def dry_run_counts(host: str, port: int, timeout: float, http_timeout: float) -> tuple[int, int]:
    from device_http import http_base_url
    from shot_index import fetch_active_shots

    history_url = f"{http_base_url(host, port)}/api/history"
    with GaggimateWsClient(host, port, timeout=timeout) as client:
        profiles = client.list_profiles()
        shots = fetch_active_shots(history_url, http_timeout)
        if shots is None:
            shots = client.list_history()
            shot_count = len(shots)
        else:
            shot_count = len(shots)
    return len(profiles), shot_count


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
        description="Pull SPIFFS data, release firmware, and OTA-update the device.",
    )
    parser.add_argument("--host", default=os.environ.get("GAGGIMATE_HOST", DEFAULT_HOST))
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("--release-only", action="store_true", help="Pull + release; skip OTA")
    parser.add_argument("--update-only", action="store_true", help="OTA only (use existing out/)")
    parser.add_argument("--no-pull", action="store_true", help="Skip device pull")
    parser.add_argument("--dry-run", action="store_true", help="Show plan only")
    parser.add_argument("--timeout", type=float, default=600.0, help="OTA wait timeout (seconds)")
    parser.add_argument("--timeout-connect", type=float, default=15.0, help="WebSocket connect timeout")
    parser.add_argument("--http-timeout", type=float, default=60.0, help="HTTP pull timeout (seconds)")
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

    host, port = parse_host(args.host)
    plan_lines = ["Deploy plan", "==========="]

    do_pull = not args.no_pull and not args.update_only
    do_release = not args.update_only
    do_ota = not args.release_only

    if do_pull:
        plan_lines.append(f"1. Pull profiles + shots from {host}")
    if do_release:
        plan_lines.append(f"2. Release ({' '.join(args.release_args) or 'default flags'})")
    if do_ota:
        plan_lines.append("3. Intelligent OTA")

    if not confirm_deploy("\n".join(plan_lines), assume_yes=args.yes, dry_run=args.dry_run):
        print("Aborted.")
        return 1

    if do_pull:
        if args.dry_run:
            try:
                profile_count, shot_count = dry_run_counts(
                    host, port, args.timeout_connect, args.http_timeout
                )
            except (TimeoutError, ConnectionError, OSError) as error:
                print(f"Error: {error}", file=sys.stderr)
                return 2
            print(f"\nPull preview: {profile_count} profile(s), {shot_count} shot(s)")
            if not confirm_pull(profile_count, shot_count, host, assume_yes=args.yes):
                print("Aborted.")
                return 1
        else:
            try:
                profile_count, shot_count = dry_run_counts(
                    host, port, args.timeout_connect, args.http_timeout
                )
            except (TimeoutError, ConnectionError, OSError) as error:
                print(f"Error: {error}", file=sys.stderr)
                return 2
            if not confirm_pull(profile_count, shot_count, host, assume_yes=args.yes):
                print("Aborted.")
                return 1

        pull_args = ["--host", host, "--http-timeout", str(args.http_timeout)]
        if args.dry_run:
            pull_args.append("--dry-run")
        run_script("pull-spiffs-data.sh", pull_args, dry_run=args.dry_run)

    if do_release:
        release_args = list(args.release_args)
        if args.yes and "--yes" not in release_args:
            release_args = ["--yes", *release_args]
        if args.dry_run and "--dry-run" not in release_args:
            release_args = ["--dry-run", *release_args]
        if do_pull and "--allow-pulled-data" not in release_args:
            release_args = ["--allow-pulled-data", *release_args]
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
