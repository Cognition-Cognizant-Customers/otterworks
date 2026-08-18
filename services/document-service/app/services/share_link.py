"""Share-link tokens for read-only document links.

A share link is stateless: the token is derived from the document id so any
replica can validate a link without a shared lookup table. Replicas therefore
have to share the keying material (``SHARE_LINK_SECRET``) rather than a table.
"""

from __future__ import annotations

import hmac
import os
import secrets
from hashlib import sha256

import structlog

logger = structlog.get_logger()

TOKEN_LENGTH = 16

_fallback_secret: str | None = None


def _process_fallback_secret() -> str:
    """Key material for an unconfigured process.

    Fail closed: a value an attacker could read from the source would make every
    link forgeable, so an unconfigured process gets a random secret instead. It
    is generated once per process so the service still validates the tokens it
    minted itself; links only survive a restart (or work across replicas) once
    SHARE_LINK_SECRET is configured.
    """
    global _fallback_secret
    if _fallback_secret is None:
        _fallback_secret = secrets.token_hex(32)
        logger.warning("share_link_secret_missing")
    return _fallback_secret


class ShareLinkService:
    """Mints and validates read-only share tokens for documents."""

    def __init__(self, secret: str | None = None):
        self.secret = (
            secret or os.environ.get("SHARE_LINK_SECRET") or _process_fallback_secret()
        )

    def mint_token(self, document_id: str) -> str:
        """Return the share token for a document."""
        digest = hmac.new(
            self.secret.encode(), document_id.encode(), sha256
        ).hexdigest()
        return digest[:TOKEN_LENGTH]

    def verify_token(self, document_id: str, token: str) -> bool:
        """Return True when the token is a valid share token for the document."""
        expected = self.mint_token(document_id)
        ok = hmac.compare_digest(expected, token)
        if not ok:
            logger.info("share_token_rejected", document_id=document_id)
        return ok
