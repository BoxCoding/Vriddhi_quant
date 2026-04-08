from typing import List
from agents.strategy.strategies.base_strategy import BaseStrategy
from core.enums import MarketCondition, TradeStyle, Underlying, StrategyName
from core.models import OptionChain, Signal

class OrderFlowStrategy(BaseStrategy):
    name = StrategyName.ORDER_FLOW.value
    description = "Order Flow Strategy based on OI + Volume."
    applicable_market_conditions = [MarketCondition.TRENDING_UP, MarketCondition.TRENDING_DOWN]

    def generate_signals(
        self, underlying: Underlying, option_chain: OptionChain, trade_style: TradeStyle, lots: int = 1
    ) -> List[Signal]:
        return []
