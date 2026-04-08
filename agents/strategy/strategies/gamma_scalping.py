from typing import List
from agents.strategy.strategies.base_strategy import BaseStrategy
from core.enums import MarketCondition, TradeStyle, Underlying, StrategyName
from core.models import OptionChain, Signal

class GammaScalpingStrategy(BaseStrategy):
    name = StrategyName.GAMMA_SCALPING.value
    description = "Gamma Scalping Strategy."
    applicable_market_conditions = [MarketCondition.HIGH_VOLATILITY]

    def generate_signals(
        self, underlying: Underlying, option_chain: OptionChain, trade_style: TradeStyle, lots: int = 1
    ) -> List[Signal]:
        return []
