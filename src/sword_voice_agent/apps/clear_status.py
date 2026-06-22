from __future__ import annotations

import argparse

from sword_voice_agent.adapters.status_store import StatusStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Clear local Sword Voice Agent status snapshots and event log."
    )
    parser.add_argument(
        "--status-dir",
        default=".cache/sword_voice_agent",
        help="Directory containing latest status snapshots and events.jsonl.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm deletion of the local status files.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    store = StatusStore(args.status_dir)
    targets = [
        store.latest_gesture_path,
        store.latest_voice_turn_path,
        store.latest_thought_core_response_path,
        store.events_path,
    ]
    if not args.yes:
        print("Refusing to clear status files without --yes.")
        for path in targets:
            print(path)
        return 2

    store.clear()
    print(f"cleared local status files under {store.root}")
    return 0


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
