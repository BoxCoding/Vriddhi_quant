from typing import List
from agents.strategy.strategies.base_strategy import BaseStrategy
from core.enums import MarketCondition, TradeStyle, Underlying, StrategyName
from core.models import OptionChain, Signal

class RegimeBreakoutStrategy(BaseStrategy):
    name = StrategyName.REGIME_BREAKOUT.value
    description = "Regime Switching Breakout Strategy."
    applicable_market_conditions = [MarketCondition.TRENDING_UP, MarketCondition.TRENDING_DOWN]

    def generate_signals(
        self, underlying: Underlying, option_chain: OptionChain, trade_style: TradeStyle, lots: int = 1
    ) -> List[Signal]:
        return []
