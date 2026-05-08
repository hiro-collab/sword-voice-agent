from pathlib import Path
from unittest import TestCase

from sword_voice_agent.system.access_control import PolicyStore


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_ROOT = REPO_ROOT / "policies" / "access"


class AccessControlTest(TestCase):
    def setUp(self) -> None:
        self.policy = PolicyStore(POLICY_ROOT)

    def test_expected_allow_decisions(self) -> None:
        cases = [
            ("thought_core_api", "memory.write.candidate", "failure_patterns"),
            ("deep_core", "web.search", None),
            ("deep_core", "memory.write.candidate", "design_notes"),
            ("mediapipe_camera_hub_stack", "reflex.emit", None),
            ("environment_state_server", "environment.observe", None),
            ("home_assistant_bridge", "home.preview", None),
            ("memory_core", "memory.write.confirmed", "failure_patterns"),
        ]

        for subject, capability, scope in cases:
            with self.subTest(subject=subject, capability=capability, scope=scope):
                decision = self.policy.authorize(
                    subject,
                    capability,
                    resource=(
                        {"type": "memory_scope", "scope": scope}
                        if scope is not None
                        else None
                    ),
                )
                self.assertTrue(decision.allowed, decision.reason)

    def test_expected_deny_decisions(self) -> None:
        cases = [
            ("thought_core_api", "memory.write.confirmed", "failure_patterns"),
            ("thought_core_api", "secrets.use.adapter", None),
            ("deep_core", "home.execute.low_risk", None),
            ("mediapipe_camera_hub_stack", "home.execute.low_risk", None),
            ("environment_state_server", "home.execute.low_risk", None),
            ("touchdesigner_control_gui", "memory.write.confirmed", "user_preferences"),
            ("memory_core", "secrets.use.adapter", None),
        ]

        for subject, capability, scope in cases:
            with self.subTest(subject=subject, capability=capability, scope=scope):
                decision = self.policy.authorize(
                    subject,
                    capability,
                    resource=(
                        {"type": "memory_scope", "scope": scope}
                        if scope is not None
                        else None
                    ),
                )
                self.assertFalse(decision.allowed)

    def test_secrets_scope_is_never_memory_readable(self) -> None:
        decision = self.policy.authorize(
            "memory_core",
            "memory.read.semantic",
            resource={"type": "memory_scope", "scope": "secrets"},
        )

        self.assertFalse(decision.allowed)
        self.assertIn("secrets", decision.reason)

    def test_high_risk_action_requires_user_confirmation(self) -> None:
        decision = self.policy.authorize(
            "home_assistant_bridge",
            "home.execute.requires_approval",
        )
        approved = self.policy.authorize(
            "home_assistant_bridge",
            "home.execute.requires_approval",
            context={"user_confirmed": True},
        )

        self.assertFalse(decision.allowed)
        self.assertTrue(decision.approval_required)
        self.assertTrue(approved.allowed)
