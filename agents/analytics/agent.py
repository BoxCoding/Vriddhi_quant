"""
Analytics & Reporting Agent.

Responsibilities:
  1. Subscribe to TRADE_PNL_UPDATE, TRADE_CLOSED, RISK_ALERT events.
  2. Maintain a session P&L ledger with per-strategy breakdown.
  3. Compute key performance metrics: Sharpe ratio, win rate, max drawdown.
  4. Send real-time Telegram notifications for key events.
  5. Generate an end-of-day report at 15:45 IST.
  6. Serve a WebSocket endpoint for the React dashboard (via FastAPI).
"""
from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from agents.base_agent import BaseAgent
from core.config import settings
from core.enums import AlertSeverity, EventType, StrategyName
from core.models import Event, RiskAlert, Trade

logger = logging.getLogger(__name__)


class AnalyticsAgent(BaseAgent):
    """
    Collects trading data, computes performance metrics, and delivers
    alerts and reports.
    """

    name = "analytics_agent"

    def __init__(self) -> None:
        super().__init__()
        # Per-trade P&L log: list of {strategy, pnl, closed_at}
        self._trade_log: List[Dict[str, Any]] = []
        # Running P&L per strategy
        self._strategy_pnl: Dict[str, float] = defaultdict(float)
        # Cumulative P&L series for drawdown calc
        self._pnl_series: List[float] = [0.0]
        # Alert log
        self._alerts: List[Dict[str, Any]] = []
        # Telegram notifier (lazy init)
        self._telegram: Optional[Any] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def on_start(self) -> None:
        await self._init_telegram()

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        eod_task = asyncio.create_task(self._eod_report_loop(), name="analytics:eod")

        async for event in self._event_bus.subscribe(
            agent_name=self.name,
            event_types=[
                EventType.TRADE_PNL_UPDATE.value,
                EventType.TRADE_CLOSED.value,
                EventType.RISK_ALERT.value,
                EventType.SIGNAL_GENERATED.value,
            ],
        ):
            if self._stop_event.is_set():
                eod_task.cancel()
                break

            if event.type == EventType.TRADE_CLOSED:
                await self._on_trade_closed(event)
            elif event.type == EventType.TRADE_PNL_UPDATE:
                await self._on_pnl_update(event)
            elif event.type == EventType.RISK_ALERT:
                await self._on_risk_alert(event)
            elif event.type == EventType.SIGNAL_GENERATED:
                await self._on_signal_generated(event)

        eod_task.cancel()

    # ── Event handlers ────────────────────────────────────────────────────────

    async def _on_trade_closed(self, event: Event) -> None:
        trade_data = event.payload.get("trade", {})
        strategy = trade_data.get("strategy", "UNKNOWN")
        pnl = float(trade_data.get("realised_pnl", 0.0))
        reason = event.payload.get("reason", "UNKNOWN")

        entry: Dict[str, Any] = {
            "trade_id": trade_data.get("id"),
            "strategy": strategy,
            "underlying": trade_data.get("underlying"),
            "pnl": pnl,
            "closed_at": datetime.now().isoformat(),
            "reason": reason,
        }
        self._trade_log.append(entry)
        self._strategy_pnl[strategy] += pnl

        # Update P&L series for drawdown
        new_cumulative = self._pnl_series[-1] + pnl
        self._pnl_series.append(new_cumulative)

        # Cache updated analytics
        await self._update_analytics_cache()

        # Send Telegram alert for notable P&L
        if abs(pnl) > 1000:
            emoji = "✅" if pnl > 0 else "🔴"
            msg = (
                f"{emoji} Trade Closed: *{strategy}*\n"
                f"P&L: ₹{pnl:,.2f}\n"
                f"Reason: {reason}\n"
                f"Net session P&L: ₹{new_cumulative:,.2f}"
            )
            await self._send_telegram(msg)

        self.logger.info(
            "Trade closed | Strategy: %s | P&L: ₹%.2f | Session: ₹%.2f",
            strategy, pnl, new_cumulative,
        )

    async def _on_pnl_update(self, event: Event) -> None:
        """Store latest P&L snapshot to Redis for the dashboard."""
        payload = event.payload
        snapshot = {
            "timestamp": payload.get("timestamp", datetime.now().isoformat()),
            "session_realized_pnl": payload.get("session_realized_pnl", 0.0),
            "unrealized_pnl": payload.get("unrealized_pnl", 0.0),
            "total_pnl": payload.get("total_pnl", 0.0),
            "open_positions": payload.get("open_positions", 0),
            "positions": payload.get("positions", []),
        }
        await self._event_bus.set_cache(
            "live_pnl_snapshot",
            str(snapshot),
            ttl_seconds=30,
        )

    async def _on_risk_alert(self, event: Event) -> None:
        alert_data = event.payload.get("alert", {})
        severity = alert_data.get("severity", "INFO")
        title = alert_data.get("title", "Risk Alert")
        message = alert_data.get("message", "")

        self._alerts.append({
            "timestamp": datetime.now().isoformat(),
            "severity": severity,
            "title": title,
            "message": message,
        })

        emoji_map = {
            AlertSeverity.CRITICAL.value: "🚨",
            AlertSeverity.WARNING.value: "⚠️",
            AlertSeverity.INFO.value: "ℹ️",
        }
        emoji = emoji_map.get(severity, "⚠️")
        msg = f"{emoji} *{title}*\n{message}"
        await self._send_telegram(msg)

    async def _on_signal_generated(self, event: Event) -> None:
        sig = event.payload.get("signal", {})
        strategy = sig.get("strategy", "")
        underlying = sig.get("underlying", "")
        confidence = sig.get("confidence", 0.0)

        self.logger.info(
            "Signal generated | Strategy: %s | Underlying: %s | Confidence: %.2f",
            strategy, underlying, confidence,
        )

        # Optional: notify on high-conviction signals
        if confidence >= 0.8:
            msg = (
                f"📊 *New High-Confidence Signal*\n"
                f"Strategy: {strategy}\n"
                f"Underlying: {underlying}\n"
                f"Confidence: {confidence:.0%}"
            )
            await self._send_telegram(msg)

    # ── Performance Metrics ───────────────────────────────────────────────────

    def compute_metrics(self) -> Dict[str, Any]:
        """Compute session performance metrics."""
        pnl_values = [t["pnl"] for t in self._trade_log]
        n = len(pnl_values)
        wins = [p for p in pnl_values if p > 0]
        losses = [p for p in pnl_values if p <= 0]

        win_rate = len(wins) / n * 100 if n > 0 else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        profit_factor = abs(sum(wins) / sum(losses)) if sum(losses) != 0 else float("inf")

        # Sharpe ratio (annualised, assuming 252 trading days)
        if n > 1:
            mean_pnl = sum(pnl_values) / n
            std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnl_values) / (n - 1))
            sharpe = (mean_pnl / std_pnl * math.sqrt(252)) if std_pnl > 0 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown
        max_dd = self._max_drawdown()

        return {
            "total_trades": n,
            "win_rate_pct": round(win_rate, 2),
            "avg_win_inr": round(avg_win, 2),
            "avg_loss_inr": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_inr": round(max_dd, 2),
            "session_pnl_inr": round(self._pnl_series[-1], 2),
            "strategy_breakdown": dict(self._strategy_pnl),
        }

    def _max_drawdown(self) -> float:
        """Calculate max drawdown from the P&L series."""
        if len(self._pnl_series) < 2:
            return 0.0
        peak = self._pnl_series[0]
        max_dd = 0.0
        for val in self._pnl_series:
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        return max_dd

    # ── EOD Report ────────────────────────────────────────────────────────────

    async def _eod_report_loop(self) -> None:
        """Wait until 15:45 IST then send the end-of-day report."""
        while not self._stop_event.is_set():
            now = datetime.now()
            target = now.replace(hour=15, minute=45, second=0, microsecond=0)
            wait = (target - now).total_seconds()
            if wait <= 0:
                wait = 86400 + wait  # Next day
            await asyncio.sleep(wait)
            if not self._stop_event.is_set():
                await self._send_eod_report()

    async def _send_eod_report(self) -> None:
        metrics = self.compute_metrics()
        msg = (
            f"📈 *NSE Options Trader — EOD Report*\n"
            f"Date: {datetime.now().strftime('%d %b %Y')}\n\n"
            f"Total Trades: {metrics['total_trades']}\n"
            f"Win Rate: {metrics['win_rate_pct']:.1f}%\n"
            f"Session P&L: ₹{metrics['session_pnl_inr']:,.2f}\n"
            f"Max Drawdown: ₹{metrics['max_drawdown_inr']:,.2f}\n"
            f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}\n"
            f"Profit Factor: {metrics['profit_factor']:.2f}\n\n"
            f"*Strategy Breakdown:*\n"
        )
        for strat, pnl in metrics.get("strategy_breakdown", {}).items():
            emoji = "✅" if pnl >= 0 else "🔴"
            msg += f"{emoji} {strat}: ₹{pnl:,.2f}\n"

        await self._send_telegram(msg)
        self.logger.info("EOD report sent.")

    # ── Telegram ──────────────────────────────────────────────────────────────

    async def _init_telegram(self) -> None:
        if not settings.notifications.telegram_bot_token:
            self.logger.info("Telegram not configured — skipping init")
            return
        try:
            from telegram import Bot  # type: ignore[import]
            self._telegram = Bot(token=settings.notifications.telegram_bot_token)
            self.logger.info("Telegram bot initialised")
        except Exception as exc:
            self.logger.warning("Telegram init failed: %s", exc)

    async def _send_telegram(self, message: str) -> None:
        if not self._telegram or not settings.notifications.telegram_chat_id:
            return
        try:
            await self._telegram.send_message(
                chat_id=settings.notifications.telegram_chat_id,
                text=message,
                parse_mode="Markdown",
            )
        except Exception as exc:
            self.logger.warning("Telegram send failed: %s", exc)

    # ── Cache ─────────────────────────────────────────────────────────────────

    async def _update_analytics_cache(self) -> None:
        """Push latest metrics to Redis for the dashboard."""
        metrics = self.compute_metrics()
        await self._event_bus.set_cache(
            "analytics_metrics",
            str(metrics),
            ttl_seconds=120,
        )
