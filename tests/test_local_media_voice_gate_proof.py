from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sword_voice_agent.apps.local_media_voice_gate_proof import build_parser, run


class LocalMediaVoiceGateProofTest(TestCase):
    def test_collect_local_redacts_raw_text_and_preserves_labels(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_index = write_json(
                root / "media-index.json",
                {
                    "assets": [
                        {
                            "id": "voice.turn_light_on.20260603",
                            "kind": "audio",
                            "relative_path": "local/media/voice/turn_light_on.m4a",
                            "duration_sec": 2.5,
                        }
                    ]
                },
            )
            gate_events = write_jsonl(
                root / "gate-events.jsonl",
                [
                    {"type": "gate.opened", "reason": "activation_delay_passed"},
                    {"type": "gate.released", "reason": "release_delay_passed"},
                ],
            )
            stt_diagnostic = write_json(
                root / "stt-diagnostic.json",
                {
                    "phase_path": [
                        "audio_open",
                        "sound_detected",
                        "speech_detected",
                        "result_ready",
                    ],
                    "final_result": True,
                    "transcript": "電気をつけて",
                },
            )
            thought_core_events = write_jsonl(
                root / "thought-core-events.jsonl",
                [
                    {
                        "event_type": "input.understood",
                        "data": {
                            "query": "電気をつけて",
                            "observed_intent": "home_light_on",
                        },
                    },
                    {
                        "event_type": "action.proposed",
                        "data": {
                            "action_id": "light_on",
                            "prompt": "do not publish this prompt",
                        },
                    },
                    {
                        "event_type": "turn.completed",
                        "data": {
                            "status": "success",
                            "answer": "raw assistant speech is not shareable",
                        },
                    },
                ],
            )

            args = build_parser().parse_args(
                [
                    "--asset-id",
                    "voice.turn_light_on.20260603",
                    "--media-index",
                    str(media_index),
                    "--gate-events",
                    str(gate_events),
                    "--stt-diagnostic",
                    str(stt_diagnostic),
                    "--thought-core-events",
                    str(thought_core_events),
                    "--mode",
                    "collect-local",
                ]
            )

            result = run(args)

            self.assertEqual(result["result"], "pass")
            self.assertEqual(result["stt"]["transcript_bucket"], "nonempty:short")
            self.assertEqual(result["thought_core"]["observed_intent"], "home_light_on")
            self.assertEqual(result["thought_core"]["observed_action_id"], "light_on")
            self.assertEqual(result["thought_core"]["status"], "success")
            self.assertEqual(result["chain"]["result"], "pass")
            self.assertTrue(result["chain"]["ready_for_middle_review"])
            self.assertEqual(
                result["chain"]["layer_results"],
                {
                    "gate_opened": True,
                    "stt_final_result": True,
                    "thought_core_turn_completed": True,
                },
            )
            rendered = json.dumps(result, ensure_ascii=False)
            self.assertNotIn("電気をつけて", rendered)
            self.assertNotIn("raw assistant speech", rendered)
            self.assertNotIn("do not publish this prompt", rendered)
            self.assertNotIn(str(root), rendered)

    def test_rejects_raw_media_input_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_index = write_minimal_media_index(root)
            raw_audio_path = root / "voice.wav"
            raw_audio_path.write_bytes(b"not used")

            args = build_parser().parse_args(
                [
                    "--asset-id",
                    "voice.turn_light_on.20260603",
                    "--media-index",
                    str(media_index),
                    "--stt-diagnostic",
                    str(raw_audio_path),
                ]
            )

            with self.assertRaises(ValueError):
                run(args)

    def test_rejects_secret_config_input_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_index = write_minimal_media_index(root)
            token_path = root / "provider-token.json"
            token_path.write_text("{}", encoding="utf-8")

            args = build_parser().parse_args(
                [
                    "--asset-id",
                    "voice.turn_light_on.20260603",
                    "--media-index",
                    str(media_index),
                    "--thought-core-events",
                    str(token_path),
                ]
            )

            with self.assertRaises(ValueError):
                run(args)

    def test_preview_mode_uses_source_static_layer_by_default(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_index = write_minimal_media_index(root)

            args = build_parser().parse_args(
                [
                    "--asset-id",
                    "voice.turn_light_on.20260603",
                    "--media-index",
                    str(media_index),
                ]
            )

            result = run(args)

            self.assertEqual(result["proof_layer"], "source/static-command-preview")
            self.assertEqual(result["result"], "not_enough_evidence")
            self.assertFalse(result["safety"]["raw_media_shared"])
            self.assertFalse(result["safety"]["raw_transcript_shared"])

    def test_expected_closed_fails_when_gesture_events_open_gate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_index = write_minimal_media_index(root, asset_id="gesture.victory")
            gesture_events = write_jsonl(
                root / "gesture-events.jsonl",
                [
                    gesture_event(timestamp=0.0, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.2, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.4, primary="sword_sign", active=True),
                ],
            )

            args = build_parser().parse_args(
                [
                    "--asset-id",
                    "gesture.victory",
                    "--media-index",
                    str(media_index),
                    "--gesture-events",
                    str(gesture_events),
                    "--gate-source",
                    "gesture-events",
                    "--expected-gate",
                    "closed",
                    "--known-limitation",
                    "victory_false_open",
                ]
            )

            result = run(args)

            self.assertEqual(result["result"], "known_limitation_fail")
            self.assertEqual(result["known_limitation"], "victory_false_open")
            self.assertTrue(result["gate"]["opened"])
            self.assertEqual(result["gate_expectation"]["observed"], "open")
            self.assertFalse(result["gate_expectation"]["matched"])
            self.assertEqual(result["chain"]["result"], "not_enough_evidence")
            rendered = json.dumps(result, ensure_ascii=False)
            self.assertNotIn(str(root), rendered)

    def test_expected_closed_passes_when_gesture_events_keep_gate_closed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_index = write_minimal_media_index(root, asset_id="gesture.open_hand")
            gesture_events = write_jsonl(
                root / "gesture-events.jsonl",
                [
                    gesture_event(timestamp=0.0, primary="none", active=False),
                    gesture_event(timestamp=0.2, primary="none", active=False),
                    gesture_event(timestamp=0.4, primary="none", active=False),
                ],
            )

            args = build_parser().parse_args(
                [
                    "--asset-id",
                    "gesture.open_hand",
                    "--media-index",
                    str(media_index),
                    "--gesture-events",
                    str(gesture_events),
                    "--gate-source",
                    "gesture-events",
                    "--expected-gate",
                    "closed",
                ]
            )

            result = run(args)

            self.assertEqual(result["result"], "pass")
            self.assertFalse(result["gate"]["opened"])
            self.assertEqual(result["gate_expectation"]["observed"], "closed")
            self.assertTrue(result["gate_expectation"]["matched"])

    def test_expected_open_passes_from_gesture_events_without_raw_media(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_index = write_minimal_media_index(root, asset_id="gesture.sword")
            gesture_events = write_jsonl(
                root / "gesture-events.jsonl",
                [
                    gesture_event(timestamp=0.0, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.2, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.4, primary="sword_sign", active=True),
                ],
            )

            args = build_parser().parse_args(
                [
                    "--asset-id",
                    "gesture.sword",
                    "--media-index",
                    str(media_index),
                    "--gesture-events",
                    str(gesture_events),
                    "--gate-source",
                    "gesture-events",
                    "--expected-gate",
                    "open",
                ]
            )

            result = run(args)

            self.assertEqual(result["result"], "pass")
            self.assertTrue(result["gate"]["opened"])
            self.assertEqual(result["gesture"]["primary_counts"], {"sword_sign": 3})
            self.assertFalse(result["safety"]["raw_media_shared"])
            self.assertEqual(result["chain"]["result"], "not_enough_evidence")

    def test_revised_gate_profile_requires_three_active_frames(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_index = write_json(
                root / "media-index.json",
                {
                    "assets": [
                        {"id": "gesture.sword", "kind": "video"},
                        {"id": "gesture.open_hand", "kind": "video"},
                        {"id": "gesture.victory", "kind": "video"},
                    ]
                },
            )
            sword_events = write_jsonl(
                root / "sword-events.jsonl",
                [
                    gesture_event(timestamp=0.0, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.12, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.21, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.31, primary="none", active=False),
                ],
            )
            open_hand_events = write_jsonl(
                root / "open-hand-events.jsonl",
                [
                    gesture_event(timestamp=0.0, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.12, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.31, primary="none", active=False),
                ],
            )
            victory_events = write_jsonl(
                root / "victory-events.jsonl",
                [
                    gesture_event(timestamp=0.0, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.32, primary="sword_sign", active=True),
                    gesture_event(timestamp=0.4, primary="none", active=False),
                ],
            )

            sword = run_revised_gate_case(
                media_index,
                "gesture.sword",
                sword_events,
                expected_gate="open",
            )
            open_hand = run_revised_gate_case(
                media_index,
                "gesture.open_hand",
                open_hand_events,
                expected_gate="closed",
            )
            victory = run_revised_gate_case(
                media_index,
                "gesture.victory",
                victory_events,
                expected_gate="closed",
                known_limitation="victory_false_open",
            )

            self.assertEqual(sword["result"], "pass")
            self.assertEqual(sword["gate_expectation"]["observed"], "open")
            self.assertEqual(open_hand["result"], "pass")
            self.assertEqual(open_hand["gate_expectation"]["observed"], "closed")
            self.assertEqual(victory["result"], "pass")
            self.assertEqual(victory["gate_expectation"]["observed"], "closed")
            self.assertEqual(
                sword["gate"]["min_activation_active_frames"],
                3,
            )

    def test_rejects_unsafe_known_limitation_label(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            media_index = write_minimal_media_index(root, asset_id="gesture.victory")

            args = build_parser().parse_args(
                [
                    "--asset-id",
                    "gesture.victory",
                    "--media-index",
                    str(media_index),
                    "--known-limitation",
                    "C:\\Users\\person\\private",
                ]
            )

            with self.assertRaises(ValueError):
                run(args)


def write_minimal_media_index(
    root: Path,
    *,
    asset_id: str = "voice.turn_light_on.20260603",
) -> Path:
    return write_json(
        root / "media-index.json",
        {
            "assets": [
                {
                    "id": asset_id,
                    "kind": "video" if asset_id.startswith("gesture.") else "audio",
                    "duration_sec": 2.5,
                }
            ]
        },
    )


def gesture_event(*, timestamp: float, primary: str, active: bool) -> object:
    return {
        "topic": "/vision/sword_sign/state",
        "payload": {
            "type": "gesture_state",
            "source": "mediapipe_sword_sign",
            "timestamp": timestamp,
            "primary": primary,
            "gestures": {
                "sword_sign": {
                    "active": active,
                    "confidence": 0.95 if active else 0.05,
                }
            },
            "stable": {
                "gestures": {
                    "sword_sign": {
                        "active": active,
                        "activated": active,
                        "released": False,
                        "confidence": 0.95 if active else 0.05,
                    }
                }
            },
        },
    }


def run_revised_gate_case(
    media_index: Path,
    asset_id: str,
    gesture_events: Path,
    *,
    expected_gate: str,
    known_limitation: str = "",
) -> dict[str, object]:
    argv = [
        "--asset-id",
        asset_id,
        "--media-index",
        str(media_index),
        "--gesture-events",
        str(gesture_events),
        "--gate-source",
        "gesture-events",
        "--expected-gate",
        expected_gate,
        "--activation-delay",
        "0.3",
        "--activation-gap-grace",
        "0.15",
        "--min-activation-active-frames",
        "3",
    ]
    if known_limitation:
        argv.extend(["--known-limitation", known_limitation])
    return run(build_parser().parse_args(argv))


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def write_jsonl(path: Path, values: list[object]) -> Path:
    path.write_text(
        "\n".join(json.dumps(value, ensure_ascii=False) for value in values),
        encoding="utf-8",
    )
    return path
