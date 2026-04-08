"""
Market Data Agent — Dhan API integration.

Responsibilities:
  1. Maintain a live WebSocket connection to Dhan's market feed for NIFTY & BANKNIFTY.
  2. Fetch and cache the full option chain periodically.
  3. Publish TICK_UPDATE and OPTION_CHAIN_UPDATE events to the event bus.
  4. Provide a cached option chain that other agents can read synchronously.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from dhanhq import dhanhq
from dhanhq import marketfeed

from agents.base_agent import BaseAgent
from core.config import settings
from core.enums import EventType, OptionType, Underlying
from core.models import Event, OptionChain, OptionTick, Tick

logger = logging.getLogger(__name__)

# Dhan security IDs for NIFTY & BANKNIFTY indices (spot price feed)
DHAN_SECURITY_IDS = {
    Underlying.NIFTY: "13",       # NSE NIFTY 50
    Underlying.BANKNIFTY: "25",   # NSE BANK NIFTY
}

LOT_SIZES = {
    Underlying.NIFTY: 50,
    Underlying.BANKNIFTY: 15,
}


class MarketDataAgent(BaseAgent):
    """
    Subscribes to Dhan live market feed for NIFTY and BANKNIFTY
    and broadcasts option chain updates to the event bus.
    """

    name = "market_data_agent"

    def __init__(self) -> None:
        super().__init__()
        self._dhan = dhanhq(
            client_id=settings.dhan.client_id,
            access_token=settings.dhan.access_token,
        )
        # Live option chain cache: {Underlying -> OptionChain}
        self._option_chains: Dict[Underlying, OptionChain] = {}
        # Live spot prices
        self._spot_prices: Dict[Underlying, float] = {}
        self._india_vix: float = 0.0

    # ── Lifecycle hooks ────────────────────────────────────────────────────────

    async def on_start(self) -> None:
        self.logger.info("MarketDataAgent: fetching initial option chains...")
        for underlying in [Underlying.NIFTY, Underlying.BANKNIFTY]:
            await self._refresh_option_chain(underlying)

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Runs two concurrent tasks: WebSocket feed + periodic option chain refresh."""
        tasks = [
            asyncio.create_task(self._websocket_feed_loop(), name="ws_feed"),
            asyncio.create_task(self._option_chain_refresh_loop(), name="oc_refresh"),
            asyncio.create_task(self._vix_refresh_loop(), name="vix_refresh"),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()

    # ── WebSocket feed ────────────────────────────────────────────────────────

    async def _websocket_feed_loop(self) -> None:
        """
        Opens a Dhan market feed WebSocket and streams live ticks.
        Dhan's Python SDK uses a synchronous websocket_client under the hood,
        so we run it in a thread executor.
        """
        self.logger.info("Starting Dhan WebSocket market feed...")

        def on_message(data):
            """Callback invoked by DhanFeed for each tick (runs in executor thread)."""
            asyncio.run_coroutine_threadsafe(
                self._handle_tick(data),
                asyncio.get_event_loop(),
            )

        def on_close():
            self.logger.warning("Dhan WebSocket connection closed. Will reconnect...")

        subscribe_list = []
        for underlying, sec_id in DHAN_SECURITY_IDS.items():
            subscribe_list.append(
                (marketfeed.NSE_FNO, sec_id, marketfeed.Full)
            )

        while not self._stop_event.is_set():
            try:
                feed = marketfeed.DhanFeed(
                    client_id=settings.dhan.client_id,
                    access_token=settings.dhan.access_token,
                    instruments=subscribe_list,
                    version='v2'  # Use v2 for latest WebSocket
                )
                feed.on_ticks = on_message
                feed.on_close = on_close
                # Run WebSocket in thread pool (blocking call)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, feed.run_forever)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self.logger.error("WebSocket error: %s. Reconnecting in 5s...", exc)
                await self.sleep(5)

    async def _handle_tick(self, data: dict) -> None:
        """Process a raw tick from the WebSocket and publish to event bus."""
        try:
            symbol = data.get("trading_symbol", "")
            ltp = float(data.get("last_price", 0))
            underlying = self._resolve_underlying(symbol)
            if underlying is None:
                return

            self._spot_prices[underlying] = ltp

            tick = Tick(
                symbol=symbol,
                underlying=underlying,
                timestamp=datetime.now(),
                ltp=ltp,
                open=float(data.get("open_price", 0)),
                high=float(data.get("high_price", 0)),
                low=float(data.get("low_price", 0)),
                close=float(data.get("close_price", 0)),
                volume=int(data.get("volume", 0)),
                oi=int(data.get("open_interest", 0)),
            )

            event = self.build_event(
                EventType.TICK_UPDATE,
                {"tick": tick.model_dump(mode="json")},
            )
            await self.publish(event)

            # Cache for fast reads
            await self._event_bus.set_cache(
                f"spot:{underlying.value}",
                str(ltp),
                ttl_seconds=10,
            )

        except Exception as exc:
            self.logger.error("Tick handling error: %s", exc)

    # ── Option chain refresh ───────────────────────────────────────────────────

    async def _option_chain_refresh_loop(self) -> None:
        """Refresh option chains every 60 seconds during market hours."""
        while not self._stop_event.is_set():
            for underlying in [Underlying.NIFTY, Underlying.BANKNIFTY]:
                await self._refresh_option_chain(underlying)
            await self.sleep(60)

    async def _refresh_option_chain(self, underlying: Underlying) -> None:
        """Fetch and cache the current option chain from Dhan."""
        try:
            expiry = self._get_nearest_expiry(underlying)
            expiry_str = expiry.strftime("%Y-%m-%d")

            # Dhan option chain API: returns all strikes for a given expiry
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: self._dhan.option_chain(
                    under_security_id=DHAN_SECURITY_IDS[underlying],
                    under_exchange_segment="IDX_I",
                    expiry=expiry_str,
                ),
            )

            if not raw or raw.get("status") != "success":
                self.logger.warning("Option chain fetch failed for %s: %s", underlying.value, raw)
                return

            spot_price = self._spot_prices.get(underlying, 0.0)
            option_chain = self._parse_option_chain(raw, underlying, expiry, spot_price)
            self._option_chains[underlying] = option_chain

            # Cache serialised chain
            await self._event_bus.set_cache(
                f"option_chain:{underlying.value}",
                option_chain.model_dump_json(),
                ttl_seconds=90,
            )

            event = self.build_event(
                EventType.OPTION_CHAIN_UPDATE,
                {"underlying": underlying.value, "expiry": expiry_str, "spot": spot_price},
            )
            await self.publish(event)

            self.logger.debug(
                "Option chain updated for %s | spot=%.2f | strikes=%d",
                underlying.value,
                spot_price,
                len(option_chain.strikes),
            )

        except Exception as exc:
            self.logger.error("Option chain refresh error for %s: %s", underlying.value, exc)

    def _parse_option_chain(
        self,
        raw: dict,
        underlying: Underlying,
        expiry: date,
        spot_price: float,
    ) -> OptionChain:
        """Convert raw Dhan option chain response to OptionChain model."""
        data = raw.get("data", {})
        oc_data = data.get("oc_data", [])

        strikes: Dict[float, Dict[str, OptionTick]] = {}
        total_ce_oi = 0
        total_pe_oi = 0

        for record in oc_data:
            strike = float(record.get("strike_price", 0))
            for opt_type_key, OptionTypeEnum in [("call_options", OptionType.CALL), ("put_options", OptionType.PUT)]:
                opt = record.get(opt_type_key, {})
                if not opt:
                    continue

                price = float(opt.get("last_price", 0))
                oi = int(opt.get("oi", 0))

                tick = OptionTick(
                    symbol=opt.get("trading_symbol", f"{underlying.value}{strike}{OptionTypeEnum.value}"),
                    underlying=underlying,
                    timestamp=datetime.now(),
                    ltp=price,
                    strike=strike,
                    option_type=OptionTypeEnum,
                    expiry=expiry,
                    bid=float(opt.get("bid_price", 0)),
                    ask=float(opt.get("ask_price", 0)),
                    bid_qty=int(opt.get("bid_qty", 0)),
                    ask_qty=int(opt.get("ask_qty", 0)),
                    volume=int(opt.get("volume", 0)),
                    oi=oi,
                )
                if strike not in strikes:
                    strikes[strike] = {}
                strikes[strike][OptionTypeEnum.value] = tick

                if OptionTypeEnum == OptionType.CALL:
                    total_ce_oi += oi
                else:
                    total_pe_oi += oi

        pcr = total_pe_oi / total_ce_oi if total_ce_oi > 0 else 0.0
        atm_strike = self._find_atm_strike(list(strikes.keys()), spot_price)
        max_pain = self._calculate_max_pain(strikes, spot_price)

        return OptionChain(
            underlying=underlying,
            spot_price=spot_price,
            timestamp=datetime.now(),
            expiry=expiry,
            strikes=strikes,
            pcr=pcr,
            max_pain=max_pain,
            atm_strike=atm_strike,
            india_vix=self._india_vix,
        )

    # ── VIX ────────────────────────────────────────────────────────────────────

    async def _vix_refresh_loop(self) -> None:
        """Refresh India VIX every 5 minutes."""
        while not self._stop_event.is_set():
            try:
                loop = asyncio.get_event_loop()
                # India VIX security id on NSE: "1"
                raw = await loop.run_in_executor(
                    None,
                    lambda: self._dhan.get_market_quote(
                        securities={"NSE_EQ": ["1"]}
                    ),
                )
                vix_data = raw.get("data", {}).get("NSE_EQ", {}).get("1", {})
                self._india_vix = float(vix_data.get("last_price", 0))
                await self._event_bus.set_cache("india_vix", str(self._india_vix), ttl_seconds=360)
            except Exception as exc:
                self.logger.warning("VIX refresh failed: %s", exc)
            await self.sleep(300)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_option_chain(self, underlying: Underlying) -> Optional[OptionChain]:
        """Synchronous access to the cached option chain (for other agents)."""
        return self._option_chains.get(underlying)

    def get_spot_price(self, underlying: Underlying) -> float:
        return self._spot_prices.get(underlying, 0.0)

    def _resolve_underlying(self, symbol: str) -> Optional[Underlying]:
        if "NIFTY BANK" in symbol or "BANKNIFTY" in symbol:
            return Underlying.BANKNIFTY
        if "NIFTY" in symbol:
            return Underlying.NIFTY
        return None

    def _get_nearest_expiry(self, underlying: Underlying) -> date:
        """
        Return the nearest weekly expiry:
          - NIFTY: Thursday expiry
          - BANKNIFTY: Wednesday expiry (since 2023)
        """
        today = date.today()
        target_weekday = 3 if underlying == Underlying.NIFTY else 2  # Thu=3, Wed=2

        days_ahead = (target_weekday - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7   # Already on expiry day — use next week's
        return today + timedelta(days=days_ahead)

    def _find_atm_strike(self, strikes: List[float], spot: float) -> float:
        if not strikes:
            return 0.0
        return min(strikes, key=lambda s: abs(s - spot))

    def _calculate_max_pain(
        self,
        strikes: Dict[float, Dict[str, OptionTick]],
        spot: float,
    ) -> float:
        """
        Max pain = strike at which total premium loss to option buyers is maximum.
        Calculated as the strike that causes the most loss to holders if expired there.
        """
        all_strikes = sorted(strikes.keys())
        if not all_strikes:
            return spot

        min_loss_strike = all_strikes[0]
        min_total_loss = float("inf")

        for test_strike in all_strikes:
            total_loss = 0.0
            for s, opts in strikes.items():
                ce = opts.get("CE")
                pe = opts.get("PE")
                if ce:
                    intrinsic = max(0.0, test_strike - s)
                    total_loss += intrinsic * ce.oi
                if pe:
                    intrinsic = max(0.0, s - test_strike)
                    total_loss += intrinsic * pe.oi

            if total_loss < min_total_loss:
                min_total_loss = total_loss
                min_loss_strike = test_strike

        return min_loss_strike
