"""
Execution Agent — smart order routing for multi-leg option strategies.

Responsibilities:
  1. Listen for RISK_APPROVED events.
  2. Convert Signal legs into OrderRequests.
  3. Place orders leg-by-leg via the Dhan broker (with retry logic).
  4. Publish ORDER_PLACED, ORDER_FILLED, ORDER_REJECTED events.
  5. For intraday strategies, register a square-off task at 15:20 IST.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, time
from typing import Dict, List, Optional

from agents.base_agent import BaseAgent
from agents.execution.brokers.dhan import DhanBroker
from core.config import settings
from core.enums import EventType, OrderSide, OrderStatus, OrderType, ProductType, TradeStyle
from core.exceptions import BrokerError, OrderRejectedError
from core.models import Event, Order, OrderRequest, RiskAssessment, Signal, Trade

logger = logging.getLogger(__name__)

# Map Dhan security IDs — this would normally come from a database / symbol lookup
# For demo purposes: NIFTY index = "13", BANKNIFTY index = "25"
# F&O contracts have their own security IDs — looked up via option chain data
SECURITY_ID_CACHE: Dict[str, str] = {}


class ExecutionAgent(BaseAgent):
    """
    Converts approved risk signals into live (or paper) Dhan orders.
    """

    name = "execution_agent"

    def __init__(self) -> None:
        super().__init__()
        self._broker = DhanBroker()
        # Active trades: trade_id → Trade
        self._active_trades: Dict[str, Trade] = {}
        # Intraday square-off scheduled: trade_id → asyncio.Task
        self._squareoff_tasks: Dict[str, asyncio.Task] = {}

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        async for event in self._event_bus.subscribe(
            agent_name=self.name,
            event_types=[EventType.RISK_APPROVED.value, EventType.MARKET_CLOSE.value],
        ):
            if self._stop_event.is_set():
                break

            if event.type == EventType.RISK_APPROVED:
                await self._execute_signal(event)
            elif event.type == EventType.MARKET_CLOSE:
                await self._squareoff_all_intraday()

    # ── Signal execution ──────────────────────────────────────────────────────

    async def _execute_signal(self, event: Event) -> None:
        """Place all legs of an approved signal."""
        try:
            signal = Signal.model_validate(event.payload["signal"])
            assessment = RiskAssessment.model_validate(event.payload["assessment"])
        except Exception as exc:
            self.logger.error("Failed to parse approved signal: %s", exc)
            return

        trade = Trade(
            strategy=signal.strategy,
            underlying=signal.underlying,
            signal_id=signal.id,
            trade_style=signal.trade_style,
        )

        self.logger.info(
            "Executing %d-leg %s signal [%s] %s",
            len(signal.legs),
            signal.strategy.value,
            signal.trade_style.value,
            signal.underlying.value,
        )

        orders: List[Order] = []
        all_filled = True

        for leg in signal.legs:
            # Determine product type from trade style
            product_type = (
                ProductType.INTRADAY
                if signal.trade_style == TradeStyle.INTRADAY
                else ProductType.MARGIN
            )

            order_request = OrderRequest(
                symbol=leg.symbol,
                underlying=leg.underlying,
                option_type=leg.option_type,
                strike=leg.strike,
                expiry=leg.expiry,
                side=leg.side,
                order_type=OrderType.LIMIT if leg.target_price else OrderType.MARKET,
                product_type=product_type,
                quantity=leg.total_quantity,
                price=leg.target_price,
                trade_style=signal.trade_style,
                signal_id=signal.id,
                trade_id=trade.id,
                tag=f"NSE_TRADER_{signal.strategy.value[:8]}",
            )

            order = await self._place_with_retry(order_request, leg.symbol)
            orders.append(order)
            trade.orders.append(order)

            if order.status == OrderStatus.REJECTED:
                all_filled = False
                self.logger.error("Leg rejected: %s. Rolling back...", leg.symbol)
                # Roll back: cancel already-placed legs
                await self._cancel_open_orders(orders[:-1])
                break

            # Publish ORDER_PLACED event
            await self.publish(self.build_event(
                EventType.ORDER_PLACED,
                {"order": order.model_dump(mode="json"), "trade_id": trade.id},
                correlation_id=signal.id,
            ))

            # Small delay between legs (avoid rate limiting)
            await asyncio.sleep(0.2)

        if all_filled:
            self._active_trades[trade.id] = trade
            await self.publish(self.build_event(
                EventType.TRADE_OPENED,
                {"trade": trade.model_dump(mode="json")},
                correlation_id=signal.id,
            ))

            # Schedule intraday square-off
            if signal.trade_style == TradeStyle.INTRADAY:
                task = asyncio.create_task(
                    self._scheduled_squareoff(trade),
                    name=f"squareoff:{trade.id}",
                )
                self._squareoff_tasks[trade.id] = task

    # ── Order placement with retry ────────────────────────────────────────────

    async def _place_with_retry(
        self,
        request: OrderRequest,
        symbol: str,
        max_retries: int = 3,
    ) -> Order:
        """Place an order with up to max_retries attempts on transient errors."""
        order = Order(
            **request.model_dump(),
            placed_at=datetime.now(),
        )

        # Lookup security ID from cache or use a placeholder
        sec_id = SECURITY_ID_CACHE.get(symbol, "0")

        for attempt in range(max_retries):
            try:
                broker_id = await self._broker.place_order(request, security_id=sec_id)
                order.broker_order_id = broker_id

                # Poll for fill status (up to 10 seconds)
                order = await self._wait_for_fill(order)
                return order

            except OrderRejectedError as exc:
                order.status = OrderStatus.REJECTED
                order.rejection_reason = exc.reason
                self.logger.error("Order rejected for %s: %s", symbol, exc.reason)
                return order

            except BrokerError as exc:
                if attempt < max_retries - 1:
                    wait = 2 ** attempt   # Exponential backoff
                    self.logger.warning(
                        "Order error for %s (attempt %d/%d): %s. Retry in %ds...",
                        symbol, attempt + 1, max_retries, exc, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    order.status = OrderStatus.REJECTED
                    order.rejection_reason = str(exc)
                    return order

        return order

    async def _wait_for_fill(self, order: Order, timeout: float = 15.0) -> Order:
        """Poll broker until order is filled or timeout."""
        if order.broker_order_id and order.broker_order_id.startswith("PAPER_"):
            # Paper mode: simulate instant fill
            order.status = OrderStatus.FILLED
            order.filled_quantity = order.quantity
            order.average_price = order.price or 100.0
            order.filled_at = datetime.now()
            return order

        deadline = asyncio.get_event_loop().time() + timeout
        poll_interval = 1.0

        while asyncio.get_event_loop().time() < deadline:
            try:
                data = await self._broker.get_order_status(order.broker_order_id)
                dhan_status = data.get("orderStatus", "PENDING")
                order.status = DhanBroker.map_order_status(dhan_status)
                order.filled_quantity = int(data.get("filledQty", 0))
                order.average_price = float(data.get("price", 0))

                if order.is_complete:
                    if order.status == OrderStatus.FILLED:
                        order.filled_at = datetime.now()
                        await self.publish(self.build_event(
                            EventType.ORDER_FILLED,
                            {"order": order.model_dump(mode="json")},
                        ))
                    return order

            except BrokerError as exc:
                self.logger.warning("Poll error: %s", exc)

            await asyncio.sleep(poll_interval)

        # Timeout — treat as pending for now
        self.logger.warning("Order %s fill timeout", order.broker_order_id)
        return order

    # ── Square-off ────────────────────────────────────────────────────────────

    async def _scheduled_squareoff(self, trade: Trade) -> None:
        """Wait until 15:20 IST then square off all legs of an intraday trade."""
        now = datetime.now()
        sq_time = now.replace(
            hour=15, minute=20, second=0, microsecond=0,
        )
        wait_seconds = (sq_time - now).total_seconds()

        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        await self._exit_trade(trade, reason="INTRADAY_SQUAREOFF")

    async def _squareoff_all_intraday(self) -> None:
        """Called on MARKET_CLOSE event — exit all intraday trades."""
        intraday_trades = [
            t for t in self._active_trades.values()
            if t.trade_style == TradeStyle.INTRADAY and t.is_open
        ]
        self.logger.info("Market close: squaring off %d intraday trades", len(intraday_trades))
        for trade in intraday_trades:
            await self._exit_trade(trade, reason="MARKET_CLOSE")

    async def _exit_trade(self, trade: Trade, reason: str = "MANUAL") -> None:
        """Exit all open legs of a trade by placing opposite orders."""
        self.logger.info("Exiting trade %s [%s] — Reason: %s", trade.id, trade.strategy.value, reason)

        for order in trade.orders:
            if order.status != OrderStatus.FILLED:
                continue

            # Reverse the leg
            exit_side = OrderSide.BUY if order.side == OrderSide.SELL else OrderSide.SELL
            exit_request = OrderRequest(
                symbol=order.symbol,
                underlying=order.underlying,
                option_type=order.option_type,
                strike=order.strike,
                expiry=order.expiry,
                side=exit_side,
                order_type=OrderType.MARKET,
                product_type=order.product_type,
                quantity=order.filled_quantity or order.quantity,
                trade_style=order.trade_style,
                signal_id=order.signal_id,
                trade_id=trade.id,
                tag=f"EXIT_{reason[:8]}",
            )

            await self._place_with_retry(exit_request, order.symbol)
            await asyncio.sleep(0.3)

        trade.is_open = False
        trade.exit_time = datetime.now()
        self._active_trades.pop(trade.id, None)

        await self.publish(self.build_event(
            EventType.TRADE_CLOSED,
            {"trade": trade.model_dump(mode="json"), "reason": reason},
        ))

    async def _cancel_open_orders(self, orders: List[Order]) -> None:
        """Cancel any orders that are still OPEN (for rollback)."""
        for order in orders:
            if order.broker_order_id and order.status in (OrderStatus.OPEN, OrderStatus.PENDING):
                try:
                    await self._broker.cancel_order(order.broker_order_id)
                except BrokerError as exc:
                    self.logger.error("Cancel failed for %s: %s", order.broker_order_id, exc)
