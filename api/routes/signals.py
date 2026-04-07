"""Signals endpoint — latest generated trading signals."""
from __future__ import annotations

import ast
from typing import Any

from fastapi import APIRouter

router = APIRouter()


@router.get("/")
async def list_signals() -> dict[str, Any]:
    """
    Return recent signal history.
    In production this queries the signals table in TimescaleDB.
    """
    return {"signals": [], "message": "Connect to TimescaleDB for signal history."}
