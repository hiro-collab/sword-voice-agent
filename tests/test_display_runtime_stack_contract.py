import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STACK_START_SCRIPT = (
    ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"
)


def read_stack_start_script() -> str:
    return STACK_START_SCRIPT.read_text(encoding="utf-8-sig")


class DisplayRuntimeStackContractTest(unittest.TestCase):
    def test_stack_passes_thought_core_endpoint_to_display_runtime(self) -> None:
        stack_start = read_stack_start_script()
        display_spec_index = stack_start.index('-Name "touchdesigner_control_gui"')
        display_spec = stack_start[display_spec_index:]

        self.assertIn('"--thought-core-host"', display_spec)
        self.assertIn("$ThoughtCoreClientHost", display_spec)
        self.assertIn('"--thought-core-port"', display_spec)
        self.assertIn("[string]$ThoughtCorePort", display_spec)


if __name__ == "__main__":
    unittest.main()
