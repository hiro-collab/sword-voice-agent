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


def write_minimal_media_index(root: Path) -> Path:
    return write_json(
        root / "media-index.json",
        {
            "assets": [
                {
                    "id": "voice.turn_light_on.20260603",
                    "kind": "audio",
                    "duration_sec": 2.5,
                }
            ]
        },
    )


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def write_jsonl(path: Path, values: list[object]) -> Path:
    path.write_text(
        "\n".join(json.dumps(value, ensure_ascii=False) for value in values),
        encoding="utf-8",
    )
    return path
