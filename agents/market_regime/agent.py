from agents.base_agent import BaseAgent

class MarketRegimeAgent(BaseAgent):
    name = "market_regime"

    async def run(self) -> None:
        self.logger.info("MarketRegimeAgent started")
        # TODO: Determine Trend vs. Range vs. High Volatility states
        while not self._stop_event.is_set():
            import asyncio
            await asyncio.sleep(1)
