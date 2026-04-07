"""Risk endpoints — current risk metrics and alerts."""
from __future__ import annotations

import ast
from typing import Any

from fastapi import APIRouter

from core.event_bus import get_event_bus

router = APIRouter()


@router.get("/metrics")
async def get_risk_metrics() -> dict[str, Any]:
    """Return the latest analytics metrics (win rate, drawdown, Sharpe, etc.)."""
    bus = await get_event_bus()
    raw = await bus.get_cache("analytics_metrics")
    if not raw:
        return {"message": "No metrics available yet."}
    try:
        return ast.literal_eval(raw) if isinstance(raw, str) else raw
    except Exception:
        return {"message": "Error parsing metrics."}


@router.get("/greeks")
async def get_portfolio_greeks() -> dict[str, Any]:
    """Return the current portfolio-level Greeks aggregate."""
    bus = await get_event_bus()
    raw = await bus.get_cache("portfolio_greeks")
    if not raw:
        return {"net_delta": 0, "net_gamma": 0, "net_theta": 0, "net_vega": 0}
    try:
        return ast.literal_eval(raw) if isinstance(raw, str) else raw
    except Exception:
        return {"message": "Error parsing Greeks."}
