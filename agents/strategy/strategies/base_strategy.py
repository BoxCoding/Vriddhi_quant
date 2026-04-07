"""
Base strategy class — all concrete strategies inherit from this.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from typing import List, Optional

from core.enums import MarketCondition, TradeStyle, Underlying
from core.models import OptionChain, Signal


class BaseStrategy(ABC):
    """
    Abstract base for all option strategies.

    Every concrete strategy must implement:
      - name: StrategyName enum value
      - applicable_market_conditions: list of conditions where this strategy works
      - generate_signals(): produce Signal objects from current market data
    """

    # Override in subclass
    name: str = "base"
    description: str = ""
    applicable_market_conditions: List[MarketCondition] = []
    applicable_trade_styles: List[TradeStyle] = [TradeStyle.INTRADAY, TradeStyle.POSITIONAL]

    def __init__(self, config: dict = None) -> None:
        self.config = config or {}

    @abstractmethod
    def generate_signals(
        self,
        underlying: Underlying,
        option_chain: OptionChain,
        trade_style: TradeStyle,
        lots: int = 1,
    ) -> List[Signal]:
        """
        Generate trading signals based on the current option chain.

        Args:
            underlying: NIFTY or BANKNIFTY
            option_chain: The enriched option chain (with Greeks)
            trade_style: INTRADAY or POSITIONAL
            lots: Number of lots per leg

        Returns:
            List of Signal objects (empty if no signal found)
        """
        ...

    def is_applicable(self, market_condition: MarketCondition) -> bool:
        """Check if this strategy works for the given market condition."""
        return market_condition in self.applicable_market_conditions

    def _get_lot_size(self, underlying: Underlying) -> int:
        from agents.market_data.agent import LOT_SIZES
        return LOT_SIZES.get(underlying, 50)

    def _select_atm_strike(self, option_chain: OptionChain) -> float:
        return option_chain.atm_strike or option_chain.spot_price

    def _select_otm_call_strike(self, option_chain: OptionChain, points_otm: float) -> Optional[float]:
        """Find the nearest available CE strike that is ~points_otm above spot."""
        target = option_chain.spot_price + points_otm
        strikes = [s for s in option_chain.strikes.keys() if "CE" in option_chain.strikes.get(s, {})]
        if not strikes:
            return None
        return min(strikes, key=lambda s: abs(s - target))

    def _select_otm_put_strike(self, option_chain: OptionChain, points_otm: float) -> Optional[float]:
        """Find the nearest available PE strike that is ~points_otm below spot."""
        target = option_chain.spot_price - points_otm
        strikes = [s for s in option_chain.strikes.keys() if "PE" in option_chain.strikes.get(s, {})]
        if not strikes:
            return None
        return min(strikes, key=lambda s: abs(s - target))

    @staticmethod
    def _symbol(underlying: Underlying, expiry: date, strike: float, opt_type: str) -> str:
        """Build a trading symbol string."""
        exp_str = expiry.strftime("%d%b%y").upper()
        return f"{underlying.value}{exp_str}{int(strike)}{opt_type}"
