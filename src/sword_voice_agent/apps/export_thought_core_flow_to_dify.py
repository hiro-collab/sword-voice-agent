from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_FLOW_PATH = Path("services/thought-core/flows/home-control-light-v0.yaml")
DEFAULT_OUTPUT_PATH = Path("dify-apps/Thought Core Home Control.visual-draft.yml")
NODE_WIDTH = 260
PLACEHOLDER_HEIGHT = 106
COMPACT_CODE_HEIGHT = 52


class FlowExportError(ValueError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export a Thought Core flow draft to a Dify visual placeholder YAML."
    )
    parser.add_argument(
        "--flow-yaml",
        default=str(DEFAULT_FLOW_PATH),
        help=(
            "Path to the Thought Core flow spec. The current draft uses "
            "JSON-compatible YAML so no third-party YAML parser is needed."
        ),
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path for the generated Dify app YAML.",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Print generated YAML instead of writing it.",
    )
    return parser


def load_flow(path: str | Path) -> dict[str, Any]:
    flow_path = Path(path)
    try:
        raw = flow_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise FlowExportError(f"cannot read flow file: {flow_path} ({exc})") from exc
    try:
        flow = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FlowExportError(
            f"{flow_path} must be JSON-compatible YAML for this exporter: {exc}"
        ) from exc
    if not isinstance(flow, dict):
        raise FlowExportError("flow root must be an object")
    validate_flow(flow)
    return flow


def validate_flow(flow: Mapping[str, Any]) -> None:
    if flow.get("schema_version") != "thought-core.flow.v0":
        raise FlowExportError("unsupported flow schema_version")
    nodes = flow.get("nodes")
    edges = flow.get("edges")
    if not isinstance(nodes, list) or not nodes:
        raise FlowExportError("flow.nodes must be a non-empty list")
    if not isinstance(edges, list):
        raise FlowExportError("flow.edges must be a list")
    seen: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            raise FlowExportError("each flow node must be an object")
        node_id = _required_text(node, "id")
        _required_text(node, "kind")
        _required_text(node, "label")
        if node_id in seen:
            raise FlowExportError(f"duplicate flow node id: {node_id}")
        seen.add(node_id)
    for edge in edges:
        if not isinstance(edge, dict):
            raise FlowExportError("each flow edge must be an object")
        source = _required_text(edge, "source")
        target = _required_text(edge, "target")
        if source not in seen:
            raise FlowExportError(f"edge source is unknown: {source}")
        if target not in seen:
            raise FlowExportError(f"edge target is unknown: {target}")


def build_dify_workflow(flow: Mapping[str, Any]) -> dict[str, Any]:
    dify = _mapping(flow.get("dify"))
    app_name = str(dify.get("app_name") or flow.get("name") or "Thought Core Visual Draft")
    app_description = str(dify.get("app_description") or flow.get("description") or "")
    nodes = [_build_dify_node(node) for node in _list(flow["nodes"])]
    node_type_by_id = {
        str(node["id"]): str(node["data"]["type"])
        for node in nodes
    }
    return {
        "app": {
            "description": app_description,
            "icon": "🧠",
            "icon_background": "#E0F2FE",
            "icon_type": "emoji",
            "mode": "advanced-chat",
            "name": app_name,
            "use_icon_as_answer_icon": False,
        },
        "dependencies": [],
        "kind": "app",
        "version": "0.6.0",
        "workflow": {
            "conversation_variables": [],
            "environment_variables": [
                _environment_variable(
                    "THOUGHT_CORE_BASE_URL",
                    "Reference only. Thought Core remains the runtime source of truth.",
                    "http://host.docker.internal:18787",
                )
            ],
            "features": _default_features(),
            "graph": {
                "edges": [
                    _build_dify_edge(edge, node_type_by_id)
                    for edge in _list(flow.get("edges", []))
                ],
                "nodes": nodes,
                "viewport": {"x": 40, "y": 120, "zoom": 0.58},
            },
            "rag_pipeline_variables": [],
        },
    }


def _build_dify_node(node: Mapping[str, Any]) -> dict[str, Any]:
    node_id = str(node["id"])
    kind = str(node["kind"])
    dify_type = str(node.get("dify_type") or "code")
    label = str(node["label"])
    title = label if dify_type == "start" else f"[TC] {label}"
    x = float(int(node.get("column", 0)) * 315)
    y = float(int(node.get("lane", 1)) * 170)
    data: dict[str, Any]
    height = PLACEHOLDER_HEIGHT
    if dify_type == "start":
        data = {
            "selected": False,
            "title": title,
            "type": "start",
            "variables": [],
        }
        height = 73
    elif dify_type == "answer":
        data = {
            "answer": _node_description(node),
            "selected": False,
            "title": title,
            "type": "answer",
            "variables": [],
        }
        height = 102
    else:
        data = {
            "selected": False,
            "title": title,
            "type": "code",
            "code": _placeholder_code(node),
            "code_language": "python3",
            "outputs": {
                "result": {"children": None, "type": "string"},
            },
            "variables": [],
        }
        height = COMPACT_CODE_HEIGHT
    return {
        "data": data,
        "height": height,
        "id": node_id,
        "position": {"x": x, "y": y},
        "positionAbsolute": {"x": x, "y": y},
        "selected": False,
        "sourcePosition": "right",
        "targetPosition": "left",
        "type": "custom",
        "width": NODE_WIDTH,
    }


def _build_dify_edge(edge: Mapping[str, Any], node_type_by_id: Mapping[str, str]) -> dict[str, Any]:
    source = str(edge["source"])
    target = str(edge["target"])
    label = str(edge.get("label") or "source")
    return {
        "data": {
            "isInLoop": source == target or _is_retry_edge(edge),
            "sourceType": node_type_by_id[source],
            "targetType": node_type_by_id[target],
            "label": label,
            "thoughtCoreLabel": label,
        },
        # Dify re-exports manually-created edges in this source/target handle shape.
        # Staying close to it makes the graph less likely to be normalized away.
        "id": f"{source}-source-{target}-target",
        "selected": False,
        "source": source,
        "sourceHandle": "source",
        "target": target,
        "targetHandle": "target",
        "type": "custom",
        "zIndex": 0,
    }


def _is_retry_edge(edge: Mapping[str, Any]) -> bool:
    label = str(edge.get("label") or "")
    return "retry" in label or "attempt" in label


def _placeholder_description(node: Mapping[str, Any]) -> str:
    metadata = {
        "id": node["id"],
        "kind": node["kind"],
        "description": node.get("description", ""),
        "events": node.get("events", []),
        "editable": node.get("editable", []),
    }
    return (
        f"{_node_description(node)}\n\n"
        "Dify上では実行しない Thought Core 専用ノードです。"
        "編集・議論用の no-op code node として表現しています。\n\n"
        "THOUGHT_CORE_META_JSON:\n"
        f"{json.dumps(metadata, ensure_ascii=False, indent=2)}"
    )


def _node_description(node: Mapping[str, Any]) -> str:
    return str(node.get("description") or node.get("kind") or node.get("id") or "")


def _placeholder_code(node: Mapping[str, Any]) -> str:
    metadata = {
        "id": node["id"],
        "kind": node["kind"],
        "label": node["label"],
        "description": node.get("description", ""),
        "events": node.get("events", []),
        "editable": node.get("editable", []),
    }
    editable = ", ".join(str(item) for item in _list(node.get("editable", [])))
    notes = _node_description(node)
    metadata_json = json.dumps(metadata, ensure_ascii=False, indent=2)
    return (
        "# THOUGHT_CORE_META_JSON\n"
        f"THOUGHT_CORE_META_JSON = {json.dumps(metadata_json, ensure_ascii=False)}\n\n"
        "def main():\n"
        "    return {\n"
        "        \"result\": THOUGHT_CORE_META_JSON,\n"
        "    }\n"
    )


def _environment_variable(name: str, description: str, value: str) -> dict[str, Any]:
    return {
        "description": description,
        "id": f"env-{_slug(name)}",
        "name": name,
        "selector": ["env", name],
        "value": value,
        "value_type": "string",
    }


def _default_features() -> dict[str, Any]:
    return {
        "file_upload": {
            "allowed_file_extensions": [".JPG", ".JPEG", ".PNG", ".GIF", ".WEBP", ".SVG"],
            "allowed_file_types": ["image"],
            "allowed_file_upload_methods": ["local_file", "remote_url"],
            "enabled": False,
            "fileUploadConfig": {
                "attachment_image_file_size_limit": 2,
                "audio_file_size_limit": 50,
                "batch_count_limit": 5,
                "file_size_limit": 15,
                "file_upload_limit": 20,
                "image_file_batch_limit": 10,
                "image_file_size_limit": 10,
                "single_chunk_attachment_limit": 10,
                "video_file_size_limit": 100,
            },
            "image": {
                "enabled": False,
                "number_limits": 3,
                "transfer_methods": ["local_file", "remote_url"],
            },
            "number_limits": 3,
        },
        "opening_statement": "",
        "retriever_resource": {"enabled": False},
        "sensitive_word_avoidance": {"enabled": False},
        "speech_to_text": {"enabled": False},
        "suggested_questions": [],
        "suggested_questions_after_answer": {"enabled": False},
        "text_to_speech": {"enabled": False, "language": "", "voice": ""},
    }


def dump_yaml(value: Any) -> str:
    return _dump_yaml(value, 0).rstrip() + "\n"


def _dump_yaml(value: Any, indent: int) -> str:
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines: list[str] = []
        for key, item in value.items():
            key_text = _dump_key(str(key))
            if _is_scalar(item):
                lines.append(f"{' ' * indent}{key_text}: {_dump_scalar(item, indent)}")
            else:
                lines.append(f"{' ' * indent}{key_text}:")
                lines.append(_dump_yaml(item, indent + 2))
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return "[]"
        lines = []
        for item in value:
            if _is_scalar(item):
                lines.append(f"{' ' * indent}- {_dump_scalar(item, indent)}")
            elif isinstance(item, dict) and item:
                item_lines = _dump_yaml(item, indent + 2).splitlines()
                first = item_lines[0].lstrip()
                lines.append(f"{' ' * indent}- {first}")
                lines.extend(item_lines[1:])
            else:
                lines.append(f"{' ' * indent}-")
                lines.append(_dump_yaml(item, indent + 2))
        return "\n".join(lines)
    return _dump_scalar(value, indent)


def _is_scalar(value: Any) -> bool:
    return (
        value is None
        or isinstance(value, (str, int, float, bool))
        or value == []
        or value == {}
    )


def _dump_key(key: str) -> str:
    if key in {"y", "Y", "n", "N", "yes", "no", "on", "off", "true", "false", "null"}:
        return json.dumps(key)
    if key.replace("_", "").replace("-", "").isalnum() and key[:1].isalpha():
        return key
    return json.dumps(key, ensure_ascii=False)


def _dump_scalar(value: Any, indent: int) -> str:
    if value == []:
        return "[]"
    if value == {}:
        return "{}"
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if "\n" in text:
        child_indent = " " * (indent + 2)
        return "|-\n" + "\n".join(f"{child_indent}{line}" for line in text.splitlines())
    return json.dumps(text, ensure_ascii=False)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _required_text(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise FlowExportError(f"{key} must be a non-empty string")
    return item


def _slug(value: str) -> str:
    chars = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        elif chars and chars[-1] != "-":
            chars.append("-")
    return "".join(chars).strip("-") or "edge"


def run(args: argparse.Namespace) -> str:
    flow = load_flow(args.flow_yaml)
    yaml_text = dump_yaml(build_dify_workflow(flow))
    if args.print:
        return yaml_text
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml_text, encoding="utf-8")
    return str(output_path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except FlowExportError as exc:
        print(f"Input error: {exc}")
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
