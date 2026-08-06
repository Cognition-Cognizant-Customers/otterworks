"""Search API endpoints."""

from __future__ import annotations

import structlog
from flask import Blueprint, current_app, jsonify, request

from app.api.health import SEARCH_COUNT
from app.services.meilisearch_client import MeiliSearchService, get_search_analytics

logger = structlog.get_logger()

search_bp = Blueprint("search", __name__)


def _get_service() -> MeiliSearchService:
    """Get the shared MeiliSearchService from app config."""
    return current_app.config["SEARCH_SERVICE"]


@search_bp.route("/", methods=["GET"], strict_slashes=False)
def search_documents() -> tuple:
    """Full-text search across documents and files.

    Query params: q (required), type, page, size
    Results are automatically scoped to the authenticated user via the
    ``X-User-ID`` header set by the API gateway.
    """
    query = request.args.get("q", "")
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = max(1, min(100, int(request.args.get("size", 20))))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid page or size parameter"}), 400
    doc_type = request.args.get("type")
    owner_id = request.headers.get("X-User-ID", "").strip() or None

    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    try:
        service = _get_service()
        results = service.search(
            query=query,
            doc_type=doc_type,
            owner_id=owner_id,
            page=page,
            page_size=page_size,
        )
        SEARCH_COUNT.inc()
        logger.info("search_executed", query=query, result_count=results.total)
        return jsonify(results.to_dict()), 200
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        logger.exception("search_failed", query=query)
        return jsonify({"error": "Search failed"}), 500


@search_bp.route("/suggest", methods=["GET"])
def suggest() -> tuple:
    """Autocomplete suggestions based on prefix.

    Query params: q (required, min 2 chars)
    """
    prefix = request.args.get("q", "")
    if not prefix or len(prefix) < 2:
        return jsonify({"suggestions": [], "query": prefix}), 200

    try:
        service = _get_service()
        suggestions = service.suggest(prefix)
        return jsonify({"suggestions": suggestions, "query": prefix}), 200
    except Exception:
        logger.exception("suggest_failed", prefix=prefix)
        return jsonify({"suggestions": [], "query": prefix}), 200


@search_bp.route("/advanced", methods=["POST"])
def advanced_search() -> tuple:
    """Advanced search with filters: date range, owner, type, tags.

    JSON body: {q, type, tags, date_from, date_to, page, size}
    owner_id is always derived from X-User-ID for tenant isolation.
    """
    data = request.get_json() or {}

    query = data.get("q")
    doc_type = data.get("type")
    owner_id = request.headers.get("X-User-ID", "").strip() or None
    tags = data.get("tags")
    date_from = data.get("date_from")
    date_to = data.get("date_to")
    try:
        page = max(int(data.get("page", 1)), 1)
        page_size = min(max(int(data.get("size", 20)), 1), 100)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid page or size parameter"}), 400

    try:
        service = _get_service()
        results = service.advanced_search(
            query=query,
            doc_type=doc_type,
            owner_id=owner_id,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
        SEARCH_COUNT.inc()
        logger.info("advanced_search_executed", query=query, result_count=results.total)
        return jsonify(results.to_dict()), 200
    except Exception:
        logger.exception("advanced_search_failed")
        return jsonify({"error": "Advanced search failed"}), 500


@search_bp.route("/analytics", methods=["GET"])
def search_analytics() -> tuple:
    """Search analytics: popular queries, zero-result queries."""
    try:
        analytics = get_search_analytics()
        return jsonify(analytics.to_dict()), 200
    except Exception:
        logger.exception("analytics_failed")
        return jsonify({"error": "Failed to retrieve analytics"}), 500
