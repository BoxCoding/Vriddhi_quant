"""Orders endpoint — recent orders from the session."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_orders() -> dict[str, Any]:
    """
    Return recent orders.
    In a production build this would query the TimescaleDB orders table.
    """
    return {"orders": [], "message": "Connect to TimescaleDB for order history."}
