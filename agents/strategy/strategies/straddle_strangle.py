"""
Short Straddle & Short Strangle Strategies.

Short Straddle: Sell ATM Call + Sell ATM Put. Max premium, but unlimited risk.
Short Strangle: Sell OTM Call + Sell OTM Put. Lower premium, wider profit range.

Both work best when: high IV Rank + expected low realized volatility.
"""
from __future__ import annotations

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


class ShortStraddleStrategy(BaseStrategy):
    name = StrategyName.SHORT_STRADDLE.value
    description = "Sell ATM Call + Sell ATM Put. Profit if market stays near current level."
    applicable_market_conditions = [MarketCondition.RANGE_BOUND, MarketCondition.LOW_VOLATILITY]
    applicable_trade_styles = [TradeStyle.INTRADAY]

    DEFAULT_MIN_IV_RANK = 60.0   # Higher threshold — riskier strategy

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

        min_iv_rank = self.config.get("min_iv_rank", self.DEFAULT_MIN_IV_RANK)

        atm_strike = option_chain.atm_strike or spot
        expiry = option_chain.expiry
        lot_size = self._get_lot_size(underlying)

        # IV Rank check
        atm_opts = option_chain.strikes.get(atm_strike, {})
        atm_ce = atm_opts.get(OptionType.CALL.value)
        iv_rank = atm_ce.greeks.iv_rank if (atm_ce and atm_ce.greeks) else 0.0

        if iv_rank < min_iv_rank:
            return []

        ce_price = self._get_price(option_chain, atm_strike, OptionType.CALL)
        pe_price = self._get_price(option_chain, atm_strike, OptionType.PUT)

        if ce_price <= 0 or pe_price <= 0:
            return []

        net_credit = (ce_price + pe_price) * lot_size * lots

        legs = [
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, atm_strike, "CE"),
                underlying=underlying,
                option_type=OptionType.CALL,
                strike=atm_strike,
                expiry=expiry,
                side=OrderSide.SELL,
                quantity=lots,
                lot_size=lot_size,
                target_price=ce_price,
                stop_loss=ce_price * 2,   # SL: 2× premium sold
            ),
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, atm_strike, "PE"),
                underlying=underlying,
                option_type=OptionType.PUT,
                strike=atm_strike,
                expiry=expiry,
                side=OrderSide.SELL,
                quantity=lots,
                lot_size=lot_size,
                target_price=pe_price,
                stop_loss=pe_price * 2,
            ),
        ]

        signal = Signal(
            strategy=StrategyName.SHORT_STRADDLE,
            underlying=underlying,
            signal_type=SignalType.ENTER,
            trade_style=trade_style,
            legs=legs,
            confidence=round(min(0.90, 0.55 + iv_rank / 250), 2),
            reasoning=(
                f"Short Straddle on {underlying.value} | Spot: {spot:.0f} | "
                f"ATM Strike: {atm_strike} | CE: ₹{ce_price:.1f} | PE: ₹{pe_price:.1f} | "
                f"Net Credit: ₹{net_credit:.0f} | IV Rank: {iv_rank:.1f}"
            ),
            market_condition=MarketCondition.RANGE_BOUND,
            expiry=expiry,
            max_loss_estimate=float("inf"),   # Technically unlimited
            max_profit_estimate=net_credit,
        )

        return [signal]

    def _get_price(self, option_chain: OptionChain, strike: float, opt_type: OptionType) -> float:
        opts = option_chain.strikes.get(strike, {})
        tick = opts.get(opt_type.value)
        if tick:
            return tick.mid_price if tick.mid_price > 0 else tick.ltp
        return 0.0


class ShortStrangleStrategy(BaseStrategy):
    name = StrategyName.SHORT_STRANGLE.value
    description = "Sell OTM Call + Sell OTM Put. Wider profit range than Straddle."
    applicable_market_conditions = [MarketCondition.RANGE_BOUND, MarketCondition.LOW_VOLATILITY]
    applicable_trade_styles = [TradeStyle.INTRADAY, TradeStyle.POSITIONAL]

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

        if underlying == Underlying.BANKNIFTY:
            otm_offset = self.config.get("otm_offset_bn", 300)
        else:
            otm_offset = self.config.get("otm_offset_nf", 150)

        min_iv_rank = self.config.get("min_iv_rank", 50.0)

        expiry = option_chain.expiry
        lot_size = self._get_lot_size(underlying)

        atm_strike = option_chain.atm_strike or spot
        atm_opts = option_chain.strikes.get(atm_strike, {})
        atm_ce = atm_opts.get(OptionType.CALL.value)
        iv_rank = atm_ce.greeks.iv_rank if (atm_ce and atm_ce.greeks) else 0.0

        if iv_rank < min_iv_rank:
            return []

        short_call_strike = self._select_otm_call_strike(option_chain, otm_offset)
        short_put_strike = self._select_otm_put_strike(option_chain, otm_offset)

        if not short_call_strike or not short_put_strike:
            return []

        ce_price = self._get_price(option_chain, short_call_strike, OptionType.CALL)
        pe_price = self._get_price(option_chain, short_put_strike, OptionType.PUT)

        if ce_price <= 0 or pe_price <= 0:
            return []

        net_credit = (ce_price + pe_price) * lot_size * lots

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
                target_price=ce_price,
                stop_loss=ce_price * 2.5,
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
                target_price=pe_price,
                stop_loss=pe_price * 2.5,
            ),
        ]

        signal = Signal(
            strategy=StrategyName.SHORT_STRANGLE,
            underlying=underlying,
            signal_type=SignalType.ENTER,
            trade_style=trade_style,
            legs=legs,
            confidence=round(min(0.85, 0.50 + iv_rank / 200), 2),
            reasoning=(
                f"Short Strangle on {underlying.value} | Spot: {spot:.0f} | "
                f"Short CE: {short_call_strike} @ ₹{ce_price:.1f} | Short PE: {short_put_strike} @ ₹{pe_price:.1f} | "
                f"Net Credit: ₹{net_credit:.0f} | IV Rank: {iv_rank:.1f}"
            ),
            market_condition=MarketCondition.RANGE_BOUND,
            expiry=expiry,
            max_loss_estimate=float("inf"),
            max_profit_estimate=net_credit,
        )

        return [signal]

    def _get_price(self, option_chain: OptionChain, strike: float, opt_type: OptionType) -> float:
        opts = option_chain.strikes.get(strike, {})
        tick = opts.get(opt_type.value)
        if tick:
            return tick.mid_price if tick.mid_price > 0 else tick.ltp
        return 0.0
