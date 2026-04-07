"""System / orchestrator control endpoints."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from core.config import settings

router = APIRouter()


class SystemStatus(BaseModel):
    mode: str
    version: str = "1.0.0"
    underlyings: list[str] = ["NIFTY", "BANKNIFTY"]


@router.get("/status", response_model=SystemStatus)
async def get_status() -> SystemStatus:
    """Return the current system mode and configuration."""
    return SystemStatus(mode=settings.trading_mode)


@router.get("/config")
async def get_config() -> dict:
    """Return non-sensitive configuration values."""
    return {
        "trading_mode": settings.trading_mode,
        "total_capital": settings.risk.total_capital,
        "max_open_trades": settings.risk.max_open_trades,
        "daily_loss_circuit_breaker_pct": settings.risk.daily_loss_circuit_breaker_pct,
        "max_net_delta": settings.risk.max_net_delta,
        "max_net_vega": settings.risk.max_net_vega,
        "enabled_underlyings": settings.strategy.underlyings,
    }
