"""
Dhan Broker Wrapper — full integration with the Dhan API v2.

Wraps dhanhq SDK to provide a clean async interface.
Supports: order placement, modification, cancellation, positions, funds.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from dhanhq import dhanhq

from core.config import settings
from core.enums import (
    Exchange,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    TradeStyle,
    Underlying,
)
from core.exceptions import BrokerError, InsufficientMarginError, OrderRejectedError
from core.models import Order, OrderRequest, Position

logger = logging.getLogger(__name__)

# Dhan exchange segment codes
DHAN_EXCHANGE_MAP = {
    "NSE": "NSE_EQ",
    "NFO": "NSE_FNO",
    "BSE": "BSE_EQ",
    "BFO": "BSE_FNO",
}

DHAN_TRANSACTION_MAP = {
    OrderSide.BUY: "BUY",
    OrderSide.SELL: "SELL",
}

DHAN_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_LOSS: "STOP_LOSS",
    OrderType.STOP_LOSS_MARKET: "STOP_LOSS_MARKET",
}

DHAN_PRODUCT_TYPE_MAP = {
    ProductType.INTRADAY: "INTRADAY",
    ProductType.DELIVERY: "CNC",
    ProductType.MARGIN: "MARGIN",
}

DHAN_STATUS_MAP = {
    "TRADED": OrderStatus.FILLED,
    "PART_TRADED": OrderStatus.PARTIALLY_FILLED,
    "PENDING": OrderStatus.PENDING,
    "TRANSIT": OrderStatus.TRANSIT,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELLED,
    "EXPIRED": OrderStatus.EXPIRED,
}

# NIFTY/BANKNIFTY lot sizes
LOT_SIZES = {
    Underlying.NIFTY: 50,
    Underlying.BANKNIFTY: 15,
}


class DhanBroker:
    """Async wrapper around the dhanhq synchronous SDK."""

    def __init__(self) -> None:
        self._client = dhanhq(
            client_id=settings.dhan.client_id,
            access_token=settings.dhan.access_token,
        )
        self._is_paper = settings.is_paper
        logger.info(
            "DhanBroker initialised | Mode: %s",
            "PAPER" if self._is_paper else "LIVE",
        )

    async def _run(self, fn, *args, **kwargs) -> Any:
        """Run a synchronous Dhan SDK call in the thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    # ── Order placement ───────────────────────────────────────────────────────

    async def place_order(self, request: OrderRequest, security_id: str) -> str:
        """
        Place an order via Dhan API.

        Args:
            request: OrderRequest model
            security_id: Dhan security ID for the instrument (e.g. "41941")

        Returns:
            Dhan order ID string

        Raises:
            OrderRejectedError, InsufficientMarginError, BrokerError
        """
        if self._is_paper:
            # Paper mode: simulate order placement
            fake_id = f"PAPER_{abs(hash(request.symbol))}"
            logger.info("[PAPER] Simulated order: %s %s %s @ %s", request.side.value, request.quantity, request.symbol, request.price)
            return fake_id

        try:
            response = await self._run(
                self._client.place_order,
                security_id=security_id,
                exchange_segment=DHAN_EXCHANGE_MAP.get(request.exchange.value, "NSE_FNO"),
                transaction_type=DHAN_TRANSACTION_MAP[request.side],
                quantity=request.quantity,
                order_type=DHAN_ORDER_TYPE_MAP[request.order_type],
                product_type=DHAN_PRODUCT_TYPE_MAP.get(request.product_type, "INTRADAY"),
                price=request.price or 0.0,
                trigger_price=request.trigger_price or 0.0,
                tag=request.tag or "NSE_TRADER",
            )

            if response.get("status") == "failure":
                errors = response.get("errors", {})
                if "Insufficient" in str(errors):
                    raise InsufficientMarginError(str(errors))
                raise OrderRejectedError(
                    f"Dhan order rejected: {errors}",
                    reason=str(errors),
                )

            order_id = response.get("data", {}).get("orderId", "")
            logger.info("Order placed: %s | symbol=%s | qty=%d", order_id, request.symbol, request.quantity)
            return order_id

        except (OrderRejectedError, InsufficientMarginError):
            raise
        except Exception as exc:
            raise BrokerError(f"Failed to place order: {exc}") from exc

    # ── Order management ──────────────────────────────────────────────────────

    async def cancel_order(self, broker_order_id: str) -> bool:
        """Cancel an open order. Returns True if cancelled."""
        if self._is_paper:
            logger.info("[PAPER] Simulated cancel: %s", broker_order_id)
            return True

        try:
            response = await self._run(self._client.cancel_order, broker_order_id)
            return response.get("status") == "success"
        except Exception as exc:
            raise BrokerError(f"Cancel order failed: {exc}") from exc

    async def modify_order(
        self,
        broker_order_id: str,
        new_price: float,
        new_quantity: Optional[int] = None,
        order_type: Optional[str] = None,
    ) -> bool:
        """Modify price/quantity of an open order."""
        if self._is_paper:
            logger.info("[PAPER] Simulated modify: %s → ₹%s", broker_order_id, new_price)
            return True

        try:
            response = await self._run(
                self._client.modify_order,
                order_id=broker_order_id,
                order_type=order_type or "LIMIT",
                leg_name="ENTRY_LEG",
                quantity=new_quantity or 0,
                price=new_price,
                trigger_price=0.0,
                disclosed_quantity=0,
                validity="DAY",
            )
            return response.get("status") == "success"
        except Exception as exc:
            raise BrokerError(f"Modify order failed: {exc}") from exc

    # ── Order status ──────────────────────────────────────────────────────────

    async def get_order_status(self, broker_order_id: str) -> Dict[str, Any]:
        """Fetch current status of a specific order."""
        if self._is_paper:
            return {"status": "TRADED", "filled_qty": 1, "average_price": 100.0}

        try:
            response = await self._run(self._client.get_order_by_id, broker_order_id)
            return response.get("data", {})
        except Exception as exc:
            raise BrokerError(f"Get order status failed: {exc}") from exc

    async def get_all_orders(self) -> List[Dict[str, Any]]:
        """Fetch today's complete order book."""
        if self._is_paper:
            return []
        try:
            response = await self._run(self._client.get_order_list)
            return response.get("data", [])
        except Exception as exc:
            raise BrokerError(f"Get order list failed: {exc}") from exc

    # ── Positions & funds ─────────────────────────────────────────────────────

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Fetch current live positions from Dhan."""
        if self._is_paper:
            return []
        try:
            response = await self._run(self._client.get_positions)
            return response.get("data", [])
        except Exception as exc:
            raise BrokerError(f"Get positions failed: {exc}") from exc

    async def get_funds(self) -> Dict[str, float]:
        """
        Fetch available margin and fund details.
        Returns dict with keys: 'available', 'used', 'total'
        """
        if self._is_paper:
            return {
                "available": settings.risk.total_capital,
                "used": 0.0,
                "total": settings.risk.total_capital,
            }
        try:
            response = await self._run(self._client.get_fund_limits)
            data = response.get("data", {})
            return {
                "available": float(data.get("availabelBalance", 0)),
                "used": float(data.get("utilizedAmount", 0)),
                "total": float(data.get("sodLimit", settings.risk.total_capital)),
            }
        except Exception as exc:
            raise BrokerError(f"Get funds failed: {exc}") from exc

    # ── Option chain ──────────────────────────────────────────────────────────

    async def get_option_chain(self, under_sec_id: str, expiry: str) -> Dict[str, Any]:
        """Fetch option chain for given underlying security ID and expiry."""
        try:
            response = await self._run(
                self._client.get_option_chain,
                under_sec_id=under_sec_id,
                under_exch_seg="IDX_I",
                expiry=expiry,
            )
            return response
        except Exception as exc:
            raise BrokerError(f"Get option chain failed: {exc}") from exc

    @staticmethod
    def map_order_status(dhan_status: str) -> OrderStatus:
        return DHAN_STATUS_MAP.get(dhan_status, OrderStatus.PENDING)
