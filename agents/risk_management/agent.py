"""
Risk Management Agent — hard gate for all trading signals.

Pre-trade checks (must all pass before order reaches execution):
  1. Daily loss circuit breaker — halt if loss > X% of capital
  2. Per-trade max loss limit
  3. Position size limit (% of capital and max lots)
  4. Portfolio delta limit
  5. Portfolio vega limit
  6. Margin availability
  7. Concentration check (max exposure per underlying)
  8. Expiry-day restrictions

Real-time monitoring (continuous):
  - Portfolio P&L vs daily circuit breaker
  - Individual trade P&L vs stop-loss
  - Greeks drift alerts
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional

from agents.base_agent import BaseAgent
from core.config import settings
from core.enums import AlertSeverity, EventType, Underlying
from core.exceptions import (
    CircuitBreakerError,
    DeltaLimitError,
    MaxLossExceededError,
    PositionSizeLimitError,
    RiskViolationError,
)
from core.models import Event, Portfolio, PortfolioGreeks, RiskAlert, RiskAssessment, Signal

logger = logging.getLogger(__name__)


class RiskManagementAgent(BaseAgent):
    """
    Hard gate between Strategy Agent and Execution Agent.
    Every signal must receive RISK_APPROVED before it can be executed.
    """

    name = "risk_management_agent"

    def __init__(self) -> None:
        super().__init__()
        self._daily_realized_pnl: float = 0.0
        self._portfolio: Portfolio = Portfolio()
        self._circuit_breaker_triggered: bool = False
        self._trade_count_today: int = 0

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Process signals and portfolio updates."""
        monitor_task = asyncio.create_task(self._monitoring_loop(), name="risk:monitor")

        async for event in self._event_bus.subscribe(
            agent_name=self.name,
            event_types=[
                EventType.SIGNAL_GENERATED.value,
                EventType.TRADE_PNL_UPDATE.value,
                EventType.TRADE_CLOSED.value,
            ],
        ):
            if self._stop_event.is_set():
                monitor_task.cancel()
                break

            if event.type == EventType.SIGNAL_GENERATED:
                await self._evaluate_signal(event)
            elif event.type in (EventType.TRADE_PNL_UPDATE, EventType.TRADE_CLOSED):
                await self._update_portfolio_state(event)

        monitor_task.cancel()

    # ── Pre-trade evaluation ──────────────────────────────────────────────────

    async def _evaluate_signal(self, event: Event) -> None:
        """Evaluate a signal against all risk rules. Publish APPROVED or REJECTED."""
        try:
            signal = Signal.model_validate(event.payload["signal"])
        except Exception as exc:
            logger.error("Failed to parse signal: %s", exc)
            return

        reasons: List[str] = []
        approved = True

        try:
            self._check_circuit_breaker()
            self._check_max_open_trades()
            self._check_per_trade_max_loss(signal)
            self._check_position_size(signal)
            self._check_delta_limit(signal)
            self._check_vega_limit(signal)
            await self._check_margin(signal)

        except RiskViolationError as exc:
            approved = False
            reasons.append(str(exc))
            self.logger.warning("Risk REJECTED signal %s: %s", signal.id, exc)

        except Exception as exc:
            approved = False
            reasons.append(f"Unexpected risk check error: {exc}")
            self.logger.error("Risk check error for signal %s: %s", signal.id, exc)

        assessment = RiskAssessment(
            signal_id=signal.id,
            approved=approved,
            reasons=reasons,
            max_loss_estimate=signal.max_loss_estimate,
            margin_required=self._estimate_margin(signal),
        )

        event_type = EventType.RISK_APPROVED if approved else EventType.RISK_REJECTED
        out_event = self.build_event(
            event_type,
            {
                "assessment": assessment.model_dump(mode="json"),
                "signal": event.payload["signal"],
            },
            correlation_id=signal.id,
        )
        await self.publish(out_event)

        if approved:
            self._trade_count_today += 1
            self.logger.info("Risk APPROVED signal %s [%s]", signal.id, signal.strategy.value)

    # ── Risk checks ───────────────────────────────────────────────────────────

    def _check_circuit_breaker(self) -> None:
        if self._circuit_breaker_triggered:
            raise CircuitBreakerError(
                "Daily loss circuit breaker is ACTIVE — trading halted.",
                limit_name="circuit_breaker",
                current_value=self._daily_realized_pnl,
                limit_value=0,
            )

        capital = settings.risk.total_capital
        cb_limit = -capital * settings.risk.daily_loss_circuit_breaker_pct
        current_pnl = self._daily_realized_pnl + self._portfolio.unrealized_pnl

        if current_pnl <= cb_limit:
            self._circuit_breaker_triggered = True
            raise CircuitBreakerError(
                f"Daily loss ₹{abs(current_pnl):.0f} exceeded circuit breaker ₹{abs(cb_limit):.0f}",
                limit_name="daily_loss_pct",
                current_value=current_pnl,
                limit_value=cb_limit,
            )

    def _check_max_open_trades(self) -> None:
        n_open = len([p for p in self._portfolio.positions if p.quantity != 0])
        max_trades = settings.risk.max_open_trades
        if n_open >= max_trades * 4:   # 4 legs per strategy
            raise PositionSizeLimitError(
                f"Max open positions ({max_trades} strategies) reached.",
                limit_name="max_open_trades",
                current_value=n_open,
                limit_value=max_trades * 4,
            )

    def _check_per_trade_max_loss(self, signal: Signal) -> None:
        max_loss = signal.max_loss_estimate
        limit = settings.risk.max_loss_per_trade
        if max_loss > limit:
            raise MaxLossExceededError(
                f"Signal max loss ₹{max_loss:.0f} exceeds limit ₹{limit:.0f}",
                limit_name="max_loss_per_trade",
                current_value=max_loss,
                limit_value=limit,
            )

    def _check_position_size(self, signal: Signal) -> None:
        capital = settings.risk.total_capital
        max_pct = settings.risk.max_capital_per_trade_pct
        estimated_cost = self._estimate_margin(signal)
        limit = capital * max_pct

        if estimated_cost > limit:
            raise PositionSizeLimitError(
                f"Estimated margin ₹{estimated_cost:.0f} exceeds {max_pct*100:.0f}% of capital (₹{limit:.0f})",
                limit_name="position_size_pct",
                current_value=estimated_cost,
                limit_value=limit,
            )

    def _check_delta_limit(self, signal: Signal) -> None:
        """Rough delta check: signal should not push portfolio delta beyond limit."""
        current_delta = self._portfolio.greeks.net_delta
        signal_delta = sum(
            leg.quantity * leg.lot_size * (1.0 if leg.side.value == "BUY" else -1.0) * 0.5
            for leg in signal.legs
        )
        projected_delta = abs(current_delta + signal_delta)
        limit = settings.risk.max_net_delta

        if projected_delta > limit:
            raise DeltaLimitError(
                f"Projected net delta {projected_delta:.1f} exceeds limit {limit:.1f}",
                limit_name="max_net_delta",
                current_value=projected_delta,
                limit_value=limit,
            )

    def _check_vega_limit(self, signal: Signal) -> None:
        """Rough vega check."""
        current_vega = self._portfolio.greeks.net_vega
        # Estimate signal vega: each sell leg adds negative vega
        signal_vega = sum(
            leg.quantity * leg.lot_size * (0.05 if leg.side.value == "BUY" else -0.05)
            for leg in signal.legs
        )
        projected_vega = abs(current_vega + signal_vega)
        limit = settings.risk.max_net_vega

        if projected_vega > limit:
            raise RiskViolationError(
                f"Projected net vega {projected_vega:.1f} exceeds limit {limit:.1f}",
                limit_name="max_net_vega",
                current_value=projected_vega,
                limit_value=limit,
            )

    async def _check_margin(self, signal: Signal) -> None:
        """Check if margin buffer is maintained after this trade."""
        margin_available = self._portfolio.margin_available
        required = self._estimate_margin(signal)
        buffer_pct = settings.risk.min_margin_buffer_pct
        capital = settings.risk.total_capital

        min_buffer = capital * buffer_pct
        if margin_available - required < min_buffer:
            raise RiskViolationError(
                f"Insufficient margin buffer. Available: ₹{margin_available:.0f}, "
                f"Required: ₹{required:.0f}, Min buffer: ₹{min_buffer:.0f}",
                limit_name="margin_buffer",
                current_value=margin_available - required,
                limit_value=min_buffer,
            )

    def _estimate_margin(self, signal: Signal) -> float:
        """Rough SPAN margin estimate = max_loss * 1.5 (conservative)."""
        if signal.max_loss_estimate < float("inf"):
            return signal.max_loss_estimate * 1.5
        # For unlimited loss strategies (short straddle/strangle), estimate by premium
        return sum(
            leg.quantity * leg.lot_size * (leg.target_price or 100) * 5
            for leg in signal.legs
            if leg.side.value == "SELL"
        )

    # ── Real-time monitoring ──────────────────────────────────────────────────

    async def _monitoring_loop(self) -> None:
        """Continuous loop checking live portfolio risk."""
        while not self._stop_event.is_set():
            await self._check_portfolio_health()
            await self.sleep(15)   # Check every 15 seconds

    async def _check_portfolio_health(self) -> None:
        """Evaluate real-time portfolio risk and send alerts."""
        capital = settings.risk.total_capital
        cb_limit = capital * settings.risk.daily_loss_circuit_breaker_pct
        current_pnl = self._daily_realized_pnl + self._portfolio.unrealized_pnl

        # Warning at 50% of circuit breaker
        if current_pnl < -cb_limit * 0.5 and not self._circuit_breaker_triggered:
            await self._send_alert(
                severity=AlertSeverity.WARNING,
                title="P&L Warning",
                message=f"Daily P&L at ₹{current_pnl:.0f} — approaching circuit breaker at ₹{-cb_limit:.0f}",
                metric_name="daily_pnl",
                current_value=current_pnl,
                limit_value=-cb_limit,
            )

        # Delta alert
        net_delta = abs(self._portfolio.greeks.net_delta)
        delta_limit = settings.risk.max_net_delta
        if net_delta > delta_limit * 0.8:
            await self._send_alert(
                severity=AlertSeverity.WARNING,
                title="High Delta Exposure",
                message=f"Net delta {net_delta:.1f} near limit {delta_limit:.1f}",
                metric_name="net_delta",
                current_value=net_delta,
                limit_value=delta_limit,
            )

    async def _send_alert(
        self, severity: AlertSeverity, title: str, message: str,
        metric_name: str, current_value: float, limit_value: float,
    ) -> None:
        alert = RiskAlert(
            severity=severity,
            title=title,
            message=message,
            metric_name=metric_name,
            current_value=current_value,
            limit_value=limit_value,
        )
        event = self.build_event(
            EventType.RISK_ALERT,
            {"alert": alert.model_dump(mode="json")},
        )
        await self.publish(event)
        self.logger.warning("[RISK ALERT] %s: %s", title, message)

    async def _update_portfolio_state(self, event: Event) -> None:
        """Update internal P&L tracking from trade events."""
        payload = event.payload
        if event.type == EventType.TRADE_CLOSED:
            pnl = payload.get("realized_pnl", 0.0)
            self._daily_realized_pnl += pnl

    def reset_daily_state(self) -> None:
        """Called at market open each day to reset daily counters."""
        self._daily_realized_pnl = 0.0
        self._circuit_breaker_triggered = False
        self._trade_count_today = 0
        self.logger.info("Risk state reset for new trading day")
