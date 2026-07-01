"""Health and readiness endpoints, plus Prometheus metrics exposition."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy.orm import Session

from seko_ai import metrics
from seko_ai.db import get_session

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.get("/metrics")
def metrics_endpoint(session: Session = Depends(get_session)) -> Response:  # noqa: B008
    """Prometheus metrics exposition (scraped by the homelab Prometheus)."""
    metrics.refresh_gauges(session)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
