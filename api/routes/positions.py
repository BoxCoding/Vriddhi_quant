"""Positions endpoints — live open positions and P&L."""
from __future__ import annotations

import ast
from typing import Any

from fastapi import APIRouter, HTTPException

from core.event_bus import get_event_bus

router = APIRouter()


@router.get("/")
async def list_positions() -> dict[str, Any]:
    """Return current open positions from Redis cache."""
    bus = await get_event_bus()
    raw = await bus.get_cache("portfolio_snapshot")
    if not raw:
        return {"positions": [], "unrealized_pnl": 0.0}
    try:
        data = ast.literal_eval(raw) if isinstance(raw, str) else raw
        return data
    except Exception:
        raise HTTPException(status_code=500, detail="Error parsing portfolio snapshot")


@router.get("/pnl")
async def get_pnl() -> dict[str, Any]:
    """Return the latest P&L snapshot."""
    bus = await get_event_bus()
    raw = await bus.get_cache("live_pnl_snapshot")
    if not raw:
        return {"session_realized_pnl": 0.0, "unrealized_pnl": 0.0, "total_pnl": 0.0}
    try:
        return ast.literal_eval(raw) if isinstance(raw, str) else raw
    except Exception:
        raise HTTPException(status_code=500, detail="Error parsing P&L snapshot")
