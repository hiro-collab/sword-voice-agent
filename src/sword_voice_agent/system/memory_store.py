from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from sword_voice_agent.system.access_control import PolicyStore
from sword_voice_agent.system.event_journal import EventJournal


class MemoryStore:
    """Small JSONL memory-core store for candidate/commit/retrieve tests."""

    def __init__(
        self,
        root: str | Path,
        *,
        policy: PolicyStore,
        journal: EventJournal | None = None,
    ) -> None:
        self.root = Path(root)
        self.policy = policy
        self.journal = journal

    @property
    def candidates_path(self) -> Path:
        return self.root / "candidates.jsonl"

    @property
    def facts_path(self) -> Path:
        return self.root / "facts.jsonl"

    def write_candidate(
        self,
        *,
        requester: str,
        item: Mapping[str, Any],
    ) -> dict[str, Any]:
        candidate = dict(item)
        scope = str(candidate.get("scope") or "")
        self.policy.require(
            requester,
            "memory.write.candidate",
            resource={"type": "memory_scope", "scope": scope},
        )
        _require_candidate_source(candidate)
        candidate.setdefault("schema_version", "memory.item.v0")
        candidate.setdefault("memory_id", f"mcand_{uuid4().hex}")
        candidate["status"] = "candidate"
        candidate.setdefault("created_at", _timestamp())
        scope_policy = self.policy.memory_scopes[scope]
        candidate.setdefault(
            "requires_user_confirmation",
            bool(scope_policy.get("write_requires_confirmation")),
        )
        duplicate = self._find_candidate_by_key(_dedup_key(candidate))
        if duplicate is not None:
            return {
                "ok": True,
                "candidate_id": duplicate["memory_id"],
                "status": "duplicate",
            }
        self._append_jsonl(self.candidates_path, candidate)
        self._append_memory_event(
            "memory.candidate_created",
            candidate,
            requester=requester,
        )
        return {
            "ok": True,
            "candidate_id": str(candidate["memory_id"]),
            "status": "accepted",
        }

    def commit(
        self,
        *,
        requester: str,
        candidate_id: str,
        user_confirmed: bool = False,
    ) -> dict[str, Any]:
        candidate = self.get_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"unknown candidate: {candidate_id}")
        scope = str(candidate["scope"])
        self.policy.require(
            requester,
            "memory.write.confirmed",
            resource={"type": "memory_scope", "scope": scope},
            context={"user_confirmed": user_confirmed},
        )
        committed = dict(candidate)
        committed["memory_id"] = f"mem_{uuid4().hex}"
        committed["status"] = "committed"
        source = dict(committed.get("source") or {})
        source["candidate_id"] = candidate_id
        committed["source"] = source
        self._append_jsonl(self.facts_path, committed)
        self._append_memory_event(
            "memory.committed",
            committed,
            requester=requester,
        )
        return {
            "ok": True,
            "memory_id": str(committed["memory_id"]),
            "status": "committed",
        }

    def retrieve(
        self,
        *,
        requester: str,
        scopes: list[str],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for scope in scopes:
            self.policy.require(
                requester,
                _read_capability_for_scope(scope),
                resource={"type": "memory_scope", "scope": scope},
            )
            for item in self._read_jsonl(self.facts_path):
                if item.get("scope") == scope:
                    results.append(item)
                    if len(results) >= limit:
                        return results
        return results

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        for candidate in self._read_jsonl(self.candidates_path):
            if candidate.get("memory_id") == candidate_id:
                return candidate
        return None

    def _find_candidate_by_key(self, key: str) -> dict[str, Any] | None:
        for candidate in self._read_jsonl(self.candidates_path):
            if _dedup_key(candidate) == key:
                return candidate
        return None

    def _append_memory_event(
        self,
        event: str,
        item: Mapping[str, Any],
        *,
        requester: str,
    ) -> None:
        if self.journal is None:
            return
        source = item.get("source") if isinstance(item.get("source"), Mapping) else {}
        self.journal.append_event(
            service_id="memory_core",
            service="memory-core",
            event=event,
            trace_id=str(source.get("trace_id") or f"trace_{uuid4().hex}"),
            turn_id=(str(source["turn_id"]) if source.get("turn_id") else None),
            payload={
                "requester": requester,
                "memory_id": item.get("memory_id"),
                "scope": item.get("scope"),
                "status": item.get("status"),
            },
            layer="memory",
        )

    def _append_jsonl(self, path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True))
            stream.write("\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        values: list[dict[str, Any]] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                values.append(value)
        return values


def _require_candidate_source(candidate: Mapping[str, Any]) -> None:
    source = candidate.get("source")
    if not isinstance(source, Mapping):
        raise ValueError("memory candidate requires source")
    if not source.get("trace_id"):
        raise ValueError("memory candidate requires source.trace_id")
    if not source.get("turn_id"):
        raise ValueError("memory candidate requires source.turn_id")


def _read_capability_for_scope(scope: str) -> str:
    if scope == "session":
        return "memory.read.session"
    if scope == "episodic":
        return "memory.read.episodic"
    return "memory.read.semantic"


def _dedup_key(item: Mapping[str, Any]) -> str:
    payload = {
        "memory_type": item.get("memory_type"),
        "scope": item.get("scope"),
        "content": item.get("content"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
