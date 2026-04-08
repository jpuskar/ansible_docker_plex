#!/usr/bin/env python3
"""Tests for godaddy_ddns.py"""

import base64
import io
import json
import socket
import time
import unittest
from types import FrameType
from unittest.mock import MagicMock, mock_open, patch

import godaddy_ddns


class TestGetPublicIp(unittest.TestCase):
    @patch("godaddy_ddns.urlopen")
    def test_returns_stripped_ip(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"  203.0.113.42\n"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        self.assertEqual(godaddy_ddns.get_public_ip(), "203.0.113.42")


class TestReadK8sSecret(unittest.TestCase):
    @patch("godaddy_ddns.urlopen")
    @patch(
        "builtins.open",
        side_effect=[
            io.StringIO("fake-token"),
            io.StringIO("godaddy-ddns"),
        ],
    )
    @patch("godaddy_ddns.ssl.create_default_context")
    def test_decodes_secret_data(
        self, mock_ssl: MagicMock, mock_file: MagicMock, mock_urlopen: MagicMock
    ) -> None:
        secret_body = {
            "data": {
                "GODADDY_DOMAIN": base64.b64encode(b"example.com").decode(),
                "GODADDY_API_KEY": base64.b64encode(b"mykey").decode(),
                "GODADDY_API_SECRET": base64.b64encode(b"mysecret").decode(),
            }
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(secret_body).encode("utf-8")
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        result = godaddy_ddns.read_k8s_secret("godaddy-config")

        self.assertEqual(result["GODADDY_DOMAIN"], "example.com")
        self.assertEqual(result["GODADDY_API_KEY"], "mykey")
        self.assertEqual(result["GODADDY_API_SECRET"], "mysecret")


class TestUpdateDns(unittest.TestCase):
    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    @patch("godaddy_ddns.socket.gethostbyname", return_value="203.0.113.1")
    def test_successful_update(
        self, mock_dns: MagicMock, mock_ip: MagicMock, mock_urlopen: MagicMock
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, last = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, 0.0)
        self.assertTrue(ok)
        self.assertGreater(last, 0.0)

        # Verify the API was called with PUT
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertIn("example.com", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "sso-key key:secret")

    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    @patch("godaddy_ddns.socket.gethostbyname", return_value="203.0.113.42")
    def test_no_update_when_ip_matches(
        self, mock_dns: MagicMock, mock_ip: MagicMock
    ) -> None:
        ok, last = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, 0.0)
        self.assertTrue(ok)
        # last_update should NOT be updated (no API write)
        self.assertEqual(last, 0.0)

    def test_invalid_hostname_single_label(self) -> None:
        ok, _ = godaddy_ddns.update_dns("localhost", "key", "secret", 3600, 0.0)
        self.assertFalse(ok)

    @patch("godaddy_ddns.get_public_ip", return_value="999.0.0.1")
    @patch(
        "godaddy_ddns.socket.gethostbyname",
        side_effect=socket.gaierror("not found"),
    )
    def test_invalid_ip_address(self, mock_dns: MagicMock, mock_ip: MagicMock) -> None:
        ok, _ = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, 0.0)
        self.assertFalse(ok)

    @patch("godaddy_ddns.get_public_ip", side_effect=OSError("network down"))
    def test_public_ip_failure(self, mock_ip: MagicMock) -> None:
        ok, _ = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, 0.0)
        self.assertFalse(ok)

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    @patch("godaddy_ddns.socket.gethostbyname", return_value="203.0.113.1")
    def test_http_403_returns_false(
        self, mock_dns: MagicMock, mock_ip: MagicMock, mock_urlopen: MagicMock
    ) -> None:
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            "https://api.godaddy.com/...", 403, "Forbidden", {}, None
        )
        ok, _ = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, 0.0)
        self.assertFalse(ok)

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    @patch("godaddy_ddns.socket.gethostbyname", return_value="203.0.113.1")
    def test_http_429_returns_false(
        self, mock_dns: MagicMock, mock_ip: MagicMock, mock_urlopen: MagicMock
    ) -> None:
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            "https://api.godaddy.com/...", 429, "Too Many Requests", {}, None
        )
        ok, _ = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, 0.0)
        self.assertFalse(ok)

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    @patch(
        "godaddy_ddns.socket.gethostbyname",
        side_effect=socket.gaierror("not found"),
    )
    def test_dns_lookup_failure_still_updates(
        self, mock_dns: MagicMock, mock_ip: MagicMock, mock_urlopen: MagicMock
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, last = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, 0.0)
        self.assertTrue(ok)
        self.assertGreater(last, 0.0)

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    @patch("godaddy_ddns.socket.gethostbyname", return_value="203.0.113.1")
    def test_domain_only_inserts_at_record(
        self, mock_dns: MagicMock, mock_ip: MagicMock, mock_urlopen: MagicMock
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, _ = godaddy_ddns.update_dns("example.com", "key", "secret", 3600, 0.0)
        self.assertTrue(ok)

        req = mock_urlopen.call_args[0][0]
        self.assertIn("/records/A/@", req.full_url)

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    @patch("godaddy_ddns.socket.gethostbyname", return_value="203.0.113.1")
    def test_cooldown_blocks_rapid_updates(
        self, mock_dns: MagicMock, mock_ip: MagicMock, mock_urlopen: MagicMock
    ) -> None:
        # Simulate last update was just now
        recent = time.time()
        ok, last = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, recent)
        self.assertFalse(ok)
        self.assertEqual(last, recent)
        # urlopen should NOT have been called (cooldown blocked the write)
        mock_urlopen.assert_not_called()

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    @patch("godaddy_ddns.socket.gethostbyname", return_value="203.0.113.1")
    def test_cooldown_allows_update_after_expiry(
        self, mock_dns: MagicMock, mock_ip: MagicMock, mock_urlopen: MagicMock
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        # Simulate last update was long ago
        old = time.time() - godaddy_ddns.UPDATE_COOLDOWN - 1
        ok, last = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, old)
        self.assertTrue(ok)
        self.assertGreater(last, old)
        mock_urlopen.assert_called_once()


class TestHandleSignal(unittest.TestCase):
    def test_sets_shutdown_flag(self) -> None:
        godaddy_ddns.shutdown = False
        godaddy_ddns.handle_signal(15, None)
        self.assertTrue(godaddy_ddns.shutdown)
        godaddy_ddns.shutdown = False  # reset


class TestMain(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {"GODADDY_SECRET_NAME": "test-secret", "GODADDY_INTERVAL": "1"},
    )
    @patch("godaddy_ddns.read_k8s_secret")
    def test_exits_on_missing_secret_keys(self, mock_read: MagicMock) -> None:
        mock_read.return_value = {"GODADDY_DOMAIN": "example.com"}
        result = godaddy_ddns.main()
        self.assertEqual(result, 1)

    @patch.dict(
        "os.environ",
        {"GODADDY_SECRET_NAME": "test-secret", "GODADDY_INTERVAL": "1"},
    )
    @patch("godaddy_ddns.read_k8s_secret", side_effect=Exception("no SA"))
    def test_exits_on_k8s_read_failure(self, mock_read: MagicMock) -> None:
        result = godaddy_ddns.main()
        self.assertEqual(result, 1)

    @patch.dict(
        "os.environ",
        {"GODADDY_SECRET_NAME": "test-secret", "GODADDY_INTERVAL": "1"},
    )
    @patch("godaddy_ddns.read_k8s_secret")
    @patch("godaddy_ddns.update_dns", return_value=(True, 1000.0))
    @patch("godaddy_ddns.time.sleep")
    def test_runs_loop_and_shuts_down(
        self,
        mock_sleep: MagicMock,
        mock_update: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        mock_read.return_value = {
            "GODADDY_DOMAIN": "example.com",
            "GODADDY_API_KEY": "key",
            "GODADDY_API_SECRET": "secret",
        }

        # Shut down after first sleep
        def stop_after_one(*args: object) -> None:
            godaddy_ddns.shutdown = True

        mock_sleep.side_effect = stop_after_one

        godaddy_ddns.shutdown = False
        result = godaddy_ddns.main()
        self.assertEqual(result, 0)
        mock_update.assert_called_once()
        godaddy_ddns.shutdown = False  # reset

    @patch.dict(
        "os.environ",
        {"GODADDY_SECRET_NAME": "test-secret", "GODADDY_INTERVAL": "1"},
    )
    @patch("godaddy_ddns.read_k8s_secret")
    @patch("godaddy_ddns.update_dns", return_value=(False, 0.0))
    @patch("godaddy_ddns.time.sleep")
    def test_backoff_increases_on_failure(
        self,
        mock_sleep: MagicMock,
        mock_update: MagicMock,
        mock_read: MagicMock,
    ) -> None:
        mock_read.return_value = {
            "GODADDY_DOMAIN": "example.com",
            "GODADDY_API_KEY": "key",
            "GODADDY_API_SECRET": "secret",
        }
        call_count = 0

        def stop_after_two(*args: object) -> None:
            nonlocal call_count
            call_count += 1
            # First loop sleeps BACKOFF_INITIAL times (60), second sleeps 120 times
            # Stop during second backoff cycle
            if call_count > 60:
                godaddy_ddns.shutdown = True

        mock_sleep.side_effect = stop_after_two

        godaddy_ddns.shutdown = False
        result = godaddy_ddns.main()
        self.assertEqual(result, 0)
        # Should have called update_dns twice (first at 60s backoff, then 120s)
        self.assertEqual(mock_update.call_count, 2)
        godaddy_ddns.shutdown = False  # reset


if __name__ == "__main__":
    unittest.main()
