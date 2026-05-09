from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


class PolicyError(ValueError):
    """Raised when policy files or requests are malformed."""


class AccessDenied(PermissionError):
    """Raised when a policy decision denies an operation."""

    def __init__(self, decision: "AuthorizationDecision") -> None:
        super().__init__(decision.reason)
        self.decision = decision


@dataclass(frozen=True)
class AuthorizationDecision:
    subject: str
    capability: str
    allowed: bool
    reason: str
    audit_required: bool = True
    approval_required: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "capability": self.capability,
            "allowed": self.allowed,
            "reason": self.reason,
            "audit_required": self.audit_required,
            "approval_required": self.approval_required,
        }


class PolicyStore:
    """File-backed capability policy for local subsystem tests."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.services = _load_json(self.root / "services.json")["services"]
        self.capabilities = _load_json(self.root / "capabilities.json")["capabilities"]
        self.memory_scopes = _load_json(self.root / "memory-scopes.json")["scopes"]
        self.action_rules = _load_json(self.root / "action-approval.json")["rules"]

    def identity_for(self, subject: str) -> str:
        policy = self.services.get(subject)
        if isinstance(policy, Mapping):
            return str(policy["identity"])
        for service_policy in self.services.values():
            if str(service_policy.get("identity")) == subject:
                return subject
        raise PolicyError(f"unknown service or identity: {subject}")

    def service_policy_for(self, subject: str) -> Mapping[str, Any]:
        policy = self.services.get(subject)
        if isinstance(policy, Mapping):
            return policy
        for service_policy in self.services.values():
            if str(service_policy.get("identity")) == subject:
                return service_policy
        raise PolicyError(f"unknown service or identity: {subject}")

    def authorize(
        self,
        subject: str,
        capability: str,
        *,
        resource: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> AuthorizationDecision:
        resource = resource or {}
        context = context or {}
        policy = self.service_policy_for(subject)
        identity = str(policy["identity"])

        if capability not in self.capabilities:
            return _deny(identity, capability, f"unknown capability: {capability}")
        if capability in set(policy.get("denied", [])):
            return _deny(identity, capability, f"explicitly denied: {capability}")
        if capability not in set(policy.get("capabilities", [])):
            return _deny(identity, capability, f"missing capability: {capability}")

        scoped = self._authorize_scoped_memory(
            identity,
            capability,
            resource=resource,
            context=context,
        )
        if scoped is not None:
            return scoped

        action = self._authorize_home_action(
            identity,
            capability,
            context=context,
        )
        if action is not None:
            return action

        return AuthorizationDecision(
            subject=identity,
            capability=capability,
            allowed=True,
            reason="allowed by capability policy",
        )

    def require(
        self,
        subject: str,
        capability: str,
        *,
        resource: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> AuthorizationDecision:
        decision = self.authorize(
            subject,
            capability,
            resource=resource,
            context=context,
        )
        if not decision.allowed:
            raise AccessDenied(decision)
        return decision

    def _authorize_scoped_memory(
        self,
        identity: str,
        capability: str,
        *,
        resource: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> AuthorizationDecision | None:
        if not capability.startswith("memory."):
            return None
        scope_name = str(resource.get("scope") or "")
        if not scope_name:
            return _deny(identity, capability, "memory scope is required")
        scope = self.memory_scopes.get(scope_name)
        if not isinstance(scope, Mapping):
            return _deny(identity, capability, f"unknown memory scope: {scope_name}")
        if scope_name == "secrets":
            return _deny(identity, capability, "secrets are not memory")

        if capability.startswith("memory.read."):
            if identity not in set(scope.get("readable_by", [])):
                return _deny(identity, capability, f"scope not readable: {scope_name}")
            return AuthorizationDecision(
                subject=identity,
                capability=capability,
                allowed=True,
                reason=f"scope readable: {scope_name}",
            )

        if capability == "memory.write.candidate":
            return AuthorizationDecision(
                subject=identity,
                capability=capability,
                allowed=True,
                reason=f"candidate write allowed for scope: {scope_name}",
            )

        if capability == "memory.write.confirmed":
            if identity not in set(scope.get("writable_by", [])):
                return _deny(identity, capability, f"scope not writable: {scope_name}")
            if bool(scope.get("write_requires_confirmation")) and not bool(
                context.get("user_confirmed")
            ):
                return AuthorizationDecision(
                    subject=identity,
                    capability=capability,
                    allowed=False,
                    reason=f"user confirmation required for scope: {scope_name}",
                    approval_required=True,
                )
            return AuthorizationDecision(
                subject=identity,
                capability=capability,
                allowed=True,
                reason=f"confirmed write allowed for scope: {scope_name}",
            )
        return None

    def _authorize_home_action(
        self,
        identity: str,
        capability: str,
        *,
        context: Mapping[str, Any],
    ) -> AuthorizationDecision | None:
        if not capability.startswith("home.execute."):
            return None
        rule = next(
            (
                candidate
                for candidate in self.action_rules
                if candidate.get("capability") == capability
            ),
            None,
        )
        if not isinstance(rule, Mapping):
            return _deny(identity, capability, f"no action rule: {capability}")
        if rule.get("approval") == "explicit_user_confirmation" and not bool(
            context.get("user_confirmed")
        ):
            return AuthorizationDecision(
                subject=identity,
                capability=capability,
                allowed=False,
                reason=f"user confirmation required for action: {capability}",
                approval_required=True,
            )
        return AuthorizationDecision(
            subject=identity,
            capability=capability,
            allowed=True,
            reason=f"action allowed by rule: {capability}",
        )


def _deny(subject: str, capability: str, reason: str) -> AuthorizationDecision:
    return AuthorizationDecision(
        subject=subject,
        capability=capability,
        allowed=False,
        reason=reason,
    )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise PolicyError(f"{path} must contain a JSON object")
    return value
