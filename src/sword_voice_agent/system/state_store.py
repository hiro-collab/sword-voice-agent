from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Mapping

from sword_voice_agent.system.access_control import AccessDenied, PolicyStore


SERVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,96}$")


class StateStore:
    """Own-state-only JSON state store."""

    def __init__(self, root: str | Path, *, policy: PolicyStore) -> None:
        self.root = Path(root)
        self.policy = policy

    def state_path(self, service_id: str) -> Path:
        return self.root / f"{_safe_service_id(service_id)}.state.json"

    def write_state(
        self,
        *,
        requester: str,
        target_service: str,
        payload: Mapping[str, Any],
    ) -> Path:
        self.policy.require(requester, "state.write.own")
        if requester != target_service:
            decision = self.policy.authorize(requester, "state.write.own")
            raise AccessDenied(
                type(decision)(
                    subject=decision.subject,
                    capability=decision.capability,
                    allowed=False,
                    reason=f"state.write.own cannot write target: {target_service}",
                    audit_required=decision.audit_required,
                )
            )
        path = self.state_path(target_service)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    def read_state(self, service_id: str) -> dict[str, Any] | None:
        try:
            value = json.loads(self.state_path(service_id).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None


def _safe_service_id(service_id: str) -> str:
    value = service_id.strip()
    if not SERVICE_ID_PATTERN.fullmatch(value):
        raise ValueError("service id must contain only letters, numbers, hyphen, or underscore")
    return value
