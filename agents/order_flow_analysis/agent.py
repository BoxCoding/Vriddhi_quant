from agents.base_agent import BaseAgent

class OrderFlowAnalysisAgent(BaseAgent):
    name = "order_flow_analysis"

    async def run(self) -> None:
        self.logger.info("OrderFlowAnalysisAgent started")
        # TODO: Detect Long/Short build-ups, liquidity absorption
        while not self._stop_event.is_set():
            import asyncio
            await asyncio.sleep(1)
