#!/usr/bin/env python3
"""
Back up device profiles and shots (default), release firmware, OTA-update, and verify.

Default: backup → release → OTA with flash-built from out/ and version check.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from device_http import device_web_urls
from gaggimate_ota import (
    apply_ota_scope,
    clear_ota_if_at_target,
    format_ota_verify_ok_message,
    normalize_version,
    ota_flash_plan,
    ota_verify_requirements,
    resolve_ota_scope,
    run_ota_sequence,
    wait_for_device_versions,
    wait_for_github_latest,
)
from gaggimate_ws import DEFAULT_HOST, GaggimateWsClient, parse_host

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "out" / "release-manifest.json"
GITHUB_RELEASES = "https://github.com/phamhanh/gaggimate/releases"


def print_device_links(host: str, port: int) -> None:
    ip, home_url, ota_url = device_web_urls(host, port)
    print(f"Device IP:        {ip}")
    print(f"Web UI:           {home_url}")
    print(f"System & Updates: {ota_url}")


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


def run_script(
    script: str,
    args: list[str],
    *,
    dry_run: bool,
    extra_env: dict[str, str] | None = None,
) -> None:
    command = [str(ROOT / "scripts" / script), *args]
    print(f"\n→ {' '.join(command)}")
    if dry_run:
        return
    env = {**os.environ, **(extra_env or {})}
    subprocess.run(command, cwd=ROOT, check=True, env=env)


def load_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        raise FileNotFoundError(
            f"Missing {MANIFEST_PATH.relative_to(ROOT)} — run release or build first"
        )
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Back up device data (default), release firmware, and OTA-update the machine."
        ),
        epilog=(
            "Examples:\n"
            "  ./scripts/deploy.sh\n"
            "  ./scripts/deploy.sh --dry-run\n"
            "  ./scripts/deploy.sh -- --patch --yes\n"
            "  ./scripts/deploy.sh --no-backup\n"
            "  ./scripts/deploy.sh --release-only\n"
            "  ./scripts/deploy.sh --update-only\n"
            "  ./scripts/deploy.sh --update-only --ota-display-only\n"
            "  ./scripts/deploy.sh --ota-controller-only -- --yes\n"
            "  ./scripts/deploy.sh --no-backup --release-only -- --build-only"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=os.environ.get("GAGGIMATE_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--release-only",
        action="store_true",
        help="Back up + release; skip OTA (default includes backup unless --no-backup)",
    )
    parser.add_argument("--update-only", action="store_true", help="OTA only (use existing out/)")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip device backup (faster; uses existing data/p, data/h or seed)",
    )
    parser.add_argument(
        "--ota-flash-built",
        action="store_true",
        help="Flash every component in out/ (default after release or --update-only)",
    )
    parser.add_argument(
        "--ota-respect-device",
        action="store_true",
        help="Only OTA when device reports *UpdateAvailable (not recommended after release)",
    )
    ota_scope = parser.add_mutually_exclusive_group()
    ota_scope.add_argument(
        "--ota-display-only",
        action="store_true",
        help="OTA display only (skip controller); requires display bins in out/",
    )
    ota_scope.add_argument(
        "--ota-controller-only",
        action="store_true",
        help="OTA controller only (skip display); requires board-firmware in out/",
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

    if not args.dry_run and not args.update_only:
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
        if args.ota_display_only:
            ota_step = "OTA display only from out/ + verify display version"
        elif args.ota_controller_only:
            ota_step = "OTA controller only from out/ + verify controller version"
        else:
            ota_step = "OTA from out/ + verify versions on device"
        plan_lines.append(f"{step}. {ota_step}")

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
        run_script(
            "release.sh",
            release_args,
            dry_run=args.dry_run,
            extra_env={"GAGGIMATE_FROM_DEPLOY": "1"},
        )

    if not do_ota:
        return 0

    if MANIFEST_PATH.is_file():
        manifest = load_manifest()
    elif args.dry_run:
        manifest = {
            "version": "(unknown — run release first)",
            "artifacts": {
                "display-firmware": True,
                "display-filesystem": True,
                "board-firmware": True,
            },
        }
    else:
        print(f"Error: missing {MANIFEST_PATH.relative_to(ROOT)}", file=sys.stderr)
        return 1
    artifacts = manifest.get("artifacts", {})
    target_version = normalize_version(str(manifest.get("version") or ""))
    if target_version:
        print(f"\nTarget version:   {target_version}")
        print(f"GitHub release:   {GITHUB_RELEASES}/tag/{target_version}")

    _, _, ota_url = device_web_urls(host, port)
    print(f"Verify in browser: {ota_url}\n")

    settings_before: dict = {}
    try:
        with GaggimateWsClient(host, port, timeout=args.timeout_connect) as client:
            settings_before = client.fetch_ota_settings(refresh=not args.dry_run)
            if target_version and not args.dry_run:
                print("Waiting for device to see new GitHub release...")
                settings_before = wait_for_github_latest(
                    client,
                    target_version,
                    timeout=args.timeout,
                )
    except (TimeoutError, ConnectionError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    if settings_before:
        print(
            f"Device before OTA: display={settings_before.get('displayVersion')!r} "
            f"controller={settings_before.get('controllerVersion')!r}"
        )

    try:
        ota_scope = resolve_ota_scope(args.ota_display_only, args.ota_controller_only)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    flash_built = args.ota_flash_built or (not args.ota_respect_device)
    update_display, update_controller, explanation = ota_flash_plan(
        settings_before,
        artifacts,
        flash_built=flash_built,
    )
    try:
        update_display, update_controller = apply_ota_scope(
            update_display,
            update_controller,
            artifacts,
            ota_scope,
        )
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if target_version and settings_before:
        update_display, update_controller = clear_ota_if_at_target(
            update_display,
            update_controller,
            settings_before,
            target_version,
        )

    print("\n".join(explanation))
    if ota_scope:
        print(f"OTA scope: {ota_scope} only")
    print(f"\nOTA decision: display={update_display} controller={update_controller}")

    if not update_display and not update_controller:
        print("\nOTA: nothing to do — selected component(s) already on target.")
        return 0

    require_display, require_controller = ota_verify_requirements(
        update_display,
        update_controller,
        ota_scope,
    )

    try:
        run_ota_sequence(
            host,
            port,
            update_display=update_display,
            update_controller=update_controller,
            timeout=args.timeout,
            connect_timeout=args.timeout_connect,
            dry_run=args.dry_run,
            client_cls=GaggimateWsClient,
        )
    except TimeoutError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.dry_run or not target_version:
        return 0

    if not require_display and not require_controller:
        return 0

    try:
        with GaggimateWsClient(host, port, timeout=args.timeout_connect) as client:
            print("\nWaiting for device OTA versions to match release...")
            settings_after = wait_for_device_versions(
                client,
                target_version,
                timeout=args.timeout,
                require_display=require_display,
                require_controller=require_controller,
            )
    except (TimeoutError, ConnectionError, OSError) as error:
        print(f"Error: post-OTA verify failed to connect: {error}", file=sys.stderr)
        return 1
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        print(
            "Retry: ./scripts/deploy.sh --update-only  or  ./scripts/update-device.sh",
            file=sys.stderr,
        )
        return 1

    print(
        f"\nDevice after OTA:  display={settings_after.get('displayVersion')!r} "
        f"controller={settings_after.get('controllerVersion')!r}"
    )

    print(
        "\n"
        + format_ota_verify_ok_message(
            target_version,
            require_display=require_display,
            require_controller=require_controller,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
