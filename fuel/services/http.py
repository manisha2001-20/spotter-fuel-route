"""Shared HTTP plumbing for the two free external APIs, plus per-request
bookkeeping of how many external calls were actually made."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import requests
from django.conf import settings

from fuel.exceptions import UpstreamError, UpstreamTimeout

logger = logging.getLogger(__name__)

_session = requests.Session()


@dataclass
class CallStats:
    """Counts external calls and cache hits for one API request (reported in
    the response `meta` so it is easy to show that the API stays within the
    "one to three map/routing calls" budget)."""
    geocoding_calls: int = 0
    routing_calls: int = 0
    cache_hits: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "external_api_calls": {"geocoding": self.geocoding_calls, "routing": self.routing_calls,
                                   "total": self.geocoding_calls + self.routing_calls},
            "cache_hits": self.cache_hits,
        }


def get_json(service: str, url: str, params: dict, timeout: float):
    headers = {"User-Agent": settings.FUEL_ROUTE["HTTP_USER_AGENT"], "Accept": "application/json"}
    try:
        response = _session.get(url, params=params, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        logger.warning("%s timed out: %s", service, exc)
        raise UpstreamTimeout(f"The {service} service did not respond within {timeout:.0f} seconds.") from exc
    except requests.RequestException as exc:
        logger.warning("%s request failed: %s", service, exc)
        raise UpstreamError(f"Could not reach the {service} service.") from exc

    try:
        body = response.json()
    except ValueError:
        body = None
    if response.status_code >= 500 or response.status_code == 429 or body is None:
        raise UpstreamError(f"The {service} service returned HTTP {response.status_code}.")
    return response.status_code, body
