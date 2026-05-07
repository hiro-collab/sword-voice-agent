from __future__ import annotations

import json
from pathlib import Path
from unittest import TestCase

from sword_voice_agent.apps.export_thought_core_flow_to_dify import (
    build_dify_workflow,
    build_parser,
    dump_yaml,
    load_flow,
    run,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
FLOW_PATH = REPO_ROOT / "services" / "thought-core" / "flows" / "home-control-light-v0.yaml"


class ExportThoughtCoreFlowToDifyTest(TestCase):
    def test_load_flow_reads_json_compatible_yaml(self) -> None:
        flow = load_flow(FLOW_PATH)

        self.assertEqual(flow["schema_version"], "thought-core.flow.v0")
        self.assertEqual(flow["id"], "home-control-light-v0")
        self.assertTrue(any(node["kind"] == "home.execute" for node in flow["nodes"]))

    def test_build_dify_workflow_uses_noop_code_nodes_for_thought_core_nodes(self) -> None:
        workflow = build_dify_workflow(load_flow(FLOW_PATH))
        nodes = {
            node["id"]: node
            for node in workflow["workflow"]["graph"]["nodes"]
        }

        execute = nodes["execute_action"]
        self.assertEqual(execute["data"]["type"], "code")
        self.assertEqual(execute["data"]["title"], "[TC] 家電操作実行")
        self.assertEqual(execute["height"], 52)
        self.assertIn("THOUGHT_CORE_META_JSON", execute["data"]["code"])
        self.assertIn('\\"kind\\": \\"home.execute\\"', execute["data"]["code"])
        self.assertEqual(list(execute["data"]["outputs"]), ["result"])

    def test_edges_keep_thought_core_branch_labels(self) -> None:
        workflow = build_dify_workflow(load_flow(FLOW_PATH))
        edges = workflow["workflow"]["graph"]["edges"]

        retry_edge = next(
            edge for edge in edges
            if edge["source"] == "retry_message" and edge["target"] == "retry_controller"
        )
        self.assertEqual(retry_edge["data"]["thoughtCoreLabel"], "next_attempt")
        self.assertEqual(retry_edge["data"]["label"], "next_attempt")
        self.assertEqual(
            retry_edge["id"],
            "retry_message-source-retry_controller-target",
        )
        self.assertTrue(retry_edge["data"]["isInLoop"])

    def test_dump_yaml_is_human_readable_and_contains_tc_nodes(self) -> None:
        yaml_text = dump_yaml(build_dify_workflow(load_flow(FLOW_PATH)))

        self.assertIn('name: "Thought Core Home Control Visual Draft"', yaml_text)
        self.assertIn("dependencies: []", yaml_text)
        self.assertNotIn("dependencies:\n[]", yaml_text)
        self.assertIn("- data:", yaml_text)
        self.assertIn('title: "[TC] 成功判定"', yaml_text)
        self.assertIn("THOUGHT_CORE_META_JSON", yaml_text)

    def test_run_writes_generated_yaml(self) -> None:
        output = REPO_ROOT / "tests" / "_tmp" / "thought-core-visual-draft-test.yml"
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()

        args = build_parser().parse_args([
            "--flow-yaml",
            str(FLOW_PATH),
            "--output",
            str(output),
        ])

        result = run(args)

        self.assertEqual(result, str(output))
        text = output.read_text(encoding="utf-8")
        self.assertIn("[TC] 再試行制御", text)
        self.assertTrue(text.startswith("app:"))

    def test_flow_file_stays_json_compatible(self) -> None:
        parsed = json.loads(FLOW_PATH.read_text(encoding="utf-8"))

        self.assertEqual(parsed["schema_version"], "thought-core.flow.v0")
