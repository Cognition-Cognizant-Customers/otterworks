#!/usr/bin/env python3
"""Unit tests for the front-door Lambda authorizer (no AWS involved).

Run: python3 scripts/tp_portal/fixture/test_authorizer.py
"""
import importlib.util
import os
import sys
import unittest

AUTHORIZER_PATH = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "..", "services", "portal-serverless", "terraform",
    "authorizer", "authorizer.py"))

spec = importlib.util.spec_from_file_location("authorizer", AUTHORIZER_PATH)
authorizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(authorizer)

TOKEN = "fixture-token-0123456789"


def event(header):
    return {"headers": {"authorization": header}} if header is not None else {"headers": {}}


class AuthorizerTest(unittest.TestCase):
    def setUp(self):
        os.environ["PORTAL_API_TOKEN"] = TOKEN

    def test_correct_bearer_token_authorized(self):
        self.assertTrue(authorizer.handler(event(f"Bearer {TOKEN}"), None)["isAuthorized"])

    def test_wrong_token_denied(self):
        self.assertFalse(authorizer.handler(event("Bearer wrong-token"), None)["isAuthorized"])

    def test_missing_header_denied(self):
        self.assertFalse(authorizer.handler(event(None), None)["isAuthorized"])
        self.assertFalse(authorizer.handler({}, None)["isAuthorized"])

    def test_empty_and_scheme_only_denied(self):
        self.assertFalse(authorizer.handler(event(""), None)["isAuthorized"])
        self.assertFalse(authorizer.handler(event("Bearer"), None)["isAuthorized"])
        self.assertFalse(authorizer.handler(event("Bearer "), None)["isAuthorized"])

    def test_wrong_scheme_denied(self):
        self.assertFalse(authorizer.handler(event(f"Basic {TOKEN}"), None)["isAuthorized"])
        self.assertFalse(authorizer.handler(event(TOKEN), None)["isAuthorized"])

    def test_missing_or_blank_expected_token_fails_closed(self):
        del os.environ["PORTAL_API_TOKEN"]
        self.assertFalse(authorizer.handler(event(f"Bearer {TOKEN}"), None)["isAuthorized"])
        self.assertFalse(authorizer.handler(event("Bearer "), None)["isAuthorized"])
        os.environ["PORTAL_API_TOKEN"] = ""
        self.assertFalse(authorizer.handler(event("Bearer x"), None)["isAuthorized"])
        self.assertFalse(authorizer.handler(event("Bearer "), None)["isAuthorized"])

    def test_non_ascii_credential_denied_not_crash(self):
        self.assertFalse(authorizer.handler(event("Bearer töken"), None)["isAuthorized"])
        self.assertFalse(authorizer.handler(event("Bearer \u00fftok"), None)["isAuthorized"])

    def test_token_is_not_a_prefix_match(self):
        self.assertFalse(authorizer.handler(event(f"Bearer {TOKEN}x"), None)["isAuthorized"])
        self.assertFalse(authorizer.handler(event(f"Bearer {TOKEN[:-1]}"), None)["isAuthorized"])


if __name__ == "__main__":
    sys.exit(unittest.main())
