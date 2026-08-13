import unittest

from kareem.tools.web import _refuse_internal_url
from kareem.web.server import _ALLOWED_ORIGINS, _origin_is_allowed, HOST, PORT


class OriginCheckTests(unittest.TestCase):
    """_origin_is_allowed gates the WebSocket handshake and state-changing
    REST requests — see kareem/web/server.py's _ALLOWED_ORIGINS comment for
    why a malicious page in another browser tab is the actual threat this
    closes (localhost-only binding alone does not stop it)."""

    def test_kareem_own_origin_allowed(self):
        self.assertTrue(_origin_is_allowed(f"http://{HOST}:{PORT}"))

    def test_localhost_variant_allowed(self):
        self.assertTrue(_origin_is_allowed(f"http://localhost:{PORT}"))

    def test_missing_origin_allowed(self):
        # Non-browser clients send no Origin header at all; they can't be
        # triggered by visiting a malicious webpage, so this is safe to allow.
        self.assertTrue(_origin_is_allowed(None))

    def test_foreign_origin_rejected(self):
        self.assertFalse(_origin_is_allowed("https://evil.example"))

    def test_wrong_port_on_localhost_rejected(self):
        self.assertFalse(_origin_is_allowed(f"http://{HOST}:9999"))

    def test_https_variant_of_own_origin_rejected(self):
        # Kareem only ever serves plain http on localhost; a scheme mismatch
        # should not be silently accepted.
        self.assertFalse(_origin_is_allowed(f"https://{HOST}:{PORT}"))

    def test_allowed_origins_set_is_exactly_the_two_documented_forms(self):
        self.assertEqual(
            _ALLOWED_ORIGINS, {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}
        )


class FetchPageSsrfGuardTests(unittest.TestCase):
    """_refuse_internal_url: fetch_page must not be usable to reach
    loopback/private/link-local addresses. IP literals are used (not
    hostnames needing real DNS) so these tests are deterministic offline."""

    def test_loopback_ip_refused(self):
        self.assertIsNotNone(_refuse_internal_url("http://127.0.0.1/admin"))

    def test_localhost_hostname_refused(self):
        self.assertIsNotNone(_refuse_internal_url("http://localhost/admin"))

    def test_link_local_cloud_metadata_address_refused(self):
        self.assertIsNotNone(
            _refuse_internal_url("http://169.254.169.254/latest/meta-data/")
        )

    def test_private_192_range_refused(self):
        self.assertIsNotNone(_refuse_internal_url("http://192.168.1.1/"))

    def test_private_10_range_refused(self):
        self.assertIsNotNone(_refuse_internal_url("http://10.0.0.5/"))

    def test_public_ip_literal_allowed(self):
        self.assertIsNone(_refuse_internal_url("http://8.8.8.8/"))

    def test_non_http_scheme_refused(self):
        self.assertIsNotNone(_refuse_internal_url("ftp://example.com/file"))

    def test_malformed_url_refused_without_crashing(self):
        self.assertIsNotNone(_refuse_internal_url("not a url at all"))

    def test_empty_string_refused_without_crashing(self):
        self.assertIsNotNone(_refuse_internal_url(""))


if __name__ == "__main__":
    unittest.main()
