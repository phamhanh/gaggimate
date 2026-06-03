#!/usr/bin/env python3
"""OTA display + controller to latest (or pinned) GitHub release — no build."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from device_http import device_web_urls
from gaggimate_ota import (
    format_ota_verify_ok_message,
    normalize_version,
    ota_verify_requirements,
    resolve_ota_scope,
    run_ota_sequence,
    versions_behind,
    wait_for_device_versions,
    wait_for_github_latest,
)
from gaggimate_ws import DEFAULT_HOST, GaggimateWsClient, parse_host

ROOT = Path(__file__).resolve().parent.parent
GITHUB_RELEASES = "https://github.com/phamhanh/gaggimate/releases"
SEMVER_TAG = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")


def latest_git_tag() -> str:
    subprocess.run(
        ["git", "fetch", "origin", "--tags"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    result = subprocess.run(
        ["git", "tag", "-l", "v*"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tags = [
        line.strip()
        for line in result.stdout.splitlines()
        if SEMVER_TAG.match(line.strip())
    ]
    if not tags:
        raise RuntimeError("No semver tags vX.Y.Z found")
    return sorted(tags, key=lambda tag: [int(part) for part in tag.lstrip("v").split(".")])[-1]


def resolve_target_version(explicit: str | None) -> str:
    if explicit:
        version = normalize_version(explicit)
        if not SEMVER_TAG.match(version):
            raise ValueError(f"Invalid version {explicit!r} (expected vX.Y.Z)")
        return version
    return latest_git_tag()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OTA Gaggimate to latest GitHub release (no backup, build, or publish).",
        epilog=(
            "Examples:\n"
            "  ./scripts/update-device.sh --dry-run\n"
            "  ./scripts/update-device.sh\n"
            "  ./scripts/update-device.sh --version v1.9.5\n"
            "  ./scripts/update-device.sh --host 192.168.51.2\n"
            "  ./scripts/update-device.sh --display-only\n"
            "  ./scripts/update-device.sh --controller-only --dry-run"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", default=os.environ.get("GAGGIMATE_HOST", DEFAULT_HOST))
    parser.add_argument(
        "--version",
        metavar="vX.Y.Z",
        help="Pin release tag (default: latest semver tag on origin)",
    )
    ota_scope = parser.add_mutually_exclusive_group()
    ota_scope.add_argument(
        "--display-only",
        action="store_true",
        help="OTA display only (skip controller flash)",
    )
    ota_scope.add_argument(
        "--controller-only",
        action="store_true",
        help="OTA controller only (skip display flash)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show plan only")
    parser.add_argument("--timeout", type=float, default=600.0, help="OTA wait timeout (seconds)")
    parser.add_argument("--timeout-connect", type=float, default=15.0, help="WebSocket connect timeout")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    try:
        target = resolve_target_version(args.version)
    except (RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    host, port = parse_host(args.host)
    ip, home_url, ota_url = device_web_urls(host, port)
    print(f"Target version:   {target}")
    print(f"GitHub release:   {GITHUB_RELEASES}/tag/{target}")
    print(f"Device IP:        {ip}")
    print(f"Web UI:           {home_url}")
    print(f"System & Updates: {ota_url}")

    settings_before: dict = {}
    try:
        with GaggimateWsClient(host, port, timeout=args.timeout_connect) as client:
            settings_before = client.fetch_ota_settings(refresh=not args.dry_run)
            if not args.dry_run:
                print("\nWaiting for device to see target on GitHub...")
                settings_before = wait_for_github_latest(
                    client,
                    target,
                    timeout=args.timeout,
                )
    except (TimeoutError, ConnectionError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    try:
        ota_scope = resolve_ota_scope(args.display_only, args.controller_only)
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    need_display, need_controller = versions_behind(settings_before, target)
    if ota_scope == "display":
        need_controller = False
    elif ota_scope == "controller":
        need_display = False

    print(
        f"\nDevice now:       display={settings_before.get('displayVersion')!r} "
        f"controller={settings_before.get('controllerVersion')!r}"
    )
    if ota_scope:
        print(f"OTA scope:        {ota_scope} only")
    print(f"Will update:      display={'yes' if need_display else 'no'}  "
          f"controller={'yes' if need_controller else 'no'}")

    if not need_display and not need_controller:
        print(f"\nAlready on {target} — nothing to do.")
        return 0

    require_display, require_controller = ota_verify_requirements(
        need_display,
        need_controller,
        ota_scope,
    )

    try:
        run_ota_sequence(
            host,
            port,
            update_display=need_display,
            update_controller=need_controller,
            timeout=args.timeout,
            connect_timeout=args.timeout_connect,
            dry_run=args.dry_run,
            client_cls=GaggimateWsClient,
        )
    except TimeoutError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if args.dry_run:
        return 0

    try:
        with GaggimateWsClient(host, port, timeout=args.timeout_connect) as client:
            print("\nWaiting for device OTA versions to match release...")
            settings_after = wait_for_device_versions(
                client,
                target,
                timeout=args.timeout,
                require_display=require_display,
                require_controller=require_controller,
            )
    except (TimeoutError, ConnectionError, OSError) as error:
        print(f"Error: post-OTA verify failed to connect: {error}", file=sys.stderr)
        return 1
    except RuntimeError as error:
        print(f"Error: {error}", file=sys.stderr)
        print(f"Check {ota_url} and retry.", file=sys.stderr)
        return 1

    print(
        f"\nDevice after OTA:  display={settings_after.get('displayVersion')!r} "
        f"controller={settings_after.get('controllerVersion')!r}"
    )

    print(
        "\n"
        + format_ota_verify_ok_message(
            target,
            require_display=require_display,
            require_controller=require_controller,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
