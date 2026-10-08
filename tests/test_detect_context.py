"""Red-first tests for detect_context_window.

Based on official llama.cpp docs:
- /props returns {"default_generation_settings": {"n_ctx": ...}, ...}
- /v1/models returns {"data": [{"id": ..., "meta": {"n_ctx": ...}}]}

Prior art: pi-llama-cpp, maverobot/qwen36-mtp opencode workaround.
"""
import json
import unittest
from unittest.mock import patch, MagicMock
from io import BytesIO


class TestDetectContextWindow(unittest.TestCase):
    def _mock_urlopen(self, responses):
        """responses: dict mapping URL substring -> response dict."""
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            for key, resp_data in responses.items():
                if key in url:
                    body = json.dumps(resp_data).encode()
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = body
                    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
                    mock_resp.__exit__ = MagicMock(return_value=False)
                    return mock_resp
            raise ValueError(f"No mock for {url}")
        return fake_urlopen

    def test_props_nested_n_ctx(self):
        """llama.cpp /props nests n_ctx under default_generation_settings."""
        from harness.config import detect_context_window
        props_resp = {
            "default_generation_settings": {
                "n_ctx": 135168,
                "params": {},
            },
            "total_slots": 1,
            "model_path": "/models/test.gguf",
        }
        with patch("urllib.request.urlopen",
                   self._mock_urlopen({"/props": props_resp,
                                       "/v1/models": {"data": []}})):
            result = detect_context_window("http://localhost:8080", "test")
            self.assertEqual(result, 135168)

    def test_v1_models_meta_n_ctx(self):
        """/v1/models meta.n_ctx takes priority."""
        from harness.config import detect_context_window
        models_resp = {
            "data": [
                {"id": "test-model",
                 "meta": {"n_ctx": 32768}},
            ]
        }
        with patch("urllib.request.urlopen",
                   self._mock_urlopen({"/v1/models": models_resp})):
            result = detect_context_window("http://localhost:8080",
                                           "test-model")
            self.assertEqual(result, 32768)

    def test_v1_models_context_length_fallback(self):
        """LM Studio-style context_length field."""
        from harness.config import detect_context_window
        models_resp = {
            "data": [
                {"id": "test-model",
                 "context_length": 262144},
            ]
        }
        with patch("urllib.request.urlopen",
                   self._mock_urlopen({"/v1/models": models_resp})):
            result = detect_context_window("http://localhost:8080",
                                           "test-model")
            self.assertEqual(result, 262144)

    def test_v1_with_trailing_slash(self):
        """base_url with /v1 suffix must not produce /v1/v1/models."""
        from harness.config import detect_context_window
        models_resp = {
            "data": [{"id": "m", "meta": {"n_ctx": 65536}}]
        }
        seen_urls = []
        def fake_urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else str(req)
            seen_urls.append(url)
            body = json.dumps(models_resp).encode()
            mock_resp = MagicMock()
            mock_resp.read.return_value = body
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp
        with patch("urllib.request.urlopen", fake_urlopen):
            result = detect_context_window("http://localhost:8080/v1", "m")
            self.assertEqual(result, 65536)
            # Verify no /v1/v1 in URL
            for u in seen_urls:
                self.assertNotIn("/v1/v1", u)


if __name__ == "__main__":
    unittest.main()
