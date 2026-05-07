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
            for index in range(max(0, title_index - 1400), title_index)
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


def test_action_parse_normalizes_bare_motion_tags() -> None:
    main = load_main("action_id・issue_id整形")
    raw = json.dumps(
        {
            "action_id": "door_open",
            "confirmed": False,
            "ack_text": "[neutral][bow]はいよ。",
            "pre_action_text": "[neutral][Bow]中扉を開けるか。やるよ。",
        },
        ensure_ascii=False,
    )
    output = main(raw, "中扉を開けて")

    assert output["ack_text"] == "[neutral][motion:bow]はいよ。"
    assert output["pre_action_text"] == "[neutral][motion:bow]中扉を開けるか。やるよ。"


def test_decision_parse_normalizes_bare_motion_tags() -> None:
    result = {
        "issue_id": "hca-test",
        "attempt": 1,
        "status": "success",
        "bridge_status": "submitted",
        "action_id": "door_open",
        "label": "中扉を開ける",
    }
    weak_decision = {
        "issue_id": "hca-test",
        "attempt": 1,
        "user_reply": "[happy][Bow]中扉、操作は受け付けたぜ。",
        "decision": "finish",
        "issue_status": "resolved",
        "action_id": "door_open",
    }

    for title in ("判定JSON整形 1", "判定JSON整形 2", "判定JSON整形 3"):
        main = load_main(title)
        output = main(json.dumps(weak_decision, ensure_ascii=False), json.dumps(result, ensure_ascii=False), "")

        assert output["user_reply"] == "[happy][motion:bow]中扉、操作は受け付けたぜ。"


def test_environment_action_registry_aliases_drive_classification() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    environment = {
        "snapshot_id": "env-test-1",
        "capabilities": {"actions": True, "relations": True},
        "actions": [
            {
                "action_id": "vacuum_return",
                "aliases": ["掃除機を戻して", "掃除機帰って"],
                "label": "掃除機を戻す",
                "target_label": "掃除機",
                "verb": "戻す",
                "pre_action_phrase": "掃除機を戻す",
                "available": True,
                "noop": False,
                "reason": "ready",
            }
        ],
    }

    output = main(raw, "掃除機を戻して", "", "", 0, "", "", "", json.dumps(environment, ensure_ascii=False))

    assert output["action_id"] == "vacuum_return"
    assert output["is_action"] is True
    assert output["environment_snapshot_id"] == "env-test-1"
    assert "掃除機を戻す" in output["pre_action_text"]
    summary = json.loads(output["issue_summary"])
    assert summary["environment_snapshot_id"] == "env-test-1"
    assert summary["should_execute"] is True


def test_environment_noop_action_skips_home_assistant_execution() -> None:
    main = load_main("action_id・issue_id整形")
    raw = json.dumps(
        {
            "action_id": "light_off",
            "confirmed": False,
            "ack_text": "[neutral]はいよ。",
            "pre_action_text": "[neutral]電気を消すか。任せとけ。",
        },
        ensure_ascii=False,
    )
    environment = {
        "snapshot_id": "env-test-2",
        "capabilities": {"actions": True, "relations": True},
        "actions": [
            {
                "action_id": "light_off",
                "aliases": ["電気を消して"],
                "label": "ライトを消す",
                "target_label": "電気",
                "verb": "消す",
                "pre_action_phrase": "電気を消す",
                "available": False,
                "noop": True,
                "reason": "already_off",
                "reason_text": "電気はすでに消えています",
            }
        ],
    }

    output = main(raw, "電気を消して", "", "", 0, "", "", "", json.dumps(environment, ensure_ascii=False))

    assert output["action_id"] == "light_off"
    assert output["is_action"] is False
    assert output["pre_action_text"] == ""
    assert "電気はもう消えてる" in output["non_execute_reply"]
    summary = json.loads(output["issue_summary"])
    assert summary["should_execute"] is False
    assert summary["action_noop"] is True
    assert summary["action_reason"] == "already_off"


def test_room_light_state_query_uses_environment_projection_without_action() -> None:
    main = load_main("action_id・issue_id整形")
    raw = json.dumps(
        {
            "action_id": "light_on",
            "confirmed": False,
            "ack_text": "[neutral]はいよ。",
            "pre_action_text": "[neutral]ライトをつけるか。やるよ。",
        },
        ensure_ascii=False,
    )
    environment = {
        "snapshot_id": "env-room-light-on",
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "on",
                "confidence_label": "high",
                "authority": "vision_snapshot_processor",
                "projected_by": "environment_state_server",
                "answer_hint": "照明が点いている可能性が高い。",
                "evidence": {
                    "lighting_type": "electric",
                    "electric_on_probability": 0.91,
                    "topic": "/vision/room_light/state",
                },
            }
        },
    }

    output = main(raw, "電気ついてる？", "", "", 0, "", "", "", json.dumps(environment, ensure_ascii=False))

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert output["confirmed"] is False
    assert output["pre_action_text"] == ""
    assert "映像推定" in output["non_execute_reply"]
    assert "ついてる可能性" in output["non_execute_reply"]
    assert output["state_query_id"] == "room_light"
    summary = json.loads(output["issue_summary"])
    assert summary["should_execute"] is False
    assert summary["state_query_id"] == "room_light"


def test_room_light_state_query_unknown_uses_answer_hint() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"light_off","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    environment = {
        "snapshot_id": "env-room-light-unknown",
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "unknown",
                "confidence_label": "low",
                "authority": "vision_snapshot_processor",
                "projected_by": "environment_state_server",
                "answer_hint": "日光の影響が強そうだ。",
                "evidence": {
                    "lighting_type": "daylight",
                    "daylight_present_probability": 0.75,
                    "electric_on_probability": 0.58,
                },
            }
        },
    }

    output = main(raw, "照明が消えてるか見える？", "", "", 0, "", "", "", json.dumps(environment, ensure_ascii=False))

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert "断定しづらい" in output["non_execute_reply"]
    assert "日光の影響が強そう" in output["non_execute_reply"]
    assert "実際はついてる" in output["non_execute_reply"]
    assert output["pending_state_query_id"] == "room_light"
    pending = json.loads(output["pending_state_query_json"])
    assert pending["state_query_id"] == "room_light"
    assert pending["snapshot_id"] == "env-room-light-unknown"
    assert pending["predicted_state"] == "unknown"
    assert pending["created_at"]
    assert "observed_at" in pending
    assert "updated_at" in pending


def test_room_light_user_feedback_uses_pending_state_query_without_action() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]ふんふん。"}'
    environment = {
        "snapshot_id": "env-room-light-feedback-current",
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "unknown",
                "confidence_label": "low",
                "authority": "vision_snapshot_processor",
                "projected_by": "environment_state_server",
                "answer_hint": "映像推定では断定できない。",
            }
        },
    }
    pending = {
        "type": "state_query_pending",
        "state_query_id": "room_light",
        "target": "room_light",
        "snapshot_id": "env-room-light-feedback-original",
        "authority": "vision_snapshot_processor",
        "projected_by": "environment_state_server",
        "predicted_state": "unknown",
        "confidence_label": "low",
        "answer_hint": "日光の影響が強そうだ。",
        "user_query": "電気ついてる？",
    }

    output = main(
        raw,
        "ついてるよ",
        "",
        "",
        0,
        "",
        "",
        "",
        json.dumps(environment, ensure_ascii=False),
        200,
        "room_light",
        json.dumps(pending, ensure_ascii=False),
        "conv-feedback-1",
    )

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert output["is_state_feedback"] is True
    assert output["pending_state_query_id"] == ""
    assert output["pending_state_query_json"] == ""
    assert "実際はついてる" in output["non_execute_reply"]
    feedback = json.loads(output["state_query_feedback_json"])
    assert feedback["type"] == "state_query_feedback"
    assert feedback["target"] == "room_light"
    assert feedback["snapshot_id"] == "env-room-light-feedback-original"
    assert feedback["current_snapshot_id"] == "env-room-light-feedback-current"
    assert feedback["predicted_state"] == "unknown"
    assert feedback["user_label"] == "on"
    assert feedback["authority"] == "user_feedback"
    assert feedback["feedback_reason"] == "user_correction_after_state_query"
    assert feedback["idempotency_key"] == "state-query-feedback:conv-feedback-1:env-room-light-feedback-original:on"


def test_room_light_user_feedback_accepts_short_yes_no_answers() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]ふんふん。"}'
    environment = {"snapshot_id": "env-current", "state_queries": {"room_light": {"state": "unknown"}}}
    pending_unknown = {
        "type": "state_query_pending",
        "state_query_id": "room_light",
        "snapshot_id": "env-original",
        "predicted_state": "unknown",
    }
    pending_on = {
        "type": "state_query_pending",
        "state_query_id": "room_light",
        "snapshot_id": "env-original-on",
        "predicted_state": "on",
    }

    yes_output = main(
        raw,
        "うん",
        "",
        "",
        0,
        "",
        "",
        "",
        json.dumps(environment, ensure_ascii=False),
        200,
        "room_light",
        json.dumps(pending_unknown, ensure_ascii=False),
    )
    no_output = main(
        raw,
        "違う",
        "",
        "",
        0,
        "",
        "",
        "",
        json.dumps(environment, ensure_ascii=False),
        200,
        "room_light",
        json.dumps(pending_on, ensure_ascii=False),
    )

    assert json.loads(yes_output["state_query_feedback_json"])["user_label"] == "on"
    assert json.loads(no_output["state_query_feedback_json"])["user_label"] == "off"


def test_stale_room_light_user_feedback_is_not_sent() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]ふんふん。"}'
    environment = {"snapshot_id": "env-current", "state_queries": {"room_light": {"state": "unknown"}}}
    pending = {
        "type": "state_query_pending",
        "state_query_id": "room_light",
        "snapshot_id": "env-old",
        "predicted_state": "unknown",
        "created_at": "2000-01-01T00:00:00+00:00",
    }

    output = main(
        raw,
        "ついてるよ",
        "",
        "",
        0,
        "",
        "",
        "",
        json.dumps(environment, ensure_ascii=False),
        200,
        "room_light",
        json.dumps(pending, ensure_ascii=False),
    )

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert output["is_state_feedback"] is False
    assert output["is_state_feedback_expired"] is True
    assert output["state_query_feedback_json"] == ""
    assert output["pending_state_query_id"] == ""
    assert output["pending_state_query_json"] == ""
    assert "もう一回" in output["non_execute_reply"]


def test_pending_room_light_feedback_does_not_swallow_light_command() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    environment = {
        "snapshot_id": "env-room-light-command-after-feedback",
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "unknown",
                "authority": "vision_snapshot_processor",
            }
        },
        "actions": [
            {
                "action_id": "light_off",
                "aliases": ["電気を消して"],
                "label": "ライトを消す",
                "target_label": "電気",
                "verb": "消す",
                "pre_action_phrase": "電気を消す",
                "available": True,
                "noop": False,
            }
        ],
    }
    pending = {
        "type": "state_query_pending",
        "state_query_id": "room_light",
        "snapshot_id": "env-room-light-feedback-original",
        "predicted_state": "unknown",
    }

    output = main(
        raw,
        "電気を消して",
        "",
        "",
        0,
        "",
        "",
        "",
        json.dumps(environment, ensure_ascii=False),
        200,
        "room_light",
        json.dumps(pending, ensure_ascii=False),
    )

    assert output["action_id"] == "light_off"
    assert output["is_action"] is True
    assert output["is_state_feedback"] is False
    assert output["pending_state_query_id"] == ""
    assert output["pending_state_query_json"] == ""


def test_room_light_state_query_reads_wrapped_environment_body() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"light_on","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    environment = {
        "snapshot_id": "env-room-light-wrapped",
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "unknown",
                "confidence_label": "low",
                "authority": "vision_snapshot_processor",
                "projected_by": "environment_state_server",
                "answer_hint": "映像推定では断定できない。",
            }
        },
    }
    wrapped_environment = json.dumps({"environment": environment}, ensure_ascii=False)
    double_encoded = json.dumps(wrapped_environment, ensure_ascii=False)

    output = main(raw, "電気はついてますか？", "", "", 0, "", "", "", double_encoded)

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert "映像推定" in output["non_execute_reply"]
    assert "映像推定では断定できない" in output["non_execute_reply"]
    assert output["state_query_json"] != "{}"
    summary = json.loads(output["issue_summary"])
    assert summary["environment_snapshot_id"] == "env-room-light-wrapped"
    assert summary["state_query_present"] is True
    assert summary["state_query_keys"] == ["room_light"]


def test_diagnostic_query_reports_workflow_version_and_environment_state() -> None:
    main = load_main("action_id・issue_id整形")
    environment = {
        "snapshot_id": "env-diagnostic",
        "available": True,
        "stale": False,
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "unknown",
                "confidence_label": "low",
                "authority": "vision_snapshot_processor",
                "projected_by": "environment_state_server",
                "answer_hint": "映像推定では断定できない。",
            }
        },
    }

    output = main(
        '{"action_id":"light_on","confirmed":false,"ack_text":"[neutral]はいよ。"}',
        "__HCA_DIAGNOSTIC__",
        "",
        "",
        0,
        "",
        "",
        "",
        json.dumps(environment, ensure_ascii=False),
        200,
    )

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert output["workflow_version"].startswith("hca-issue-iteration-state-feedback-")
    assert output["ack_text"].startswith("[neutral]HCA_DIAGNOSTIC_JSON ")
    diagnostic = json.loads(output["diagnostic_json"])
    assert diagnostic["workflow_version"] == output["workflow_version"]
    assert diagnostic["feedback_contract"]["state_query_feedback"] is True
    assert diagnostic["feedback_contract"]["ttl_seconds"] == 120
    assert diagnostic["feedback_contract"]["feedback_reason"] == "user_correction_after_state_query"
    assert diagnostic["environment"]["environment_status_code"] == 200
    assert diagnostic["environment"]["environment_snapshot_id"] == "env-diagnostic"
    assert diagnostic["environment"]["state_query_present"] is True
    assert diagnostic["environment"]["state_query_keys"] == ["room_light"]
    assert diagnostic["environment"]["room_light"]["authority"] == "vision_snapshot_processor"


def test_room_light_state_query_without_projection_does_not_execute() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"light_off","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    environment = {"snapshot_id": "env-no-state-query", "actions": []}

    output = main(raw, "照明消えてる？", "", "", 0, "", "", "", json.dumps(environment, ensure_ascii=False))

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert "まだ確認できねえ" in output["non_execute_reply"]


def test_light_command_still_executes_when_state_queries_exist() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"light_off","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    environment = {
        "snapshot_id": "env-room-light-command",
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "on",
                "authority": "vision_snapshot_processor",
            }
        },
        "actions": [
            {
                "action_id": "light_off",
                "aliases": ["電気を消して"],
                "label": "ライトを消す",
                "target_label": "電気",
                "verb": "消す",
                "pre_action_phrase": "電気を消す",
                "available": True,
                "noop": False,
            }
        ],
    }

    output = main(raw, "電気を消して", "", "", 0, "", "", "", json.dumps(environment, ensure_ascii=False))

    assert output["action_id"] == "light_off"
    assert output["is_action"] is True
    assert output["state_query_id"] == ""


def test_environment_state_uses_home_control_token_and_default_port() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "http://host.docker.internal:8790/environment/current" in text
    assert "http://host.docker.internal:8790/environment/relations" in text
    assert "http://host.docker.internal:8790/feedback/state-query" in text
    assert "ENVIRONMENT_RELATIONS_URL" in text
    assert "ENVIRONMENT_FEEDBACK_URL" in text
    assert "Authorization:Bearer {{#env.HOME_CONTROL_API_TOKEN#}}" in text
    assert "ENVIRONMENT_STATE_TOKEN" not in text
