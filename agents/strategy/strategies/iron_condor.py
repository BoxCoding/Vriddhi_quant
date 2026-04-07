"""
Iron Condor Strategy.

A market-neutral options strategy that profits when the underlying stays
within a defined range. Sell an OTM call spread + sell an OTM put spread.

Best for: Range-bound/low-volatility markets, high IV Rank (premium selling).

Structure:
  Leg 1: SELL OTM Call (short call)
  Leg 2: BUY further OTM Call (protection)
  Leg 3: SELL OTM Put (short put)
  Leg 4: BUY further OTM Put (protection)
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List

from agents.strategy.strategies.base_strategy import BaseStrategy
from core.enums import (
    MarketCondition,
    OptionType,
    OrderSide,
    SignalType,
    StrategyName,
    TradeStyle,
    Underlying,
)
from core.models import OptionChain, Signal, StrategyLeg


class IronCondorStrategy(BaseStrategy):
    name = StrategyName.IRON_CONDOR.value
    description = (
        "Sell OTM Call Spread + Sell OTM Put Spread. "
        "Profit zone: between the two short strikes."
    )
    applicable_market_conditions = [
        MarketCondition.RANGE_BOUND,
        MarketCondition.LOW_VOLATILITY,
    ]
    applicable_trade_styles = [TradeStyle.INTRADAY, TradeStyle.POSITIONAL]

    # Strategy defaults (overridable via config)
    DEFAULT_SHORT_CALL_OFFSET = 200   # Short Call: ATM + 200 pts (NIFTY)
    DEFAULT_WING_WIDTH = 100           # Width of each spread wing
    DEFAULT_MIN_IV_RANK = 40.0         # Only trade if IV Rank >= 40

    def generate_signals(
        self,
        underlying: Underlying,
        option_chain: OptionChain,
        trade_style: TradeStyle,
        lots: int = 1,
    ) -> List[Signal]:

        spot = option_chain.spot_price
        if spot <= 0:
            return []

        # Adjust offsets for BANKNIFTY (higher premium, wider strikes)
        if underlying == Underlying.BANKNIFTY:
            short_offset = self.config.get("short_call_offset_bn", 400)
            wing_width = self.config.get("wing_width_bn", 200)
        else:
            short_offset = self.config.get("short_call_offset_nf", self.DEFAULT_SHORT_CALL_OFFSET)
            wing_width = self.config.get("wing_width_nf", self.DEFAULT_WING_WIDTH)

        # Minimum IV rank check
        min_iv_rank = self.config.get("min_iv_rank", self.DEFAULT_MIN_IV_RANK)

        expiry = option_chain.expiry
        lot_size = self._get_lot_size(underlying)

        # ── Find strikes ───────────────────────────────────────────────────────

        short_call_strike = self._select_otm_call_strike(option_chain, short_offset)
        short_put_strike = self._select_otm_put_strike(option_chain, short_offset)

        if not short_call_strike or not short_put_strike:
            return []

        long_call_strike = self._select_otm_call_strike(option_chain, short_offset + wing_width)
        long_put_strike = self._select_otm_put_strike(option_chain, short_offset + wing_width)

        if not long_call_strike or not long_put_strike:
            return []

        # ── Check IV rank from ATM strike's Greeks ─────────────────────────────

        atm_strike = option_chain.atm_strike or spot
        atm_opts = option_chain.strikes.get(atm_strike, {})
        atm_ce = atm_opts.get(OptionType.CALL.value)
        iv_rank = atm_ce.greeks.iv_rank if (atm_ce and atm_ce.greeks) else 0.0

        if iv_rank < min_iv_rank:
            return []   # Not enough IV premium to sell

        # ── Premium collected ──────────────────────────────────────────────────

        sc_price = self._get_price(option_chain, short_call_strike, OptionType.CALL)
        lc_price = self._get_price(option_chain, long_call_strike, OptionType.CALL)
        sp_price = self._get_price(option_chain, short_put_strike, OptionType.PUT)
        lp_price = self._get_price(option_chain, long_put_strike, OptionType.PUT)

        net_credit = (sc_price - lc_price + sp_price - lp_price) * lot_size * lots

        if net_credit <= 0:
            return []   # No credit — skip

        # Max loss = wing width - net credit (per lot)
        max_loss = (wing_width - (sc_price - lc_price + sp_price - lp_price)) * lot_size * lots

        # ── Build legs ────────────────────────────────────────────────────────

        legs = [
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, short_call_strike, "CE"),
                underlying=underlying,
                option_type=OptionType.CALL,
                strike=short_call_strike,
                expiry=expiry,
                side=OrderSide.SELL,
                quantity=lots,
                lot_size=lot_size,
                target_price=sc_price,
            ),
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, long_call_strike, "CE"),
                underlying=underlying,
                option_type=OptionType.CALL,
                strike=long_call_strike,
                expiry=expiry,
                side=OrderSide.BUY,
                quantity=lots,
                lot_size=lot_size,
                target_price=lc_price,
            ),
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, short_put_strike, "PE"),
                underlying=underlying,
                option_type=OptionType.PUT,
                strike=short_put_strike,
                expiry=expiry,
                side=OrderSide.SELL,
                quantity=lots,
                lot_size=lot_size,
                target_price=sp_price,
            ),
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, long_put_strike, "PE"),
                underlying=underlying,
                option_type=OptionType.PUT,
                strike=long_put_strike,
                expiry=expiry,
                side=OrderSide.BUY,
                quantity=lots,
                lot_size=lot_size,
                target_price=lp_price,
            ),
        ]

        signal = Signal(
            strategy=StrategyName.IRON_CONDOR,
            underlying=underlying,
            signal_type=SignalType.ENTER,
            trade_style=trade_style,
            legs=legs,
            confidence=round(min(0.95, 0.5 + iv_rank / 200), 2),
            reasoning=(
                f"Iron Condor on {underlying.value} | Spot: {spot:.0f} | "
                f"Short Call: {short_call_strike} | Short Put: {short_put_strike} | "
                f"Net Credit: ₹{net_credit:.0f} | IV Rank: {iv_rank:.1f}"
            ),
            market_condition=MarketCondition.RANGE_BOUND,
            expiry=expiry,
            max_loss_estimate=max_loss,
            max_profit_estimate=net_credit,
        )

        return [signal]

    def _get_price(self, option_chain: OptionChain, strike: float, opt_type: OptionType) -> float:
        opts = option_chain.strikes.get(strike, {})
        tick = opts.get(opt_type.value)
        if tick:
            return tick.mid_price if tick.mid_price > 0 else tick.ltp
        return 0.0
