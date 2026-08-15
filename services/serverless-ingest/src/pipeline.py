"""Shared conventions for the serverless CUSTBILL pipeline.

Key layout in the ingest bucket (all prefixes namespaced so tenants can run
concurrently, exactly like the NS-scoped legacy harness):

    landing/<ns>/CUSTBILL_<NS>_<nnn>.dat     input, dropped by the feed
    parsed/<ns>/CUSTBILL_<NS>_<nnn>.psv      parse output (byte-identical to legacy)
    reports/<ns>/finance_billing_<stamp>.csv finance report (and .xls copy)
"""

from __future__ import annotations

import os

LANDING_PREFIX = "landing"
PARSED_PREFIX = "parsed"
REPORTS_PREFIX = "reports"


def env(name: str) -> str:
    """Read a required environment variable set by the Terraform stack."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required environment variable {name}")
    return value


def namespace_from_key(key: str) -> str:
    """Extract the namespace from a pipeline object key.

    >>> namespace_from_key("landing/demo/CUSTBILL_DEMO_001.dat")
    'demo'
    """
    parts = key.split("/")
    if len(parts) < 3:
        raise ValueError(f"key {key!r} is not <prefix>/<ns>/<file>")
    return parts[1]


def landing_key(ns: str, filename: str) -> str:
    return f"{LANDING_PREFIX}/{ns}/{filename}"


def parsed_key(ns: str, filename: str) -> str:
    return f"{PARSED_PREFIX}/{ns}/{filename}"


def report_key(ns: str, filename: str) -> str:
    return f"{REPORTS_PREFIX}/{ns}/{filename}"
