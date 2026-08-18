"""Lambda authorizer for the portal HTTP API (payload v2, simple responses).

API Gateway only invokes this when the Authorization header is present (it is
the identity source; a missing header is rejected with 401 before invocation).
The header must be exactly "Bearer <token>" where <token> is the value of the
PORTAL_API_TOKEN environment variable; anything else is denied (403).
"""
import hmac
import os


def handler(event, context):
    expected = os.environ["PORTAL_API_TOKEN"]
    supplied = event.get("headers", {}).get("authorization", "")
    prefix, _, token = supplied.partition(" ")
    authorized = (
        prefix == "Bearer"
        and bool(token)
        and hmac.compare_digest(token, expected)
    )
    return {"isAuthorized": authorized}
