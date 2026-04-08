from typing import List
from agents.strategy.strategies.base_strategy import BaseStrategy
from core.enums import MarketCondition, TradeStyle, Underlying, StrategyName
from core.models import OptionChain, Signal

class WeeklyThetaDecayStrategy(BaseStrategy):
    name = StrategyName.WEEKLY_THETA_DECAY.value
    description = "Weekly Expiry Theta Decay."
    applicable_market_conditions = [MarketCondition.RANGE_BOUND]

    def generate_signals(
        self, underlying: Underlying, option_chain: OptionChain, trade_style: TradeStyle, lots: int = 1
    ) -> List[Signal]:
        return []
