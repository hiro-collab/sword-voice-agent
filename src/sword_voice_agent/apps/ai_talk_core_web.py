"""Run ai_talk_core Web UI with sword-voice-agent integration defaults."""

from __future__ import annotations

import argparse
import importlib
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from sword_voice_agent.apps.send_handoff_to_dify import validate_path_argument


@dataclass(frozen=True)
class AiTalkCoreWebDefaults:
    """Browser UI defaults applied only by this integration launcher."""

    record_gate_auto: bool = True
    save_handoff: bool = True


CHECKBOX_IDS = {
    "record_gate_auto": "record_gate_auto",
    "record_save_handoff": "save_handoff",
    "upload_save_handoff": "save_handoff",
}
AI_TALK_CORE_WEB_PRESET_ENV = "AI_TALK_CORE_WEB_PRESET"
NATIVE_PROFILE_NAMES = ("integration", "dify")


def apply_ai_talk_core_web_defaults(
    html: str,
    defaults: AiTalkCoreWebDefaults,
) -> str:
    """Apply integration-only checkbox defaults to ai_talk_core's HTML."""
    values = {
        "record_gate_auto": defaults.record_gate_auto,
        "save_handoff": defaults.save_handoff,
    }
    updated = html
    for element_id, setting_name in CHECKBOX_IDS.items():
        updated = set_checkbox_checked(
            updated,
            element_id=element_id,
            checked=values[setting_name],
        )
    return updated


def detect_native_startup_profile(ai_talk_core_root: Path) -> str | None:
    """Detect ai_talk_core's native Web UI startup profile, if available."""
    app_js = ai_talk_core_root / "src" / "web" / "static" / "app.js"
    try:
        text = app_js.read_text(encoding="utf-8")
    except OSError:
        return None
    for profile_name in NATIVE_PROFILE_NAMES:
        if re.search(rf"\b{re.escape(profile_name)}\s*:", text):
            return profile_name
    return None


def should_use_native_profile(defaults: AiTalkCoreWebDefaults) -> bool:
    """Return whether native ai_talk_core startup defaults should be applied."""
    return defaults.record_gate_auto or defaults.save_handoff


def build_native_startup_query(
    profile_name: str,
    defaults: AiTalkCoreWebDefaults,
) -> str:
    """Build query params for native ai_talk_core startup defaults."""
    params: dict[str, str] = {"profile": profile_name}
    if not defaults.record_gate_auto:
        params["record_gate_auto"] = "0"
    if not defaults.save_handoff:
        params["record_save_handoff"] = "0"
        params["upload_save_handoff"] = "0"
    return urlencode(params)


def set_checkbox_checked(html: str, element_id: str, checked: bool) -> str:
    """Set or clear a checkbox's checked attribute by id."""
    pattern = re.compile(
        r"(<input\b(?=[^>]*\bid=[\"']"
        + re.escape(element_id)
        + r"[\"'])[^>]*)(>)",
        flags=re.IGNORECASE,
    )

    def replace(match: re.Match[str]) -> str:
        tag = match.group(1)
        closing = match.group(2)
        if checked:
            if re.search(r"\schecked(?:\s*=\s*[\"'][^\"']*[\"'])?", tag, flags=re.IGNORECASE):
                return tag + closing
            return tag + " checked" + closing
        return re.sub(
            r"\schecked(?:\s*=\s*[\"'][^\"']*[\"'])?",
            "",
            tag,
            flags=re.IGNORECASE,
        ) + closing

    return pattern.sub(replace, html)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run ai_talk_core Web UI with sword-voice-agent defaults."
    )
    parser.add_argument(
        "--ai-talk-core-root",
        required=True,
        help="Path to the ai_talk_core repository root.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)

    gate_group = parser.add_mutually_exclusive_group()
    gate_group.add_argument(
        "--record-gate-auto",
        dest="record_gate_auto",
        action="store_true",
        default=True,
        help="Check '入力ゲートで録音を制御する' by default.",
    )
    gate_group.add_argument(
        "--no-record-gate-auto",
        dest="record_gate_auto",
        action="store_false",
        help="Leave '入力ゲートで録音を制御する' unchecked.",
    )

    handoff_group = parser.add_mutually_exclusive_group()
    handoff_group.add_argument(
        "--save-handoff",
        dest="save_handoff",
        action="store_true",
        default=True,
        help="Check handoff save options by default.",
    )
    handoff_group.add_argument(
        "--no-save-handoff",
        dest="save_handoff",
        action="store_false",
        help="Leave handoff save options unchecked.",
    )
    return parser


def load_ai_talk_core_app(ai_talk_core_root: Path) -> Any:
    """Import ai_talk_core's Flask app factory from a repository checkout."""
    app_py = ai_talk_core_root / "src" / "web" / "app.py"
    if not app_py.exists():
        raise RuntimeError(f"ai_talk_core web app not found: {app_py}")

    root_text = str(ai_talk_core_root)
    if root_text not in sys.path:
        sys.path.insert(0, root_text)
    module = importlib.import_module("src.web.app")
    return module.create_app()


def install_default_injection(app: Any, defaults: AiTalkCoreWebDefaults) -> None:
    """Install a response hook that adjusts only the initial browser page."""
    from flask import request

    @app.after_request
    def apply_defaults(response: Any) -> Any:
        if request.method != "GET" or request.path != "/":
            return response
        content_type = response.headers.get("Content-Type", "")
        if "text/html" not in content_type:
            return response
        html = response.get_data(as_text=True)
        response.set_data(apply_ai_talk_core_web_defaults(html, defaults))
        return response


def install_native_startup_redirect(
    app: Any,
    profile_name: str,
    defaults: AiTalkCoreWebDefaults,
) -> None:
    """Redirect the initial page to ai_talk_core's native startup query."""
    from flask import redirect, request

    query = build_native_startup_query(profile_name, defaults)

    @app.before_request
    def apply_native_startup_query() -> Any:
        if request.method != "GET" or request.path != "/" or request.query_string:
            return None
        return redirect(f"/?{query}", code=302)


def load_ai_talk_core_app_with_optional_preset(
    ai_talk_core_root: Path,
    profile_name: str | None,
) -> Any:
    """Load ai_talk_core while setting its native preset env when requested."""
    previous = os.environ.get(AI_TALK_CORE_WEB_PRESET_ENV)
    try:
        if profile_name:
            os.environ[AI_TALK_CORE_WEB_PRESET_ENV] = profile_name
        else:
            os.environ.pop(AI_TALK_CORE_WEB_PRESET_ENV, None)
        return load_ai_talk_core_app(ai_talk_core_root)
    finally:
        if previous is None:
            os.environ.pop(AI_TALK_CORE_WEB_PRESET_ENV, None)
        else:
            os.environ[AI_TALK_CORE_WEB_PRESET_ENV] = previous


def run(args: argparse.Namespace) -> int:
    validate_path_argument(args.ai_talk_core_root, "--ai-talk-core-root")
    ai_talk_core_root = Path(args.ai_talk_core_root).resolve()
    defaults = AiTalkCoreWebDefaults(
        record_gate_auto=args.record_gate_auto,
        save_handoff=args.save_handoff,
    )
    native_profile = detect_native_startup_profile(ai_talk_core_root)
    native_defaults_enabled = bool(
        native_profile and should_use_native_profile(defaults)
    )
    native_env_profile = native_profile if native_defaults_enabled else None
    app = load_ai_talk_core_app_with_optional_preset(
        ai_talk_core_root,
        native_env_profile,
    )
    if native_defaults_enabled and defaults != AiTalkCoreWebDefaults():
        install_native_startup_redirect(app, native_profile, defaults)
    elif native_profile is None:
        install_default_injection(app, defaults)
    app.run(host=args.host, port=args.port, debug=False, use_reloader=False)
    return 0


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
