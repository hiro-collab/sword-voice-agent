from __future__ import annotations

import json
from datetime import datetime, timezone
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


def extract_node_block(title: str) -> str:
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    title_index = next(index for index, line in enumerate(lines) if f"title: {title}" in line)
    start = title_index
    while start > 0 and not lines[start].startswith("      - data:"):
        start -= 1
    end = len(lines)
    for index in range(title_index + 1, len(lines)):
        if lines[index].startswith("      - data:"):
            end = index
            break
    return "\n".join(lines[start:end])


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


def test_room_light_state_query_overrides_light_action_and_uses_learning_context() -> None:
    main = load_main("action_id・issue_id整形")
    raw = json.dumps(
        {
            "action_id": "light_on",
            "confirmed": False,
            "ack_text": "[relaxed]ほうほう。ふんふん。また電気の話か！",
            "pre_action_text": "[neutral]電気をつけるか。やるよ。",
        },
        ensure_ascii=False,
    )
    environment = {
        "snapshot_id": "env-room-light-unknown",
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "unknown",
                "confidence_label": "low",
                "answer_hint": "映像推定では断定できない。日光の影響が強そう。",
                "authority": "vision_snapshot_processor",
                "learning": {"level": "reinforced", "level_index": 4, "accepted_count": 13},
            }
        },
    }

    output = main(
        raw,
        "電気はついてるでしょうか",
        environment_body=json.dumps(environment, ensure_ascii=False),
    )

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert output["pre_action_text"] == ""
    assert "また電気の話" not in output["ack_text"]
    assert "覚えた材料は増えてる" in output["non_execute_reply"]
    assert "点いてる？消えてる？" in output["non_execute_reply"]
    assert "実際はどう" not in output["non_execute_reply"]
    assert output["pending_state_query_id"] == "room_light"


def test_room_light_state_query_reports_detected_state_without_open_ended_prompt() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    environment = {
        "snapshot_id": "env-room-light-off",
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "off",
                "confidence_label": "medium",
                "answer_hint": "照明は消えている可能性が高い。",
                "authority": "vision_snapshot_processor",
                "learning": {"level": "reinforced", "level_index": 4, "accepted_count": 13},
            }
        },
    }

    output = main(
        raw,
        "電気はついてるでしょうか",
        environment_body=json.dumps(environment, ensure_ascii=False),
    )

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert "消えてる可能性" in output["non_execute_reply"]
    assert "そこそこ見えてる" in output["non_execute_reply"]
    assert "ついてるのか、消えてるのか" not in output["non_execute_reply"]


def test_room_light_state_query_uses_home_assistant_light_state_when_vision_is_unknown() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    environment = {
        "snapshot_id": "env-room-light-ha-on",
        "appliances": {
            "light": {
                "state": "on",
                "updated_at": "2026-05-07T19:27:01+09:00",
                "source": "home_assistant",
                "stale": False,
            }
        },
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "unknown",
                "confidence_label": "low",
                "answer_hint": "映像推定では断定できない。日光の影響が強そう。",
                "authority": "vision_snapshot_processor",
                "learning": {"level": "reinforced", "level_index": 4, "accepted_count": 20},
            }
        },
    }

    output = main(
        raw,
        "電気はついているでしょうか",
        environment_body=json.dumps(environment, ensure_ascii=False),
    )

    assert output["action_id"] == "none"
    assert output["is_action"] is False
    assert "機器状態では、電気はついてる" in output["non_execute_reply"]
    assert "映像推定" in output["non_execute_reply"]
    assert "点いてる？消えてる？" not in output["non_execute_reply"]


def test_room_light_state_query_reports_stale_home_assistant_light_state_as_last_seen() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]はいよ。"}'
    environment = {
        "snapshot_id": "env-room-light-ha-stale-on",
        "appliances": {
            "light": {
                "state": "on",
                "updated_at": "2026-05-07T19:27:01+09:00",
                "source": "home_assistant",
                "stale": True,
            }
        },
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "unknown",
                "confidence_label": "low",
                "answer_hint": "映像推定では断定できない。日光の影響が強そう。",
                "authority": "vision_snapshot_processor",
            }
        },
    }

    output = main(
        raw,
        "電気はついているでしょうか",
        environment_body=json.dumps(environment, ensure_ascii=False),
    )

    assert "最後に取れた機器状態では、電気はついてる" in output["non_execute_reply"]
    assert "違ってたら教えてくれ" in output["non_execute_reply"]


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


def test_action_parse_repairs_unsafe_ack_instead_of_dropping_it() -> None:
    main = load_main("action_id・issue_id整形")
    raw = json.dumps(
        {
            "action_id": "light_off",
            "confirmed": False,
            "ack_text": "[neutral]おう、聞こえたぜ。",
            "pre_action_text": "[neutral]電気を消すか。任せとけ。",
        },
        ensure_ascii=False,
    )

    output = main(raw, "電気を消して")

    assert output["ack_text"]
    assert output["ack_text"].startswith("[")
    assert "聞こえたぜ" not in output["ack_text"]
    assert "完了" not in output["ack_text"]


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


def test_decision_parse_keeps_natural_success_reply_with_short_ack_prefix() -> None:
    result = {
        "issue_id": "hca-test",
        "attempt": 1,
        "status": "success",
        "bridge_status": "submitted",
        "action_id": "door_open",
        "label": "中扉を開ける",
    }
    natural_decision = {
        "issue_id": "hca-test",
        "attempt": 1,
        "user_reply": "[happy]はいよ、中扉の操作は受け付けたぜ。",
        "decision": "finish",
        "issue_status": "resolved",
        "action_id": "door_open",
    }

    for title in ("判定JSON整形 1", "判定JSON整形 2", "判定JSON整形 3"):
        main = load_main(title)
        output = main(json.dumps(natural_decision, ensure_ascii=False), json.dumps(result, ensure_ascii=False), "")

        assert output["decision"] == "finish"
        assert output["user_reply"] == "[happy]はいよ、中扉の操作は受け付けたぜ。"


def test_light_success_creates_post_action_room_light_feedback_pending() -> None:
    result = {
        "issue_id": "hca-light-feedback",
        "attempt": 1,
        "status": "success",
        "bridge_status": "submitted",
        "action_id": "light_on",
        "label": "ライトをつける",
    }
    decision = {
        "issue_id": "hca-light-feedback",
        "attempt": 1,
        "user_reply": "[happy]ライトは受け付けたぜ。",
        "decision": "finish",
        "issue_status": "resolved",
        "action_id": "light_on",
    }
    environment = {
        "snapshot_id": "env-after-light-on",
        "wait_result": {
            "target": "room_light",
            "matched": True,
            "after": "2026-05-07T12:00:00+00:00",
            "observed_at": "2026-05-07T12:00:00.300000+00:00",
            "elapsed_ms": 300,
            "reason": "matched",
        },
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "unknown",
                "confidence_label": "low",
                "authority": "vision_snapshot_processor",
                "projected_by": "environment_state_server",
                "answer_hint": "映像推定では断定できない。",
                "evidence": {
                    "topic": "/vision/room_light/state",
                    "observed_at": "2026-05-07T12:00:00+00:00",
                    "updated_at": "2026-05-07T12:00:01+00:00",
                    "electric_on_probability": 0.51,
                },
            }
        },
    }

    for title in ("判定JSON整形 1", "判定JSON整形 2", "判定JSON整形 3"):
        main = load_main(title)
        output = main(
            json.dumps(decision, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            "",
            json.dumps(environment, ensure_ascii=False),
        )

        assert output["issue_status"] == "resolved"
        assert "実際はついてる" in output["user_reply"]
        assert output["pending_state_query_id"] == "room_light"
        pending = json.loads(output["pending_state_query_json"])
        assert pending["feedback_reason"] == "user_correction_after_light_action"
        assert pending["source_context"] == "post_light_action"
        assert pending["action_id"] == "light_on"
        assert pending["issue_id"] == "hca-light-feedback"
        assert pending["expected_state"] == "on"
        assert pending["snapshot_id"] == "env-after-light-on"
        assert pending["predicted_state"] == "unknown"
        assert pending["wait_result"]["matched"] is True
        assert pending["wait_result"]["reason"] == "matched"
        assert pending["evidence"]["topic"] == "/vision/room_light/state"


def test_light_success_does_not_use_room_light_snapshot_when_wait_times_out() -> None:
    result = {
        "issue_id": "hca-light-timeout",
        "attempt": 1,
        "status": "success",
        "bridge_status": "submitted",
        "action_id": "light_off",
    }
    decision = {
        "issue_id": "hca-light-timeout",
        "attempt": 1,
        "user_reply": "[happy]電気は受け付けたぜ。",
        "decision": "finish",
        "issue_status": "resolved",
        "action_id": "light_off",
    }
    environment = {
        "snapshot_id": "env-old-light",
        "wait_result": {
            "target": "room_light",
            "matched": False,
            "after": "2026-05-07T12:01:00+00:00",
            "timeout_ms": 1500,
            "reason": "timeout",
        },
        "state_queries": {
            "room_light": {
                "available": True,
                "stale": False,
                "state": "on",
                "confidence_label": "high",
            }
        },
    }

    for title in ("判定JSON整形 1", "判定JSON整形 2", "判定JSON整形 3"):
        main = load_main(title)
        output = main(
            json.dumps(decision, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            "",
            json.dumps(environment, ensure_ascii=False),
        )

        assert output["pending_state_query_id"] == ""
        assert output["pending_state_query_json"] == ""
        assert "実際は消えてる" not in output["user_reply"]
        assert "映像側の更新がまだ取れて" in output["user_reply"]


def test_post_action_room_light_feedback_payload_keeps_action_context() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]ふんふん。"}'
    environment = {"snapshot_id": "env-current-after-light", "state_queries": {"room_light": {"state": "unknown"}}}
    pending = {
        "type": "state_query_pending",
        "state_query_id": "room_light",
        "target": "room_light",
        "feedback_reason": "user_correction_after_light_action",
        "source_context": "post_light_action",
        "action_id": "light_on",
        "issue_id": "hca-light-feedback",
        "expected_state": "on",
        "snapshot_id": "env-after-light-on",
        "predicted_state": "unknown",
        "confidence_label": "low",
        "created_at": datetime.now(timezone.utc).isoformat(),
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
        "conv-light-feedback",
    )

    feedback = json.loads(output["state_query_feedback_json"])
    assert feedback["feedback_reason"] == "user_correction_after_light_action"
    assert feedback["source_context"] == "post_light_action"
    assert feedback["action_id"] == "light_on"
    assert feedback["issue_id"] == "hca-light-feedback"
    assert feedback["expected_state"] == "on"
    assert feedback["user_label"] == "on"
    assert feedback["idempotency_key"] == "state-query-feedback:conv-light-feedback:env-after-light-on:on"


def test_retry_attempts_emit_action_started_at_for_post_action_wait() -> None:
    result = {
        "issue_id": "hca-retry-at",
        "attempt": 1,
        "status": "error",
        "bridge_status": "failed",
        "action_id": "light_on",
    }
    decision = {
        "issue_id": "hca-retry-at",
        "attempt": 1,
        "decision": "retry_ai",
        "issue_status": "retrying",
        "action_id": "light_on",
        "next_input": "ライトをもう一回つける",
    }

    for title in ("判定JSON整形 1", "判定JSON整形 2", "判定JSON整形 3"):
        main = load_main(title)
        output = main(json.dumps(decision, ensure_ascii=False), json.dumps(result, ensure_ascii=False), "")

        assert output["decision"] == "retry_ai"
        assert output["action_started_at"]


def test_post_action_environment_nodes_wait_for_fresh_room_light_snapshot() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "title: Environment state after HA 1" in text
    assert "wait_for:room_light" in text
    assert "after:{{#1777642160512.action_started_at#}}" in text
    assert "after:{{#hca_decision_parse_1.action_started_at#}}" in text
    assert "after:{{#hca_decision_parse_2.action_started_at#}}" in text
    assert text.count("timeout_ms:1500") >= 3


def test_final_assigners_preserve_post_action_room_light_feedback_pending() -> None:
    for attempt in (1, 2, 3):
        block = extract_node_block(f"Issue状態保存 final {attempt}")
        parse_node = f"hca_decision_parse_{attempt}"

        assert f"- {parse_node}\n                - pending_state_query_id" in block
        assert f"- {parse_node}\n                - pending_state_query_json" in block
        assert "- conversation\n                - pending_state_query_id" in block
        assert "- conversation\n                - pending_state_query_json" in block


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
    assert "点いてる？消えてる？" in output["non_execute_reply"]
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


def test_room_light_user_feedback_prefers_explicit_state_over_yes_prefix() -> None:
    main = load_main("action_id・issue_id整形")
    raw = '{"action_id":"none","confirmed":false,"ack_text":"[neutral]ふんふん。"}'
    environment = {"snapshot_id": "env-current", "state_queries": {"room_light": {"state": "unknown"}}}
    pending_unknown = {
        "type": "state_query_pending",
        "state_query_id": "room_light",
        "snapshot_id": "env-original",
        "predicted_state": "unknown",
    }

    off_output = main(
        raw,
        "はい、消えています",
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
    on_output = main(
        raw,
        "はい、点いてます",
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

    assert json.loads(off_output["state_query_feedback_json"])["user_label"] == "off"
    assert "実際は消えてる" in off_output["non_execute_reply"]
    assert json.loads(on_output["state_query_feedback_json"])["user_label"] == "on"
    assert "実際はついてる" in on_output["non_execute_reply"]


def test_non_execute_reply_prompt_must_not_rewrite_fixed_reply() -> None:
    node = extract_node_block("通常会話LLM")

    assert "実行不要応答候補が空でなければ、その文字列を一字一句そのまま返してください" in node
    assert "前後に相槌、説明、提案、締め文、追加質問を追加しないでください" in node
    assert "一字一句そのまま読む必要はありません" not in node


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
        "appliances": {
            "light": {
                "state": "on",
                "stale": False,
                "source": "home_assistant",
                "updated_at": "2026-05-07T19:27:01+09:00",
                "action_id": "light_on",
            }
        },
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
    assert diagnostic["feedback_contract"]["post_action_light_feedback"] is True
    assert diagnostic["feedback_contract"]["ttl_seconds"] == 120
    assert diagnostic["feedback_contract"]["wait_for_room_light"]["timeout_ms"] == 1500
    assert diagnostic["feedback_contract"]["wait_for_room_light"]["timeout_status"] == 200
    assert "observed_at_after" in diagnostic["feedback_contract"]["wait_for_room_light"]["matched_true_requires"]
    assert "source_snapshot_id_present" in diagnostic["feedback_contract"]["wait_for_room_light"]["matched_true_requires"]
    assert "user_correction_after_state_query" in diagnostic["feedback_contract"]["feedback_reasons"]
    assert "user_correction_after_light_action" in diagnostic["feedback_contract"]["feedback_reasons"]
    assert diagnostic["environment"]["environment_status_code"] == 200
    assert diagnostic["environment"]["environment_snapshot_id"] == "env-diagnostic"
    assert diagnostic["environment"]["state_query_present"] is True
    assert diagnostic["environment"]["state_query_keys"] == ["room_light"]
    assert diagnostic["environment"]["room_light"]["authority"] == "vision_snapshot_processor"
    assert diagnostic["environment"]["light_appliance"]["state"] == "on"
    assert diagnostic["environment"]["light_appliance"]["source"] == "home_assistant"


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
