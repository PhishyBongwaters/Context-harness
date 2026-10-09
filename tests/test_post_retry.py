"""429 retries with backoff in providers._post."""
import io
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from harness.providers import ProviderError, _post, _retry_delay


def _http_error(code, headers=None):
    fp = io.BytesIO(b'{"error": "rate limited"}')
    return urllib.error.HTTPError(
        "http://x", code, "Too Many Requests", headers or {}, fp)


class TestRetryDelay(unittest.TestCase):
    def test_exponential_backoff(self):
        self.assertEqual(_retry_delay({}, 0), 1.0)
        self.assertEqual(_retry_delay({}, 1), 2.0)
        self.assertEqual(_retry_delay({}, 2), 4.0)

    def test_retry_after_honored(self):
        self.assertEqual(_retry_delay({"Retry-After": "5"}, 0), 5.0)

    def test_retry_after_capped(self):
        self.assertEqual(_retry_delay({"Retry-After": "3600"}, 0), 60.0)

    def test_bad_retry_after_ignored(self):
        self.assertEqual(_retry_delay({"Retry-After": "soon"}, 0), 1.0)


class TestPostRetry(unittest.TestCase):
    def _ok_response(self):
        resp = MagicMock()
        resp.read.return_value = b'{"ok": true}'
        resp.__enter__.return_value = resp
        return resp

    def test_retries_429_then_succeeds(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise _http_error(429)
            return self._ok_response()

        with patch("urllib.request.urlopen", fake_urlopen), \
             patch("time.sleep") as sleep:
            out = _post("http://x", {}, {"a": 1})
        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_gives_up_after_retries(self):
        def fake_urlopen(req, timeout=None):
            raise _http_error(429)

        with patch("urllib.request.urlopen", fake_urlopen), \
             patch("time.sleep"):
            with self.assertRaises(ProviderError) as cm:
                _post("http://x", {}, {"a": 1})
        self.assertEqual(cm.exception.status, 429)

    def test_non_429_not_retried(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise _http_error(500)

        with patch("urllib.request.urlopen", fake_urlopen), \
             patch("time.sleep") as sleep:
            with self.assertRaises(ProviderError):
                _post("http://x", {}, {"a": 1})
        self.assertEqual(len(calls), 1)
        sleep.assert_not_called()

    def test_success_no_retry(self):
        with patch("urllib.request.urlopen",
                   return_value=self._ok_response()), \
             patch("time.sleep") as sleep:
            out = _post("http://x", {}, {"a": 1})
        self.assertEqual(out, {"ok": True})
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
