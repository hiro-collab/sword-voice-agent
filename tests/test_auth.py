from unittest import TestCase

from sword_voice_agent.adapters.auth import is_loopback_host, validate_http_url


class AuthTest(TestCase):
    def test_loopback_host_detection(self) -> None:
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertFalse(is_loopback_host(""))
        self.assertFalse(is_loopback_host("0.0.0.0"))
        self.assertFalse(is_loopback_host("192.168.1.10"))

    def test_rejects_plain_http_for_non_loopback_url(self) -> None:
        with self.assertRaises(ValueError):
            validate_http_url("http://example.com/api", label="test URL")

    def test_rejects_embedded_url_credentials(self) -> None:
        with self.assertRaises(ValueError):
            validate_http_url("https://user:pass@example.com/api", label="test URL")
