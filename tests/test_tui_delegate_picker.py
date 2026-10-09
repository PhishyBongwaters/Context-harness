"""TUI: delegate model picker (ctrl+u) — headless."""
import unittest
from unittest.mock import MagicMock

from harness.tui.app import HarnessApp, ProviderScreen, ModelScreen


class TestDelegatePicker(unittest.TestCase):
    def test_binding_exists(self):
        def _key(b):
            return b.key if hasattr(b, "key") else b[0]
        keys = [_key(b) for b in HarnessApp.BINDINGS]
        self.assertIn("ctrl+u", keys)

    def test_provider_screen_delegate_mode(self):
        s = ProviderScreen(current="x", entries=[], for_delegate=True)
        self.assertTrue(s._for_delegate)
        s2 = ProviderScreen(current="x", entries=[])
        self.assertFalse(s2._for_delegate)

    def test_model_screen_delegate_mode(self):
        m = ModelScreen("nvidia", "Qwen", for_delegate=True)
        self.assertTrue(m._for_delegate)

    def test_set_delegate_model(self):
        app = HarnessApp(MagicMock(), MagicMock(), MagicMock())
        cfg = MagicMock()
        cfg.delegate_model = None
        app._cfg = cfg
        loop = MagicMock()
        loop._delegate_model = None
        app._agent_loop = loop
        # avoid config file write: make config_path fail gracefully
        app._log = MagicMock()
        with unittest.mock.patch(
                "harness.config.config_path",
                side_effect=OSError("nope")):
            app._set_delegate_model("nvidia", "Qwen3")
        self.assertEqual(cfg.delegate_model, "nvidia/Qwen3")
        self.assertEqual(loop._delegate_model, "nvidia/Qwen3")


if __name__ == "__main__":
    unittest.main()
