from agents.base_agent import BaseAgent

class HedgingAgent(BaseAgent):
    name = "hedging"

    async def run(self) -> None:
        self.logger.info("HedgingAgent started")
        # TODO: Delta and tail risk hedging
        while not self._stop_event.is_set():
            import asyncio
            await asyncio.sleep(1)
