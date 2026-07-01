"""Health and readiness endpoints, plus Prometheus metrics exposition."""

from __future__ import annotations

from fastapi import APIRouter, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/metrics")
def metrics() -> Response:
    """Prometheus metrics exposition (scraped by the homelab Prometheus)."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
