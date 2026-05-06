from __future__ import annotations

import json
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "dify-apps"
    / "Home Control Assistant.issue-iteration.yml"
)


def extract_code_block(title: str) -> str:
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    title_index = next(index for index, line in enumerate(lines) if f"title: {title}" in line)
    code_index = title_index
    while code_index < len(lines) and "code: |" not in lines[code_index]:
        code_index += 1
    assert code_index < len(lines), f"code block not found for {title}"

    content_indent = len(lines[code_index]) - len(lines[code_index].lstrip(" ")) + 2
    prefix = " " * content_indent
    code_lines: list[str] = []
    for line in lines[code_index + 1 :]:
        if line == "":
            code_lines.append("")
            continue
        if not line.startswith(prefix):
            break
        code_lines.append(line[content_indent:])
    return "\n".join(code_lines)


def load_main(title: str):
    namespace: dict[str, object] = {}
    exec(extract_code_block(title), namespace)
    return namespace["main"]


def test_confirmation_required_echo_is_replaced_with_confirmation_prompt() -> None:
    result = {
        "issue_id": "hca-test",
        "attempt": 1,
        "status": "confirmation_required",
        "bridge_status": "confirmation_required",
        "action_id": "door_open",
        "label": "中扉を開ける",
    }
    weak_decision = {
        "issue_id": "hca-test",
        "attempt": 1,
        "user_reply": "[neutral]中扉を開けてほしいんだな。",
        "decision": "finish",
        "issue_status": "closed",
        "action_id": "door_open",
    }

    for title in ("判定JSON整形 1", "判定JSON整形 2", "判定JSON整形 3"):
        main = load_main(title)
        output = main(json.dumps(weak_decision, ensure_ascii=False), json.dumps(result, ensure_ascii=False), "token")

        assert output["decision"] == "ask_user"
        assert output["issue_status"] == "need_user"
        assert "お願い" in output["user_reply"]
        assert "OK" in output["user_reply"]
        assert "ほしいんだな" not in output["user_reply"]


def test_success_echo_is_replaced_with_bridge_message() -> None:
    result = {
        "issue_id": "hca-test",
        "attempt": 1,
        "status": "success",
        "bridge_status": "submitted",
        "action_id": "door_open",
        "label": "中扉を開ける",
        "bridge_message": "中扉を開けました。",
    }
    weak_decision = {
        "issue_id": "hca-test",
        "attempt": 1,
        "user_reply": "[neutral]中扉を開けてほしいんだな。",
        "decision": "ask_user",
        "issue_status": "need_user",
        "action_id": "door_open",
    }

    for title in ("判定JSON整形 1", "判定JSON整形 2", "判定JSON整形 3"):
        main = load_main(title)
        output = main(json.dumps(weak_decision, ensure_ascii=False), json.dumps(result, ensure_ascii=False), "")

        assert output["decision"] == "finish"
        assert output["issue_status"] == "resolved"
        assert output["user_reply"] == "[happy]中扉を開けました。"
