import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STACK_START_SCRIPT = (
    ROOT / "ops" / "scripts" / "home-control-stack" / "start-home-control-stack.ps1"
)


def read_stack_start_script() -> str:
    return STACK_START_SCRIPT.read_text(encoding="utf-8-sig")


class DisplayRuntimeStackContractTest(unittest.TestCase):
    def test_stack_passes_status_probe_endpoints_to_display_runtime(self) -> None:
        stack_start = read_stack_start_script()
        display_spec_index = stack_start.index('-Name "touchdesigner_control_gui"')
        display_spec = stack_start[display_spec_index:]

        self.assertIn('"--home-assistant-bridge-host"', display_spec)
        self.assertIn("$HomeAssistantBridgeClientHost", display_spec)
        self.assertIn('"--home-assistant-bridge-port"', display_spec)
        self.assertIn("[string]$HomeAssistantBridgePort", display_spec)
        self.assertIn('"--environment-state-host"', display_spec)
        self.assertIn("$EnvironmentStateClientHost", display_spec)
        self.assertIn('"--environment-state-port"', display_spec)
        self.assertIn("[string]$EnvironmentStatePort", display_spec)
        self.assertIn('"--aituber-host"', display_spec)
        self.assertIn("$AituberClientHost", display_spec)
        self.assertIn('"--aituber-port"', display_spec)
        self.assertIn("[string]$AituberPort", display_spec)
        self.assertIn('"--aituber-url"', display_spec)
        self.assertIn("$AituberProjectionVisualUrl", display_spec)
        self.assertIn('"--touchdesigner-host"', display_spec)
        self.assertIn("$TouchDesignerUdpClientHost", display_spec)
        self.assertIn('"--touchdesigner-port"', display_spec)
        self.assertIn("[string]$TouchDesignerUdpPort", display_spec)
        self.assertIn('"--thought-core-host"', display_spec)
        self.assertIn("$ThoughtCoreClientHost", display_spec)
        self.assertIn('"--thought-core-port"', display_spec)
        self.assertIn("[string]$ThoughtCorePort", display_spec)

    def test_stack_uses_client_hosts_for_status_urls(self) -> None:
        stack_start = read_stack_start_script()

        self.assertIn("$HomeAssistantBridgeClientHost", stack_start)
        self.assertIn("$EnvironmentStateClientHost", stack_start)
        self.assertIn("$AituberClientHost", stack_start)
        self.assertIn("$TouchDesignerGuiClientHost", stack_start)
        self.assertIn("$TouchDesignerUdpClientHost", stack_start)
        self.assertIn("$AituberProjectionVisualUrl", stack_start)
        self.assertNotIn('"http://127.0.0.1:$AituberPort"', stack_start)
        self.assertNotIn('"http://127.0.0.1:$TouchDesignerGuiPort', stack_start)
        self.assertNotIn('"http://127.0.0.1:$EnvironmentStatePort', stack_start)
        self.assertNotIn('"http://127.0.0.1:$HomeAssistantBridgePort', stack_start)
        self.assertNotIn('"127.0.0.1:9001"', stack_start)
        self.assertNotIn('TOUCHDESIGNER_UDP_PORT"] = "9001"', stack_start)


if __name__ == "__main__":
    unittest.main()
