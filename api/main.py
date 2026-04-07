"""
FastAPI Application — REST + WebSocket API for the NSE Options Trader dashboard.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from api.routes import orders, positions, risk, signals, system
from core.config import settings
from core.event_bus import EventBus, get_event_bus_sync
from core.enums import EventType

logger = logging.getLogger(__name__)

# Shared event bus for WebSocket broadcasting
_event_bus: EventBus | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / teardown for the FastAPI application."""
    global _event_bus
    _event_bus = get_event_bus_sync()
    await _event_bus.connect()
    logger.info("FastAPI startup complete — mode=%s", settings.trading_mode)
    yield
    if _event_bus:
        await _event_bus.disconnect()
    logger.info("FastAPI shutdown")


app = FastAPI(
    title="NSE Options Trader API",
    description=(
        "Production-grade multi-agent quantitative options trading API "
        "for NIFTY & BANKNIFTY on NSE. Powered by Dhan broker + Google Gemini LLM."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(system.router, prefix="/api/v1/system", tags=["System"])
app.include_router(positions.router, prefix="/api/v1/positions", tags=["Positions"])
app.include_router(orders.router, prefix="/api/v1/orders", tags=["Orders"])
app.include_router(signals.router, prefix="/api/v1/signals", tags=["Signals"])
app.include_router(risk.router, prefix="/api/v1/risk", tags=["Risk"])


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["Health"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "mode": settings.trading_mode}


# ── WebSocket — live dashboard feed ───────────────────────────────────────────
class ConnectionManager:
    """Manages active WebSocket connections for the dashboard."""

    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)
        logger.info("WS client connected (total=%d)", len(self.active))

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)
        logger.info("WS client disconnected (remaining=%d)", len(self.active))

    async def broadcast(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)


ws_manager = ConnectionManager()


@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket) -> None:
    """
    WebSocket endpoint consumed by the React dashboard.
    Streams live P&L, positions, signals, and risk alerts.
    """
    await ws_manager.connect(websocket)
    try:
        assert _event_bus is not None
        async for event in _event_bus.subscribe(
            agent_name="dashboard_ws",
            event_types=[
                EventType.TRADE_PNL_UPDATE.value,
                EventType.SIGNAL_GENERATED.value,
                EventType.RISK_ALERT.value,
                EventType.ORDER_FILLED.value,
                EventType.TRADE_CLOSED.value,
            ],
        ):
            await ws_manager.broadcast(event.payload)
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.error("WebSocket error: %s", exc)
        ws_manager.disconnect(websocket)
