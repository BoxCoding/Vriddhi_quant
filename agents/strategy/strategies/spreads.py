"""
Bull Call Spread Strategy.

Moderately bullish directional strategy.
Buy ATM/slightly-ITM Call + Sell OTM Call.
Defined risk/reward. Best when expecting 1-3% upside.

Legs:
  Leg 1: BUY lower strike CE (at or near ATM)
  Leg 2: SELL higher strike CE (OTM)
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


class BullCallSpreadStrategy(BaseStrategy):
    name = StrategyName.BULL_CALL_SPREAD.value
    description = "Buy ATM Call + Sell OTM Call. Defined-risk bullish spread."
    applicable_market_conditions = [MarketCondition.TRENDING_UP]
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
            spread_width = self.config.get("spread_width_bn", 300)
        else:
            spread_width = self.config.get("spread_width_nf", 150)

        expiry = option_chain.expiry
        lot_size = self._get_lot_size(underlying)

        # Buy ATM call
        buy_strike = option_chain.atm_strike or spot
        # Sell OTM call
        sell_strike = self._select_otm_call_strike(option_chain, spread_width)

        if not sell_strike:
            return []

        buy_price = self._get_price(option_chain, buy_strike, OptionType.CALL)
        sell_price = self._get_price(option_chain, sell_strike, OptionType.CALL)

        if buy_price <= 0 or sell_price <= 0:
            return []

        net_debit = (buy_price - sell_price) * lot_size * lots
        max_profit = (sell_strike - buy_strike - net_debit / (lot_size * lots)) * lot_size * lots

        if max_profit <= 0 or net_debit <= 0:
            return []

        legs = [
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, buy_strike, "CE"),
                underlying=underlying,
                option_type=OptionType.CALL,
                strike=buy_strike,
                expiry=expiry,
                side=OrderSide.BUY,
                quantity=lots,
                lot_size=lot_size,
                target_price=buy_price,
            ),
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, sell_strike, "CE"),
                underlying=underlying,
                option_type=OptionType.CALL,
                strike=sell_strike,
                expiry=expiry,
                side=OrderSide.SELL,
                quantity=lots,
                lot_size=lot_size,
                target_price=sell_price,
            ),
        ]

        signal = Signal(
            strategy=StrategyName.BULL_CALL_SPREAD,
            underlying=underlying,
            signal_type=SignalType.ENTER,
            trade_style=trade_style,
            legs=legs,
            confidence=0.70,
            reasoning=(
                f"Bull Call Spread on {underlying.value} | Spot: {spot:.0f} | "
                f"Buy {buy_strike} CE @ ₹{buy_price:.1f} | Sell {sell_strike} CE @ ₹{sell_price:.1f} | "
                f"Net Debit: ₹{net_debit:.0f} | Max Profit: ₹{max_profit:.0f}"
            ),
            market_condition=MarketCondition.TRENDING_UP,
            expiry=expiry,
            max_loss_estimate=net_debit,
            max_profit_estimate=max_profit,
        )

        return [signal]

    def _get_price(self, option_chain: OptionChain, strike: float, opt_type: OptionType) -> float:
        opts = option_chain.strikes.get(strike, {})
        tick = opts.get(opt_type.value)
        if tick:
            return tick.mid_price if tick.mid_price > 0 else tick.ltp
        return 0.0


class BearPutSpreadStrategy(BaseStrategy):
    """
    Bear Put Spread Strategy.

    Moderately bearish. Buy ATM Put + Sell OTM Put.
    """
    name = StrategyName.BEAR_PUT_SPREAD.value
    description = "Buy ATM Put + Sell OTM Put. Defined-risk bearish spread."
    applicable_market_conditions = [MarketCondition.TRENDING_DOWN]
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
            spread_width = self.config.get("spread_width_bn", 300)
        else:
            spread_width = self.config.get("spread_width_nf", 150)

        expiry = option_chain.expiry
        lot_size = self._get_lot_size(underlying)

        buy_strike = option_chain.atm_strike or spot
        sell_strike = self._select_otm_put_strike(option_chain, spread_width)

        if not sell_strike:
            return []

        buy_price = self._get_price(option_chain, buy_strike, OptionType.PUT)
        sell_price = self._get_price(option_chain, sell_strike, OptionType.PUT)

        if buy_price <= 0 or sell_price <= 0:
            return []

        net_debit = (buy_price - sell_price) * lot_size * lots
        max_profit = (buy_strike - sell_strike - net_debit / (lot_size * lots)) * lot_size * lots

        if max_profit <= 0 or net_debit <= 0:
            return []

        legs = [
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, buy_strike, "PE"),
                underlying=underlying,
                option_type=OptionType.PUT,
                strike=buy_strike,
                expiry=expiry,
                side=OrderSide.BUY,
                quantity=lots,
                lot_size=lot_size,
                target_price=buy_price,
            ),
            StrategyLeg(
                symbol=self._symbol(underlying, expiry, sell_strike, "PE"),
                underlying=underlying,
                option_type=OptionType.PUT,
                strike=sell_strike,
                expiry=expiry,
                side=OrderSide.SELL,
                quantity=lots,
                lot_size=lot_size,
                target_price=sell_price,
            ),
        ]

        signal = Signal(
            strategy=StrategyName.BEAR_PUT_SPREAD,
            underlying=underlying,
            signal_type=SignalType.ENTER,
            trade_style=trade_style,
            legs=legs,
            confidence=0.70,
            reasoning=(
                f"Bear Put Spread on {underlying.value} | Spot: {spot:.0f} | "
                f"Buy {buy_strike} PE @ ₹{buy_price:.1f} | Sell {sell_strike} PE @ ₹{sell_price:.1f} | "
                f"Net Debit: ₹{net_debit:.0f} | Max Profit: ₹{max_profit:.0f}"
            ),
            market_condition=MarketCondition.TRENDING_DOWN,
            expiry=expiry,
            max_loss_estimate=net_debit,
            max_profit_estimate=max_profit,
        )

        return [signal]

    def _get_price(self, option_chain: OptionChain, strike: float, opt_type: OptionType) -> float:
        opts = option_chain.strikes.get(strike, {})
        tick = opts.get(opt_type.value)
        if tick:
            return tick.mid_price if tick.mid_price > 0 else tick.ltp
        return 0.0
