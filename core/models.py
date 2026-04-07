"""
Domain models (Pydantic v2) — shared across all agents.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, computed_field

from core.enums import (
    AgentStatus,
    AlertSeverity,
    EventType,
    Exchange,
    MarketCondition,
    OptionType,
    OrderSide,
    OrderStatus,
    OrderType,
    ProductType,
    Segment,
    SignalType,
    StrategyName,
    TradeStyle,
    Underlying,
)


def _uid() -> str:
    return str(uuid.uuid4())


# ── Market Data ──────────────────────────────────────────────────────────────

class Greeks(BaseModel):
    """Option Greeks snapshot."""
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    rho: float = 0.0
    iv: float = 0.0          # Implied Volatility (annualised, e.g. 0.18 = 18%)
    iv_rank: float = 0.0     # IV Rank 0-100
    iv_percentile: float = 0.0


class Tick(BaseModel):
    """Single price tick for any instrument."""
    symbol: str
    underlying: Underlying
    timestamp: datetime
    ltp: float               # Last Traded Price
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: int = 0
    oi: int = 0              # Open Interest


class OptionTick(Tick):
    """Price tick specifically for an option contract."""
    strike: float
    option_type: OptionType
    expiry: date
    bid: float = 0.0
    ask: float = 0.0
    bid_qty: int = 0
    ask_qty: int = 0
    greeks: Optional[Greeks] = None

    @computed_field
    @property
    def mid_price(self) -> float:
        if self.bid > 0 and self.ask > 0:
            return (self.bid + self.ask) / 2
        return self.ltp

    @computed_field
    @property
    def spread_pct(self) -> float:
        if self.mid_price > 0:
            return (self.ask - self.bid) / self.mid_price * 100
        return 0.0


class OptionChain(BaseModel):
    """Full option chain for a given underlying + expiry."""
    underlying: Underlying
    spot_price: float
    timestamp: datetime
    expiry: date
    # {strike -> {"CE": OptionTick, "PE": OptionTick}}
    strikes: Dict[float, Dict[str, OptionTick]] = Field(default_factory=dict)
    pcr: float = 0.0               # Put-Call Ratio (OI based)
    max_pain: float = 0.0          # Max pain strike
    atm_strike: float = 0.0        # At-the-money strike
    india_vix: float = 0.0


class OHLCV(BaseModel):
    """OHLCV candle for technical analysis."""
    symbol: str
    underlying: Underlying
    timestamp: datetime
    timeframe: str              # "1m", "5m", "15m", "1h", "1d"
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int = 0


# ── Signals ──────────────────────────────────────────────────────────────────

class StrategyLeg(BaseModel):
    """A single leg in a multi-leg option strategy."""
    symbol: str
    underlying: Underlying
    option_type: OptionType
    strike: float
    expiry: date
    side: OrderSide
    quantity: int                  # Number of lots
    lot_size: int                  # NSE lot size (NIFTY=50, BANKNIFTY=15)
    target_price: Optional[float] = None   # Limit price guidance
    stop_loss: Optional[float] = None

    @computed_field
    @property
    def total_quantity(self) -> int:
        return self.quantity * self.lot_size


class Signal(BaseModel):
    """Trading signal produced by the Strategy Agent."""
    id: str = Field(default_factory=_uid)
    strategy: StrategyName
    underlying: Underlying
    signal_type: SignalType
    trade_style: TradeStyle
    legs: List[StrategyLeg]
    confidence: float = Field(ge=0.0, le=1.0)   # 0.0 – 1.0
    reasoning: str                               # LLM or rule-based explanation
    market_condition: MarketCondition
    timestamp: datetime = Field(default_factory=datetime.now)
    expiry: Optional[date] = None
    max_loss_estimate: float = 0.0
    max_profit_estimate: float = 0.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Orders ───────────────────────────────────────────────────────────────────

class OrderRequest(BaseModel):
    """Request to place an order (pre-broker)."""
    symbol: str
    underlying: Underlying
    exchange: Exchange = Exchange.NFO
    segment: Segment = Segment.FNO
    option_type: Optional[OptionType] = None
    strike: Optional[float] = None
    expiry: Optional[date] = None
    side: OrderSide
    order_type: OrderType
    product_type: ProductType = ProductType.INTRADAY
    quantity: int
    price: Optional[float] = None
    trigger_price: Optional[float] = None
    trade_style: TradeStyle = TradeStyle.INTRADAY
    signal_id: Optional[str] = None
    trade_id: Optional[str] = None
    tag: Optional[str] = None


class Order(OrderRequest):
    """Placed order with broker tracking fields."""
    id: str = Field(default_factory=_uid)
    broker_order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: int = 0
    average_price: float = 0.0
    placed_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    filled_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None

    @computed_field
    @property
    def is_complete(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED)


# ── Positions & Portfolio ────────────────────────────────────────────────────

class Position(BaseModel):
    """Live open position."""
    id: str = Field(default_factory=_uid)
    symbol: str
    underlying: Underlying
    option_type: Optional[OptionType] = None
    strike: Optional[float] = None
    expiry: Optional[date] = None
    side: OrderSide
    quantity: int                  # Signed: +ve = long, -ve = short
    average_price: float
    ltp: float
    trade_style: TradeStyle
    trade_id: Optional[str] = None
    greeks: Optional[Greeks] = None
    opened_at: datetime = Field(default_factory=datetime.now)

    @computed_field
    @property
    def unrealized_pnl(self) -> float:
        if self.side == OrderSide.BUY:
            return (self.ltp - self.average_price) * abs(self.quantity)
        return (self.average_price - self.ltp) * abs(self.quantity)


class PortfolioGreeks(BaseModel):
    """Aggregated Greeks across all open positions."""
    net_delta: float = 0.0
    net_gamma: float = 0.0
    net_theta: float = 0.0
    net_vega: float = 0.0
    net_rho: float = 0.0


class Portfolio(BaseModel):
    """Live portfolio snapshot."""
    positions: List[Position] = Field(default_factory=list)
    greeks: PortfolioGreeks = Field(default_factory=PortfolioGreeks)
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_pnl: float = 0.0
    margin_used: float = 0.0
    margin_available: float = 0.0
    updated_at: datetime = Field(default_factory=datetime.now)

    @computed_field
    @property
    def total_positions(self) -> int:
        return len(self.positions)


# ── Risk ─────────────────────────────────────────────────────────────────────

class RiskAssessment(BaseModel):
    """Result of the Risk Management Agent's evaluation of a signal."""
    signal_id: str
    approved: bool
    reasons: List[str] = Field(default_factory=list)
    max_loss_estimate: float = 0.0
    margin_required: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)


class RiskAlert(BaseModel):
    """Real-time risk alert generated by the Risk Management Agent."""
    id: str = Field(default_factory=_uid)
    severity: AlertSeverity
    title: str
    message: str
    metric_name: str
    current_value: float
    limit_value: float
    timestamp: datetime = Field(default_factory=datetime.now)


# ── Trades ───────────────────────────────────────────────────────────────────

class Trade(BaseModel):
    """A complete trade (entry + exit) grouping multiple orders."""
    id: str = Field(default_factory=_uid)
    strategy: StrategyName
    underlying: Underlying
    signal_id: str
    trade_style: TradeStyle
    orders: List[Order] = Field(default_factory=list)
    entry_time: datetime = Field(default_factory=datetime.now)
    exit_time: Optional[datetime] = None
    realized_pnl: Optional[float] = None
    is_open: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ── Events (Event Bus) ───────────────────────────────────────────────────────

class Event(BaseModel):
    """Generic event published to the Redis Streams event bus."""
    id: str = Field(default_factory=_uid)
    type: EventType
    source_agent: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)
    correlation_id: Optional[str] = None    # Link related events (e.g., signal → order chain)


# ── Agent ────────────────────────────────────────────────────────────────────

class AgentHeartbeat(BaseModel):
    """Heartbeat published by each agent to the event bus."""
    agent_id: str
    agent_name: str
    status: AgentStatus
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: Dict[str, Any] = Field(default_factory=dict)
