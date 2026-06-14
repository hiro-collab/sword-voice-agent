from __future__ import annotations

import argparse
import json
from pathlib import Path

from sword_voice_agent.adapters.no_provider_child_provenance import (
    build_no_provider_child_provenance_diagnostics,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build summary-only Launch Manager / Thought Core child provenance "
            "diagnostics without executing a Thought Core turn."
        )
    )
    parser.add_argument(
        "--agent-os-root",
        default=".",
        help="Path to the sword-agent-os repository root.",
    )
    parser.add_argument(
        "--selected-profile",
        default="thought-core-v0",
        help="Launch Manager selected profile label to inspect.",
    )
    parser.add_argument(
        "--simulate-runner-no-provider",
        action="store_true",
        help=(
            "Set launcher-side process env classes to no-provider before applying "
            "service .env import precedence."
        ),
    )
    parser.add_argument(
        "--force-no-provider",
        action="store_true",
        help=(
            "Set the explicit Thought Core force no-provider class used after "
            "service .env import."
        ),
    )
    parser.add_argument(
        "--top-level-text-present-class",
        default="",
        help="Summary class only; do not pass raw text.",
    )
    parser.add_argument(
        "--payload-marker-class",
        default="",
        help="Summary marker class only; do not pass raw text.",
    )
    parser.add_argument(
        "--context-ref-payload-class",
        default="",
        help="Summary class from context_refs; evidence label only.",
    )
    parser.add_argument(
        "--listener-class",
        action="append",
        default=[],
        metavar="NAME=CLASS",
        help="Optional selected listener class, for example thought_core_api=none.",
    )
    parser.add_argument(
        "--output-json",
        default="",
        help="Write diagnostics JSON to this path. Prints JSON if omitted.",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    process_env = {}
    if args.simulate_runner_no_provider:
        process_env = {
            "THOUGHT_CORE_LLM_ENABLED": "0",
            "THOUGHT_CORE_ACTION_LLM_ENABLED": "0",
        }
    if args.force_no_provider:
        process_env["THOUGHT_CORE_FORCE_NO_PROVIDER"] = "1"
    payload = build_no_provider_child_provenance_diagnostics(
        agent_os_root=Path(args.agent_os_root),
        selected_profile=args.selected_profile,
        process_env=process_env,
        listener_classes=parse_listener_classes(args.listener_class),
        payload_marker_class=args.payload_marker_class,
        top_level_text_present_class=args.top_level_text_present_class,
        context_ref_payload_class=args.context_ref_payload_class,
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote diagnostics JSON: {output}")
    else:
        print(text)
    return 0


def parse_listener_classes(items: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator:
            raise SystemExit(f"invalid --listener-class value: {item}")
        result[name.strip()] = value.strip()
    return result


def main(argv: list[str] | None = None) -> int:
    return run(build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
