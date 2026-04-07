"""
Strategy Agent — LangGraph + Google Gemini LLM.

Responsibilities:
  1. Receive Greeks updates from the event bus.
  2. Assess current market condition (trending/range-bound/volatile) using
     technical indicators and option chain data.
  3. Query the Gemini LLM as a "trading analyst" to validate the market
     condition assessment and choose the most appropriate strategy.
  4. Generate a Signal from the chosen strategy.
  5. Publish the Signal as a SIGNAL_GENERATED event.

Architecture:
  Uses LangGraph StateGraph with the following nodes:
    [market_analysis] → [llm_strategy_selection] → [signal_generation] → [publish]
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, TypedDict

import google.generativeai as genai
from langgraph.graph import END, StateGraph

from agents.base_agent import BaseAgent
from agents.strategy.strategies.base_strategy import BaseStrategy
from agents.strategy.strategies.iron_condor import IronCondorStrategy
from agents.strategy.strategies.spreads import BearPutSpreadStrategy, BullCallSpreadStrategy
from agents.strategy.strategies.straddle_strangle import ShortStrangleStrategy, ShortStraddleStrategy
from core.config import settings
from core.enums import EventType, MarketCondition, StrategyName, TradeStyle, Underlying
from core.models import Event, OptionChain, Signal

logger = logging.getLogger(__name__)


# ── LangGraph State ───────────────────────────────────────────────────────────

class StrategyState(TypedDict):
    underlying: str
    spot_price: float
    option_chain_json: str
    iv_rank: float
    iv_percentile: float
    pcr: float
    india_vix: float
    trade_style: str
    market_condition: Optional[str]
    chosen_strategy: Optional[str]
    llm_reasoning: Optional[str]
    signals: List[Dict]
    error: Optional[str]


# ── Strategy Registry ─────────────────────────────────────────────────────────

STRATEGY_REGISTRY: Dict[str, BaseStrategy] = {
    StrategyName.IRON_CONDOR.value: IronCondorStrategy(),
    StrategyName.BULL_CALL_SPREAD.value: BullCallSpreadStrategy(),
    StrategyName.BEAR_PUT_SPREAD.value: BearPutSpreadStrategy(),
    StrategyName.SHORT_STRADDLE.value: ShortStraddleStrategy(),
    StrategyName.SHORT_STRANGLE.value: ShortStrangleStrategy(),
}


# ── LangGraph Nodes ───────────────────────────────────────────────────────────

def node_market_analysis(state: StrategyState) -> StrategyState:
    """
    Rule-based market condition assessment based on:
      - IV Rank (high = sell premium, low = buy options)
      - PCR (>1.2 = bearish, <0.8 = bullish, else neutral)
      - India VIX (>20 = high vol, <14 = low vol)
    """
    iv_rank = state["iv_rank"]
    pcr = state["pcr"]
    vix = state["india_vix"]

    if vix > 20 or iv_rank > 70:
        condition = MarketCondition.HIGH_VOLATILITY
    elif iv_rank < 30 and vix < 14:
        condition = MarketCondition.LOW_VOLATILITY
    elif pcr > 1.3:
        condition = MarketCondition.TRENDING_DOWN
    elif pcr < 0.75:
        condition = MarketCondition.TRENDING_UP
    else:
        condition = MarketCondition.RANGE_BOUND

    state["market_condition"] = condition.value
    return state


async def node_llm_strategy_selection(state: StrategyState, model) -> StrategyState:
    """
    Ask Gemini to act as a quantitative options trading analyst and:
      1. Confirm or refine the rule-based market condition.
      2. Choose the most appropriate strategy.
      3. Provide a brief justification.
    """
    prompt = f"""You are an expert quantitative options trading analyst for the Indian stock market (NSE).

Current market snapshot for {state['underlying']}:
- Spot Price: ₹{state['spot_price']:.2f}
- ATM IV Rank: {state['iv_rank']:.1f}/100 (>60 = expensive options, sell premium; <30 = cheap, buy options)
- ATM IV Percentile: {state['iv_percentile']:.1f}%
- Put-Call Ratio (OI): {state['pcr']:.2f} (>1.2 = bearish tilt, <0.8 = bullish tilt)
- India VIX: {state['india_vix']:.2f} (>20 = high fear, <14 = complacent)
- Preliminary market condition: {state['market_condition']}
- Trading style requested: {state['trade_style']}

Available strategies:
1. IRON_CONDOR — Range-bound, high IV Rank. Sell OTM call spread + put spread.
2. BULL_CALL_SPREAD — Moderately bullish. Buy ATM call, sell OTM call.
3. BEAR_PUT_SPREAD — Moderately bearish. Buy ATM put, sell OTM put.
4. SHORT_STRADDLE — High IV Rank, very low expected move. Sell ATM call + put.
5. SHORT_STRANGLE — High IV Rank, low expected move. Sell OTM call + put.

Respond ONLY in the following JSON format, no other text:
{{
  "market_condition": "<TRENDING_UP|TRENDING_DOWN|RANGE_BOUND|HIGH_VOLATILITY|LOW_VOLATILITY|UNKNOWN>",
  "chosen_strategy": "<STRATEGY_NAME or null if no trade>",
  "confidence": <0.0 to 1.0>,
  "reasoning": "<2-3 sentence explanation>"
}}"""

    try:
        response = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: model.generate_content(prompt),
        )
        raw = response.text.strip()

        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        parsed = json.loads(raw)
        state["market_condition"] = parsed.get("market_condition", state["market_condition"])
        state["chosen_strategy"] = parsed.get("chosen_strategy")
        state["llm_reasoning"] = parsed.get("reasoning", "")

        logger.info(
            "Gemini | %s | Condition: %s | Strategy: %s | Confidence: %.2f",
            state["underlying"],
            state["market_condition"],
            state["chosen_strategy"],
            parsed.get("confidence", 0),
        )

    except Exception as exc:
        logger.error("LLM strategy selection failed: %s", exc)
        state["error"] = str(exc)
        # Fall back to rule-based: pick Iron Condor for range-bound, else no trade
        if state["market_condition"] == MarketCondition.RANGE_BOUND.value:
            state["chosen_strategy"] = StrategyName.IRON_CONDOR.value
        elif state["market_condition"] == MarketCondition.TRENDING_UP.value:
            state["chosen_strategy"] = StrategyName.BULL_CALL_SPREAD.value
        elif state["market_condition"] == MarketCondition.TRENDING_DOWN.value:
            state["chosen_strategy"] = StrategyName.BEAR_PUT_SPREAD.value

    return state


def node_signal_generation(state: StrategyState) -> StrategyState:
    """Run the chosen strategy to produce Signal objects."""
    strategy_name = state.get("chosen_strategy")
    if not strategy_name:
        state["signals"] = []
        return state

    strategy = STRATEGY_REGISTRY.get(strategy_name)
    if not strategy:
        logger.warning("Unknown strategy: %s", strategy_name)
        state["signals"] = []
        return state

    try:
        underlying = Underlying(state["underlying"])
        option_chain = OptionChain.model_validate_json(state["option_chain_json"])
        trade_style = TradeStyle(state["trade_style"])

        signals = strategy.generate_signals(
            underlying=underlying,
            option_chain=option_chain,
            trade_style=trade_style,
            lots=1,
        )

        # Enrich reasoning with LLM insight
        for sig in signals:
            if state.get("llm_reasoning"):
                sig.reasoning = f"[Gemini] {state['llm_reasoning']}\n\n[Rule] {sig.reasoning}"

        state["signals"] = [s.model_dump(mode="json") for s in signals]

    except Exception as exc:
        logger.error("Signal generation failed: %s", exc)
        state["error"] = str(exc)
        state["signals"] = []

    return state


# ── Strategy Agent ────────────────────────────────────────────────────────────

class StrategyAgent(BaseAgent):
    """
    LangGraph + Gemini-powered strategy agent.
    Subscribes to GREEKS_UPDATE events and publishes SIGNAL_GENERATED events.
    """

    name = "strategy_agent"

    def __init__(self) -> None:
        super().__init__()
        self._gemini_model = self._init_gemini()
        self._graph = self._build_graph()
        # Throttle: re-evaluate each underlying at most every N seconds
        self._last_run: Dict[str, datetime] = {}

    def _init_gemini(self):
        """Initialise Google Gemini model."""
        genai.configure(api_key=settings.gemini.api_key)
        model = genai.GenerativeModel(
            model_name=settings.gemini.model,
            generation_config=genai.types.GenerationConfig(
                temperature=settings.gemini.temperature,
                max_output_tokens=settings.gemini.max_tokens,
            ),
        )
        self.logger.info("Gemini model %s initialised", settings.gemini.model)
        return model

    def _build_graph(self) -> Any:
        """Build the LangGraph StateGraph for strategy selection."""
        graph = StateGraph(StrategyState)

        graph.add_node("market_analysis", node_market_analysis)
        graph.add_node(
            "llm_strategy_selection",
            lambda state: asyncio.get_event_loop().run_until_complete(
                node_llm_strategy_selection(state, self._gemini_model)
            ),
        )
        graph.add_node("signal_generation", node_signal_generation)

        graph.set_entry_point("market_analysis")
        graph.add_edge("market_analysis", "llm_strategy_selection")
        graph.add_edge("llm_strategy_selection", "signal_generation")
        graph.add_edge("signal_generation", END)

        return graph.compile()

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Subscribe to Greeks updates and trigger strategy evaluation."""
        async for event in self._event_bus.subscribe(
            agent_name=self.name,
            event_types=[EventType.GREEKS_UPDATE.value],
        ):
            if self._stop_event.is_set():
                break
            await self._on_greeks_update(event)

    async def _on_greeks_update(self, event: Event) -> None:
        underlying_str = event.payload.get("underlying")
        if not underlying_str:
            return

        # Throttle: max once per 5-minute evaluation window
        now = datetime.now()
        last = self._last_run.get(underlying_str)
        cooldown = settings.strategy.re_evaluation_interval_seconds
        if last and (now - last).total_seconds() < cooldown:
            return

        self._last_run[underlying_str] = now

        # Fetch cached option chain (with Greeks)
        cached_oc = await self._event_bus.get_cache(f"option_chain_greeks:{underlying_str}")
        if not cached_oc:
            # Fall back to non-Greeks chain
            cached_oc = await self._event_bus.get_cache(f"option_chain:{underlying_str}")
        if not cached_oc:
            self.logger.warning("No option chain available for %s", underlying_str)
            return

        # Run both intraday and positional evaluations
        for trade_style in [TradeStyle.INTRADAY, TradeStyle.POSITIONAL]:
            await self._run_graph(
                underlying_str=underlying_str,
                option_chain_json=cached_oc,
                greeks_payload=event.payload,
                trade_style=trade_style,
            )

    async def _run_graph(
        self,
        underlying_str: str,
        option_chain_json: str,
        greeks_payload: dict,
        trade_style: TradeStyle,
    ) -> None:
        """Execute the LangGraph pipeline for one underlying × trade style."""
        try:
            initial_state: StrategyState = {
                "underlying": underlying_str,
                "spot_price": greeks_payload.get("spot", 0.0),
                "option_chain_json": option_chain_json,
                "iv_rank": greeks_payload.get("iv_rank", 0.0),
                "iv_percentile": greeks_payload.get("iv_percentile", 0.0),
                "pcr": 1.0,   # Will be enriched from option chain
                "india_vix": greeks_payload.get("india_vix", 15.0),
                "trade_style": trade_style.value,
                "market_condition": None,
                "chosen_strategy": None,
                "llm_reasoning": None,
                "signals": [],
                "error": None,
            }

            # Enrich PCR from option chain
            try:
                oc = OptionChain.model_validate_json(option_chain_json)
                initial_state["pcr"] = oc.pcr
                initial_state["india_vix"] = oc.india_vix or initial_state["india_vix"]
            except Exception:
                pass

            # Run graph in thread pool (LangGraph is sync)
            loop = asyncio.get_event_loop()
            final_state = await loop.run_in_executor(
                None,
                lambda: self._graph.invoke(initial_state),
            )

            signals_raw = final_state.get("signals", [])
            for sig_dict in signals_raw:
                signal = Signal.model_validate(sig_dict)
                event = self.build_event(
                    EventType.SIGNAL_GENERATED,
                    {"signal": signal.model_dump(mode="json")},
                    correlation_id=signal.id,
                )
                await self.publish(event)
                self.logger.info(
                    "Signal: %s | %s | %s | Confidence: %.2f",
                    signal.strategy.value,
                    underlying_str,
                    trade_style.value,
                    signal.confidence,
                )

        except Exception as exc:
            self.logger.error("Strategy graph error for %s/%s: %s", underlying_str, trade_style.value, exc)
