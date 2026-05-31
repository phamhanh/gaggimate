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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gaggimate_ws import (
    DEFAULT_HOST,
    GaggimateWsClient,
    ProfileSummary,
    parse_host,
)


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
