"""
Order Manager Agent — order lifecycle tracking and P&L aggregation.

Responsibilities:
  1. Subscribe to ORDER_PLACED, ORDER_FILLED, ORDER_REJECTED, TRADE_CLOSED events.
  2. Maintain in-memory state for all active orders and positions.
  3. Periodically reconcile with the Dhan order book.
  4. Compute real-time unrealised P&L per position.
  5. Aggregate portfolio-level Greeks from open positions.
  6. Publish TRADE_PNL_UPDATE events for the Risk Agent and Analytics Agent.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from agents.base_agent import BaseAgent
from agents.execution.brokers.dhan import DhanBroker
from core.config import settings
from core.enums import EventType, OrderStatus, Underlying
from core.models import (
    Event,
    Order,
    Portfolio,
    PortfolioGreeks,
    Position,
    Trade,
)

logger = logging.getLogger(__name__)


class OrderManagerAgent(BaseAgent):
    """
    Tracks all active orders/positions and maintains a live P&L ledger.
    Acts as the single source of truth for the portfolio state.
    """

    name = "order_manager_agent"

    def __init__(self) -> None:
        super().__init__()
        self._broker = DhanBroker()

        # State stores
        self._orders: Dict[str, Order] = {}      # order_id → Order
        self._positions: Dict[str, Position] = {} # symbol → Position
        self._trades: Dict[str, Trade] = {}       # trade_id → Trade
        self._portfolio = Portfolio()

        # Realised P&L for the session
        self._session_realised_pnl: float = 0.0

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Subscribe to order events and run periodic reconciliation."""
        reconcile_task = asyncio.create_task(
            self._reconciliation_loop(), name="om:reconcile"
        )
        pnl_task = asyncio.create_task(
            self._pnl_broadcast_loop(), name="om:pnl_broadcast"
        )

        async for event in self._event_bus.subscribe(
            agent_name=self.name,
            event_types=[
                EventType.ORDER_PLACED.value,
                EventType.ORDER_FILLED.value,
                EventType.ORDER_REJECTED.value,
                EventType.TRADE_OPENED.value,
                EventType.TRADE_CLOSED.value,
                EventType.TICK_UPDATE.value,
            ],
        ):
            if self._stop_event.is_set():
                reconcile_task.cancel()
                pnl_task.cancel()
                break

            handler = {
                EventType.ORDER_PLACED: self._on_order_placed,
                EventType.ORDER_FILLED: self._on_order_filled,
                EventType.ORDER_REJECTED: self._on_order_rejected,
                EventType.TRADE_OPENED: self._on_trade_opened,
                EventType.TRADE_CLOSED: self._on_trade_closed,
                EventType.TICK_UPDATE: self._on_tick_update,
            }.get(event.type)

            if handler:
                await handler(event)

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_order_placed(self, event: Event) -> None:
        order = Order.model_validate(event.payload["order"])
        self._orders[order.id] = order
        self.logger.debug("Order tracked: %s [%s] %s", order.id, order.side.value, order.symbol)

    async def _on_order_filled(self, event: Event) -> None:
        order = Order.model_validate(event.payload["order"])
        self._orders[order.id] = order

        # Update position
        self._update_position(order)
        self.logger.info(
            "Order filled: %s | %s | qty=%d | avg_price=%.2f",
            order.symbol,
            order.side.value,
            order.filled_quantity or order.quantity,
            order.average_price or 0,
        )

    async def _on_order_rejected(self, event: Event) -> None:
        order = Order.model_validate(event.payload["order"])
        self._orders[order.id] = order
        self.logger.warning("Order rejected: %s — %s", order.symbol, order.rejection_reason)

    async def _on_trade_opened(self, event: Event) -> None:
        trade = Trade.model_validate(event.payload["trade"])
        self._trades[trade.id] = trade
        self.logger.info(
            "Trade opened: %s | %s | %s",
            trade.id,
            trade.strategy.value,
            trade.underlying.value,
        )

    async def _on_trade_closed(self, event: Event) -> None:
        trade_data = event.payload.get("trade", {})
        trade_id = trade_data.get("id")
        if trade_id and trade_id in self._trades:
            trade = self._trades[trade_id]
            trade.is_open = False
            trade.exit_time = datetime.now()

            # Compute realised P&L for the trade
            realised = self._compute_trade_pnl(trade)
            self._session_realised_pnl += realised
            trade.realised_pnl = realised

            self.logger.info(
                "Trade closed: %s | Strategy: %s | Realised P&L: ₹%.2f",
                trade_id,
                trade.strategy.value,
                realised,
            )

            await self.publish(self.build_event(
                EventType.TRADE_PNL_UPDATE,
                {
                    "trade_id": trade_id,
                    "realized_pnl": realised,
                    "session_realized_pnl": self._session_realised_pnl,
                },
                correlation_id=trade_id,
            ))

    async def _on_tick_update(self, event: Event) -> None:
        """Update LTP for the ticked symbol and recompute unrealised P&L."""
        symbol = event.payload.get("symbol")
        ltp = float(event.payload.get("ltp", 0))
        if not symbol or ltp <= 0:
            return

        if symbol in self._positions:
            pos = self._positions[symbol]
            pos.current_price = ltp
            pos.unrealised_pnl = (ltp - pos.average_entry_price) * pos.quantity

        self._portfolio.unrealized_pnl = sum(
            p.unrealised_pnl or 0.0 for p in self._positions.values()
        )

    # ── Position management ───────────────────────────────────────────────────

    def _update_position(self, order: Order) -> None:
        """Update or create a position based on a filled order."""
        sym = order.symbol
        qty_delta = (order.filled_quantity or order.quantity)
        price = order.average_price or order.price or 0.0

        if order.side.value == "SELL":
            qty_delta = -qty_delta

        if sym in self._positions:
            pos = self._positions[sym]
            new_qty = pos.quantity + qty_delta
            if new_qty == 0:
                # Position fully closed
                del self._positions[sym]
                return
            # Update average entry price (FIFO approximation)
            if (pos.quantity > 0 and qty_delta > 0) or (pos.quantity < 0 and qty_delta < 0):
                pos.average_entry_price = (
                    (pos.average_entry_price * abs(pos.quantity) + price * abs(qty_delta))
                    / abs(new_qty)
                )
            pos.quantity = new_qty
        else:
            self._positions[sym] = Position(
                symbol=sym,
                quantity=qty_delta,
                average_entry_price=price,
                current_price=price,
                unrealised_pnl=0.0,
                strategy=order.tag,
            )

        # Sync portfolio positions list
        self._portfolio.positions = list(self._positions.values())

    # ── Reconciliation ───────────────────────────────────────────────────────

    async def _reconciliation_loop(self) -> None:
        """Reconcile order book with Dhan every 30 seconds (LIVE mode only)."""
        if settings.trading_mode != "live":
            return

        while not self._stop_event.is_set():
            await asyncio.sleep(30)
            try:
                await self._reconcile_with_broker()
            except Exception as exc:
                self.logger.error("Reconciliation error: %s", exc)

    async def _reconcile_with_broker(self) -> None:
        """Fetch positions from Dhan and reconcile with internal state."""
        loop = asyncio.get_event_loop()
        broker_positions = await loop.run_in_executor(
            None, self._broker.get_positions
        )
        for bp in broker_positions or []:
            sym = bp.get("tradingSymbol", "")
            qty = int(bp.get("netQty", 0))
            avg_price = float(bp.get("buyAvg", 0) or bp.get("sellAvg", 0))
            if sym and sym in self._positions:
                pos = self._positions[sym]
                if pos.quantity != qty:
                    self.logger.warning(
                        "Position mismatch for %s: internal=%d broker=%d",
                        sym, pos.quantity, qty,
                    )
                    pos.quantity = qty
                    pos.average_entry_price = avg_price

    # ── P&L broadcast ─────────────────────────────────────────────────────────

    async def _pnl_broadcast_loop(self) -> None:
        """Broadcast portfolio P&L snapshot every 10 seconds."""
        while not self._stop_event.is_set():
            await asyncio.sleep(10)
            await self._broadcast_portfolio_snapshot()

    async def _broadcast_portfolio_snapshot(self) -> None:
        """Publish a full portfolio snapshot to Redis cache and event bus."""
        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "session_realized_pnl": self._session_realised_pnl,
            "unrealized_pnl": self._portfolio.unrealized_pnl,
            "total_pnl": self._session_realised_pnl + self._portfolio.unrealized_pnl,
            "open_positions": len(self._positions),
            "positions": [p.model_dump(mode="json") for p in self._portfolio.positions],
        }

        await self._event_bus.set_cache(
            "portfolio_snapshot", str(snapshot), ttl_seconds=30
        )

        await self.publish(self.build_event(
            EventType.TRADE_PNL_UPDATE,
            snapshot,
        ))

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_trade_pnl(self, trade: Trade) -> float:
        """
        Simple P&L computation based on entry vs exit fills.
        For multi-leg strategies, net the premium received vs paid.
        """
        buy_cost = sum(
            (o.average_price or 0) * (o.filled_quantity or o.quantity)
            for o in trade.orders
            if o.side.value == "BUY" and o.status == OrderStatus.FILLED
        )
        sell_proceeds = sum(
            (o.average_price or 0) * (o.filled_quantity or o.quantity)
            for o in trade.orders
            if o.side.value == "SELL" and o.status == OrderStatus.FILLED
        )
        return sell_proceeds - buy_cost

    def get_portfolio_snapshot(self) -> Portfolio:
        return self._portfolio

    def get_open_positions(self) -> List[Position]:
        return list(self._positions.values())
