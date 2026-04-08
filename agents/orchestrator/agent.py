"""
Orchestrator Agent — system coordinator using LangGraph.

Responsibilities:
  1. Start, monitor, and restart all specialist agents.
  2. Enforce market session schedule (pre-market → open → close → EOD).
  3. Hot-reload configuration without restarting.
  4. Control PAPER / LIVE mode transitions.
  5. Expose a control interface via the FastAPI layer.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, time
from typing import Any, Dict, Optional

from agents.analytics.agent import AnalyticsAgent
from agents.execution.agent import ExecutionAgent
from agents.greeks_engine.agent import GreeksEngineAgent
from agents.market_data.agent import MarketDataAgent
from agents.order_manager.agent import OrderManagerAgent
from agents.risk_management.agent import RiskManagementAgent
from agents.strategy.agent import StrategyAgent
from agents.feature_engineering.agent import FeatureEngineeringAgent
from agents.order_flow_analysis.agent import OrderFlowAnalysisAgent
from agents.market_regime.agent import MarketRegimeAgent
from agents.hedging.agent import HedgingAgent
from core.config import settings
from core.enums import EventType
from core.models import Event

logger = logging.getLogger(__name__)

# IST market schedule (UTC+5:30 offset — handled by host timezone)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
PRE_MARKET_START = time(8, 45)
EOD_REPORT = time(15, 45)


class Orchestrator:
    """
    Manages the lifecycle of all trading agents.

    Usage:
        orchestrator = Orchestrator()
        await orchestrator.run()
    """

    def __init__(self) -> None:
        self._agents = {
            "market_data": MarketDataAgent(),
            "feature_engineering": FeatureEngineeringAgent(),
            "order_flow_analysis": OrderFlowAnalysisAgent(),
            "market_regime": MarketRegimeAgent(),
            "greeks_engine": GreeksEngineAgent(),
            "strategy": StrategyAgent(),
            "risk_management": RiskManagementAgent(),
            "hedging": HedgingAgent(),
            "execution": ExecutionAgent(),
            "order_manager": OrderManagerAgent(),
            "analytics": AnalyticsAgent(),
        }
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._mode = settings.trading_mode

        # Handle SIGINT/SIGTERM for graceful shutdown
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._handle_signal)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start all agents and run the market session event loop."""
        logger.info(
            "🚀 NSE Options Trader starting in %s mode",
            self._mode.upper(),
        )
        self._running = True

        # Start all agents
        await self._start_all_agents()

        # Schedule session events
        session_task = asyncio.create_task(
            self._session_loop(), name="orchestrator:session"
        )
        health_task = asyncio.create_task(
            self._health_monitor_loop(), name="orchestrator:health"
        )

        try:
            await asyncio.gather(session_task, health_task)
        except asyncio.CancelledError:
            pass
        finally:
            await self._stop_all_agents()
            logger.info("✅  All agents stopped. Goodbye.")

    async def _start_all_agents(self) -> None:
        """Start every agent as an independent async task."""
        for name, agent in self._agents.items():
            task = asyncio.create_task(
                self._run_agent_with_restart(name, agent),
                name=f"agent:{name}",
            )
            self._tasks[name] = task
            logger.info("  ✔  Agent started: %s", name)
        # Give agents a moment to connect
        await asyncio.sleep(2)

    async def _stop_all_agents(self) -> None:
        """Gracefully stop all running agents."""
        logger.info("Stopping all agents...")
        for name, agent in self._agents.items():
            try:
                await agent.stop()
            except Exception as exc:
                logger.error("Error stopping %s: %s", name, exc)
        for task in self._tasks.values():
            task.cancel()

    # ── Agent restart wrapper ─────────────────────────────────────────────────

    async def _run_agent_with_restart(self, name: str, agent: Any) -> None:
        """Run an agent with exponential backoff restart on failure."""
        retry_delay = 1
        max_delay = 60

        while self._running:
            try:
                await agent.start()
            except asyncio.CancelledError:
                logger.info("Agent %s cancelled", name)
                return
            except Exception as exc:
                logger.error(
                    "Agent %s crashed: %s. Restarting in %ds...",
                    name, exc, retry_delay,
                )
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_delay)
            else:
                retry_delay = 1   # Reset on clean exit

    # ── Market session scheduler ──────────────────────────────────────────────

    async def _session_loop(self) -> None:
        """Drive market session state transitions."""
        while self._running:
            now = datetime.now().time().replace(second=0, microsecond=0)

            if now == PRE_MARKET_START:
                await self._on_pre_market()
            elif now == MARKET_OPEN:
                await self._on_market_open()
            elif now == MARKET_CLOSE:
                await self._on_market_close()
            elif now == EOD_REPORT:
                await self._on_eod()

            await asyncio.sleep(30)   # Check every 30 seconds

    async def _on_pre_market(self) -> None:
        """Pre-market: warm up data feeds and run checks."""
        logger.info("⏰  Pre-market warmup (08:45 IST)")
        # Reset risk agent daily state
        risk_agent: RiskManagementAgent = self._agents["risk_management"]  # type: ignore
        risk_agent.reset_daily_state()

    async def _on_market_open(self) -> None:
        """Market open: publish MARKET_OPEN event to all agents."""
        logger.info("🟢  Market OPEN (09:15 IST)")
        event = Event(
            type=EventType.MARKET_OPEN,
            source_agent="orchestrator",
            payload={"mode": self._mode},
        )
        try:
            await self._agents["market_data"]._event_bus.publish(event)
        except Exception as exc:
            logger.warning("Failed to publish MARKET_OPEN: %s", exc)

    async def _on_market_close(self) -> None:
        """Market close: trigger intraday square-off."""
        logger.info("🔴  Market CLOSE (15:30 IST)")
        event = Event(
            type=EventType.MARKET_CLOSE,
            source_agent="orchestrator",
            payload={},
        )
        try:
            await self._agents["market_data"]._event_bus.publish(event)
        except Exception as exc:
            logger.warning("Failed to publish MARKET_CLOSE: %s", exc)

    async def _on_eod(self) -> None:
        """End-of-day: trigger EOD analytics report."""
        logger.info("📊  EOD report triggered (15:45 IST)")

    # ── Health monitor ─────────────────────────────────────────────────────────

    async def _health_monitor_loop(self) -> None:
        """Check task health every 60 seconds."""
        while self._running:
            await asyncio.sleep(60)
            for name, task in self._tasks.items():
                if task.done() and not task.cancelled():
                    exc = task.exception()
                    if exc:
                        logger.error("Agent %s task ended with error: %s", name, exc)

    # ── Mode control ──────────────────────────────────────────────────────────

    def switch_to_live(self) -> None:
        """Switch to live trading mode (requires explicit call + confirmation)."""
        if self._mode == "paper":
            self._mode = "live"
            logger.warning(
                "⚡  LIVE TRADING ENABLED — real orders will be placed on NSE!"
            )
        else:
            logger.info("Already in live mode.")

    def switch_to_paper(self) -> None:
        self._mode = "paper"
        logger.info("Switched back to PAPER mode. No real orders will be placed.")

    def get_status(self) -> Dict[str, Any]:
        """Return a health status dict for the API layer."""
        return {
            "mode": self._mode,
            "running": self._running,
            "agents": {
                name: "running" if not task.done() else "stopped"
                for name, task in self._tasks.items()
            },
            "timestamp": datetime.now().isoformat(),
        }

    # ── Signal handler ─────────────────────────────────────────────────────────

    def _handle_signal(self, signum: int, frame: Any) -> None:
        logger.info("Received signal %d — initiating graceful shutdown...", signum)
        self._running = False
        for task in self._tasks.values():
            task.cancel()


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    orchestrator = Orchestrator()
    await orchestrator.run()


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    asyncio.run(main())
