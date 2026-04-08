#!/usr/bin/env python3
"""Tests for godaddy_ddns.py"""

import base64
import io
import json
import socket
import struct
import unittest
from unittest.mock import MagicMock, patch

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


class TestBuildDnsQuery(unittest.TestCase):
    def test_builds_valid_query(self) -> None:
        pkt = godaddy_ddns._build_dns_query("site.example.com")
        # Header is 12 bytes, then labels, then null, then QTYPE+QCLASS (4 bytes)
        self.assertTrue(len(pkt) > 12)
        # Check header: QDCOUNT=1
        qdcount = struct.unpack(">H", pkt[4:6])[0]
        self.assertEqual(qdcount, 1)


class TestParseDnsResponse(unittest.TestCase):
    def _make_response(self, ip_bytes: bytes) -> bytes:
        """Build a minimal DNS response with one A record."""
        # Header: ID, flags=0x8180 (response), QDCOUNT=1, ANCOUNT=1
        header = struct.pack(">HHHHHH", 0xABCD, 0x8180, 1, 1, 0, 0)
        # Question: site.example.com A IN
        question = b"\x04site\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
        # Answer: pointer to name, TYPE=A, CLASS=IN, TTL=3600, RDLENGTH=4, RDATA
        answer = struct.pack(">H", 0xC00C)  # pointer to offset 12
        answer += struct.pack(">HHIH", 1, 1, 3600, 4)
        answer += ip_bytes
        return header + question + answer

    def test_parses_a_record(self) -> None:
        data = self._make_response(bytes([203, 0, 113, 42]))
        self.assertEqual(godaddy_ddns._parse_dns_response(data), "203.0.113.42")

    def test_returns_none_on_empty(self) -> None:
        self.assertIsNone(godaddy_ddns._parse_dns_response(b""))

    def test_returns_none_on_truncated(self) -> None:
        self.assertIsNone(godaddy_ddns._parse_dns_response(b"\x00" * 11))


class TestResolveExternal(unittest.TestCase):
    @patch("godaddy_ddns.socket.socket")
    def test_returns_ip_on_success(
        self, mock_sock_cls: MagicMock
    ) -> None:
        # Build a fake DNS response
        header = struct.pack(">HHHHHH", 0xABCD, 0x8180, 1, 1, 0, 0)
        question = b"\x04site\x07example\x03com\x00" + struct.pack(">HH", 1, 1)
        answer = struct.pack(">H", 0xC00C) + struct.pack(">HHIH", 1, 1, 3600, 4)
        answer += bytes([203, 0, 113, 42])
        response = header + question + answer

        mock_sock = MagicMock()
        mock_sock.recvfrom.return_value = (response, ("216.69.185.1", 53))
        mock_sock_cls.return_value = mock_sock

        result = godaddy_ddns.resolve_external("site.example.com")
        self.assertEqual(result, "203.0.113.42")

    @patch("godaddy_ddns.socket.socket")
    def test_returns_none_on_all_failures(
        self, mock_sock_cls: MagicMock
    ) -> None:
        mock_sock = MagicMock()
        mock_sock.sendto.side_effect = socket.error("timeout")
        mock_sock_cls.return_value = mock_sock
        result = godaddy_ddns.resolve_external("site.example.com")
        self.assertIsNone(result)


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
    def _mock_urlopen_ok(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

    # --- IP-based cooldown (last_ip tracking) ---

    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    def test_skips_update_when_public_ip_matches_last_written(
        self, mock_ip: MagicMock
    ) -> None:
        ok, last = godaddy_ddns.update_dns(
            "home.example.com", "key", "secret", 3600, "203.0.113.42"
        )
        self.assertTrue(ok)
        self.assertEqual(last, "203.0.113.42")

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.resolve_external", return_value="203.0.113.1")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    def test_updates_when_public_ip_differs_from_last_written(
        self,
        mock_ip: MagicMock,
        mock_resolve: MagicMock,
        mock_urlopen: MagicMock,
    ) -> None:
        self._mock_urlopen_ok(mock_urlopen)
        ok, last = godaddy_ddns.update_dns(
            "home.example.com", "key", "secret", 3600, "10.0.0.1"
        )
        self.assertTrue(ok)
        self.assertEqual(last, "203.0.113.42")

    # --- GoDaddy authoritative DNS check ---

    @patch("godaddy_ddns.resolve_external", return_value="203.0.113.42")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    def test_no_update_when_external_dns_matches(
        self, mock_ip: MagicMock, mock_resolve: MagicMock
    ) -> None:
        ok, last = godaddy_ddns.update_dns(
            "home.example.com", "key", "secret", 3600, ""
        )
        self.assertTrue(ok)
        self.assertEqual(last, "203.0.113.42")

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.resolve_external", return_value=None)
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    def test_updates_when_external_dns_returns_none(
        self,
        mock_ip: MagicMock,
        mock_resolve: MagicMock,
        mock_urlopen: MagicMock,
    ) -> None:
        self._mock_urlopen_ok(mock_urlopen)
        ok, last = godaddy_ddns.update_dns(
            "home.example.com", "key", "secret", 3600, ""
        )
        self.assertTrue(ok)
        self.assertEqual(last, "203.0.113.42")

    # --- Successful update ---

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.resolve_external", return_value="10.0.0.1")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    def test_successful_update_records_ip(
        self,
        mock_ip: MagicMock,
        mock_resolve: MagicMock,
        mock_urlopen: MagicMock,
    ) -> None:
        self._mock_urlopen_ok(mock_urlopen)
        ok, last = godaddy_ddns.update_dns(
            "home.example.com", "key", "secret", 3600, ""
        )
        self.assertTrue(ok)
        self.assertEqual(last, "203.0.113.42")

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "PUT")
        self.assertIn("example.com", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "sso-key key:secret")

    # --- Error handling ---

    def test_invalid_hostname_single_label(self) -> None:
        ok, _ = godaddy_ddns.update_dns("localhost", "key", "secret", 3600, "")
        self.assertFalse(ok)

    @patch("godaddy_ddns.get_public_ip", return_value="999.0.0.1")
    def test_invalid_ip_address(self, mock_ip: MagicMock) -> None:
        ok, _ = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, "")
        self.assertFalse(ok)

    @patch("godaddy_ddns.get_public_ip", side_effect=OSError("network down"))
    def test_public_ip_failure(self, mock_ip: MagicMock) -> None:
        ok, _ = godaddy_ddns.update_dns("home.example.com", "key", "secret", 3600, "")
        self.assertFalse(ok)

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.resolve_external", return_value="10.0.0.1")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    def test_http_403_returns_false(
        self,
        mock_ip: MagicMock,
        mock_resolve: MagicMock,
        mock_urlopen: MagicMock,
    ) -> None:
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            "https://api.godaddy.com/...", 403, "Forbidden", {}, None
        )
        ok, last = godaddy_ddns.update_dns(
            "home.example.com", "key", "secret", 3600, ""
        )
        self.assertFalse(ok)
        self.assertEqual(last, "")

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.resolve_external", return_value="10.0.0.1")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    def test_http_429_returns_false(
        self,
        mock_ip: MagicMock,
        mock_resolve: MagicMock,
        mock_urlopen: MagicMock,
    ) -> None:
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            "https://api.godaddy.com/...", 429, "Too Many Requests", {}, None
        )
        ok, _ = godaddy_ddns.update_dns(
            "home.example.com", "key", "secret", 3600, ""
        )
        self.assertFalse(ok)

    # --- @ record for bare domain ---

    @patch("godaddy_ddns.urlopen")
    @patch("godaddy_ddns.resolve_external", return_value="10.0.0.1")
    @patch("godaddy_ddns.get_public_ip", return_value="203.0.113.42")
    def test_domain_only_inserts_at_record(
        self,
        mock_ip: MagicMock,
        mock_resolve: MagicMock,
        mock_urlopen: MagicMock,
    ) -> None:
        self._mock_urlopen_ok(mock_urlopen)
        ok, _ = godaddy_ddns.update_dns("example.com", "key", "secret", 3600, "")
        self.assertTrue(ok)

        req = mock_urlopen.call_args[0][0]
        self.assertIn("/records/A/@", req.full_url)


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
    @patch("godaddy_ddns.update_dns", return_value=(True, "203.0.113.42"))
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
    @patch("godaddy_ddns.update_dns", return_value=(False, ""))
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
            if call_count > 60:
                godaddy_ddns.shutdown = True

        mock_sleep.side_effect = stop_after_two

        godaddy_ddns.shutdown = False
        result = godaddy_ddns.main()
        self.assertEqual(result, 0)
        self.assertEqual(mock_update.call_count, 2)
        godaddy_ddns.shutdown = False  # reset

    @patch.dict(
        "os.environ",
        {"GODADDY_SECRET_NAME": "test-secret", "GODADDY_INTERVAL": "1"},
    )
    @patch("godaddy_ddns.read_k8s_secret")
    @patch("godaddy_ddns.update_dns")
    @patch("godaddy_ddns.time.sleep")
    def test_last_ip_passed_between_iterations(
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
        # First call returns new IP, second call uses it
        mock_update.side_effect = [
            (True, "203.0.113.42"),
            (True, "203.0.113.42"),
        ]
        call_count = 0

        def stop_after_second_update(*args: object) -> None:
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                godaddy_ddns.shutdown = True

        mock_sleep.side_effect = stop_after_second_update

        godaddy_ddns.shutdown = False
        godaddy_ddns.main()
        # Verify second call received the IP from the first call
        second_call = mock_update.call_args_list[1]
        self.assertEqual(second_call[1].get("last_ip") or second_call[0][4], "203.0.113.42")
        godaddy_ddns.shutdown = False  # reset


if __name__ == "__main__":
    unittest.main()
