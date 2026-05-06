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
    code_index = next(
        (
            index
            for index in range(title_index, min(len(lines), title_index + 120))
            if "code: |" in lines[index]
        ),
        None,
    )
    if code_index is None:
        candidates = [
            index
            for index in range(max(0, title_index - 700), title_index)
            if "code: |" in lines[index]
        ]
        assert candidates, f"code block not found for {title}"
        code_index = candidates[-1]

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
        assert "まだ実行して" in output["user_reply"]
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


def test_confirmation_required_permission_only_reply_is_replaced() -> None:
    result = {
        "issue_id": "hca-test",
        "attempt": 1,
        "status": "confirmation_required",
        "bridge_status": "confirmation_required",
        "action_id": "door_close",
        "label": "中扉を閉める",
    }
    weak_decision = {
        "issue_id": "hca-test",
        "attempt": 1,
        "user_reply": "[neutral]おっけい、お願い！",
        "decision": "finish",
        "issue_status": "closed",
        "action_id": "door_close",
    }

    for title in ("判定JSON整形 1", "判定JSON整形 2", "判定JSON整形 3"):
        main = load_main(title)
        output = main(json.dumps(weak_decision, ensure_ascii=False), json.dumps(result, ensure_ascii=False), "token")

        assert output["decision"] == "ask_user"
        assert output["issue_status"] == "need_user"
        assert "中扉を閉める" in output["user_reply"]
        assert "まだ実行して" in output["user_reply"]


def test_success_permission_or_execute_request_is_replaced_with_bridge_message() -> None:
    result = {
        "issue_id": "hca-test",
        "attempt": 1,
        "status": "success",
        "bridge_status": "submitted",
        "action_id": "door_open",
        "label": "中扉を開ける",
        "bridge_message": "中扉を開けました。",
    }
    weak_replies = [
        "[happy]はい、お願い！",
        "[happy]おっしゃ、実行してくれ！",
    ]

    for title in ("判定JSON整形 1", "判定JSON整形 2", "判定JSON整形 3"):
        main = load_main(title)
        for user_reply in weak_replies:
            weak_decision = {
                "issue_id": "hca-test",
                "attempt": 1,
                "user_reply": user_reply,
                "decision": "ask_user",
                "issue_status": "need_user",
                "action_id": "door_open",
            }
            output = main(json.dumps(weak_decision, ensure_ascii=False), json.dumps(result, ensure_ascii=False), "")

            assert output["decision"] == "finish"
            assert output["issue_status"] == "resolved"
            assert output["user_reply"] == "[happy]中扉を開けました。"


def test_door_action_text_is_inferred_before_issue_inheritance() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]はいよ。"}'

    for user_text, expected_action_id in (
        ("中扉を開けて", "door_open"),
        ("中扉を閉じて", "door_close"),
        ("中扉を閉じ", "door_close"),
        ("中扉を止めて", "door_stop"),
    ):
        output = main(raw, user_text)

        assert output["action_id"] == expected_action_id
        assert output["is_action"] is True
        assert output["pre_action_text"]
        assert "完了" not in output["pre_action_text"]

    output = main(raw, "中扉を閉じて", "HCA-old", "need_user", 1, "door_open", "", "token")

    assert output["action_id"] == "door_close"
    assert output["is_action"] is True
    assert output["inherited_issue"] is False
    assert output["issue_id"] != "HCA-old"
    assert output["confirmation_token"] == ""
    assert "中扉を閉める" in output["pre_action_text"]


def test_confirmation_reply_still_inherits_pending_issue() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    output = main(raw, "はい、実行して", "HCA-old", "need_user", 1, "door_open", "", "token")

    assert output["action_id"] == "door_open"
    assert output["confirmed"] is True
    assert output["inherited_issue"] is True
    assert output["issue_id"] == "HCA-old"
    assert output["confirmation_token"] == "token"
    assert output["pre_action_text"] == "[happy]OK、続きやるぞ。"


def test_pre_action_text_filters_completion_claims() -> None:
    main = load_main("action_id・issue_id整形")
    raw = json.dumps(
        {
            "action_id": "door_close",
            "confirmed": False,
            "ack_text": "[neutral]はいよ。",
            "pre_action_text": "[happy]中扉を閉めたぜ。",
        },
        ensure_ascii=False,
    )
    output = main(raw, "中扉を閉めて")

    assert output["action_id"] == "door_close"
    assert output["pre_action_text"] == "[neutral]中扉を閉めるか。わかった、やるよ。"


def test_environment_state_uses_home_control_token_and_default_port() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "http://host.docker.internal:8790/environment/current" in text
    assert "Authorization:Bearer {{#env.HOME_CONTROL_API_TOKEN#}}" in text
    assert "ENVIRONMENT_STATE_TOKEN" not in text
