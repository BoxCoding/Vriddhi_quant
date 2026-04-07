"""
Greeks Engine Agent.

Responsibilities:
  - Listen for OPTION_CHAIN_UPDATE events.
  - Compute live Greeks (Δ Γ Θ V ρ) + IV for every strike in the option chain.
  - Maintain a portfolio Greeks aggregate (net delta, vega, theta, etc.).
  - Publish GREEKS_UPDATE events for downstream agents.
  - Cache computed Greeks in Redis.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from typing import Dict, List, Optional

from agents.base_agent import BaseAgent
from agents.greeks_engine.black_scholes import (
    RISK_FREE_RATE,
    compute_greeks,
    compute_iv_percentile,
    compute_iv_rank,
    implied_volatility,
)
from core.enums import EventType, OptionType, Underlying
from core.exceptions import IVSolverError
from core.models import Event, Greeks, OptionChain, PortfolioGreeks

logger = logging.getLogger(__name__)

# Rolling IV history size (approx. 252 trading days = 1 year)
IV_HISTORY_SIZE = 252


class GreeksEngineAgent(BaseAgent):
    """
    Computes option Greeks for all NIFTY and BANKNIFTY contracts
    in real-time and maintains a portfolio-level Greeks aggregate.
    """

    name = "greeks_engine_agent"

    def __init__(self) -> None:
        super().__init__()
        # Historical ATM IV for IV Rank / Percentile computation
        self._iv_history: Dict[Underlying, List[float]] = {
            Underlying.NIFTY: [],
            Underlying.BANKNIFTY: [],
        }
        # Most recent portfolio Greeks
        self._portfolio_greeks = PortfolioGreeks()

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Subscribe to option chain updates and compute Greeks on each update."""
        async for event in self._event_bus.subscribe(
            agent_name=self.name,
            event_types=[EventType.OPTION_CHAIN_UPDATE.value],
        ):
            if self._stop_event.is_set():
                break
            await self._on_option_chain_update(event)

    # ── Handler ───────────────────────────────────────────────────────────────

    async def _on_option_chain_update(self, event: Event) -> None:
        """Triggered every time the Market Data Agent refreshes an option chain."""
        underlying_str = event.payload.get("underlying")
        try:
            underlying = Underlying(underlying_str)
        except ValueError:
            return

        # Retrieve the cached option chain
        cached = await self._event_bus.get_cache(f"option_chain:{underlying.value}")
        if not cached:
            self.logger.warning("No option chain in cache for %s", underlying.value)
            return

        option_chain = OptionChain.model_validate_json(cached)
        await self._compute_and_publish(option_chain)

    async def _compute_and_publish(self, option_chain: OptionChain) -> None:
        """Compute Greeks for every strike and publish an update event."""
        underlying = option_chain.underlying
        spot = option_chain.spot_price
        today = date.today()

        if spot <= 0:
            return

        T = self._time_to_expiry_years(option_chain.expiry, today)

        # Get current ATM IV from the ATM strike to update IV history
        atm_iv = self._get_atm_iv(option_chain, spot, T)
        self._update_iv_history(underlying, atm_iv)

        hist_ivs = self._iv_history[underlying]

        greeks_matrix: Dict[str, Dict[str, dict]] = {}

        for strike, opts in option_chain.strikes.items():
            greeks_matrix[str(strike)] = {}
            for opt_type_str, opt_tick in opts.items():
                opt_type = OptionType(opt_type_str)
                market_price = opt_tick.mid_price if opt_tick.mid_price > 0 else opt_tick.ltp

                # Solve for IV
                try:
                    iv = implied_volatility(
                        market_price=market_price,
                        S=spot,
                        K=strike,
                        T=T,
                        r=RISK_FREE_RATE,
                        option_type=opt_type,
                    )
                except IVSolverError:
                    iv = atm_iv   # Fall back to ATM IV

                iv_rank = compute_iv_rank(iv, hist_ivs)
                iv_percentile = compute_iv_percentile(iv, hist_ivs)

                greeks = compute_greeks(
                    S=spot,
                    K=strike,
                    T=T,
                    r=RISK_FREE_RATE,
                    sigma=iv,
                    option_type=opt_type,
                    iv_rank=iv_rank,
                    iv_percentile=iv_percentile,
                )

                # Attach Greeks back to the option tick (update cache)
                opt_tick.greeks = greeks
                greeks_matrix[str(strike)][opt_type_str] = greeks.model_dump()

        # Cache enriched option chain (with Greeks)
        await self._event_bus.set_cache(
            f"option_chain_greeks:{underlying.value}",
            option_chain.model_dump_json(),
            ttl_seconds=120,
        )

        # Publish event
        event = self.build_event(
            EventType.GREEKS_UPDATE,
            {
                "underlying": underlying.value,
                "spot": spot,
                "atm_iv": atm_iv,
                "iv_rank": compute_iv_rank(atm_iv, hist_ivs),
                "iv_percentile": compute_iv_percentile(atm_iv, hist_ivs),
                "strikes_count": len(greeks_matrix),
            },
        )
        await self.publish(event)

        self.logger.debug(
            "Greeks computed for %s | spot=%.2f | ATM IV=%.2f%% | IV Rank=%.1f",
            underlying.value,
            spot,
            atm_iv * 100,
            compute_iv_rank(atm_iv, hist_ivs),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_atm_iv(self, option_chain: OptionChain, spot: float, T: float) -> float:
        """
        Estimate ATM IV by averaging the IV of the two closest strikes.
        Use the CE IV (more liquid) as primary.
        """
        if not option_chain.strikes:
            return 0.20  # Default 20%

        sorted_strikes = sorted(option_chain.strikes.keys(), key=lambda s: abs(s - spot))

        for strike in sorted_strikes[:3]:
            opts = option_chain.strikes[strike]
            ce = opts.get(OptionType.CALL.value)
            if ce and ce.mid_price > 0.5:
                try:
                    return implied_volatility(
                        market_price=ce.mid_price,
                        S=spot,
                        K=strike,
                        T=T,
                        r=RISK_FREE_RATE,
                        option_type=OptionType.CALL,
                    )
                except IVSolverError:
                    continue
        return 0.20

    def _update_iv_history(self, underlying: Underlying, atm_iv: float) -> None:
        """Maintain a rolling 252-day IV history."""
        if atm_iv <= 0:
            return
        hist = self._iv_history[underlying]
        hist.append(atm_iv)
        if len(hist) > IV_HISTORY_SIZE:
            hist.pop(0)

    @staticmethod
    def _time_to_expiry_years(expiry: date, today: date) -> float:
        """Convert expiry date to time in years. Minimum 1 hour to avoid singularity."""
        delta = (expiry - today).days
        T = max(delta, 1.0 / 24.0) / 365.0
        return T

    async def get_portfolio_greeks(self) -> PortfolioGreeks:
        """Return the current aggregated portfolio Greeks."""
        return self._portfolio_greeks

    async def update_portfolio_greeks(self, greeks: PortfolioGreeks) -> None:
        """
        Called by the Order Manager Agent whenever positions change.
        Re-aggregates the portfolio Greeks.
        """
        self._portfolio_greeks = greeks
        await self._event_bus.set_cache(
            "portfolio_greeks",
            greeks.model_dump_json(),
            ttl_seconds=60,
        )
