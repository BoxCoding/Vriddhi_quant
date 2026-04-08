"""
Core domain enumerations for the NSE Options Trading system.
"""
from enum import Enum


class Exchange(str, Enum):
    NSE = "NSE"
    NFO = "NFO"   # NSE Futures & Options segment
    BSE = "BSE"


class Segment(str, Enum):
    EQUITY = "EQUITY"
    FNO = "FNO"


class Underlying(str, Enum):
    NIFTY = "NIFTY"
    BANKNIFTY = "BANKNIFTY"
    FINNIFTY = "FINNIFTY"


class OptionType(str, Enum):
    CALL = "CE"
    PUT = "PE"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LOSS = "SL"
    STOP_LOSS_MARKET = "SL-M"


class ProductType(str, Enum):
    INTRADAY = "INTRADAY"   # Dhan uses INTRADAY
    DELIVERY = "CNC"
    MARGIN = "MARGIN"       # For F&O positional


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    TRANSIT = "TRANSIT"
    FILLED = "TRADED"       # Dhan uses TRADED
    PARTIALLY_FILLED = "PART_TRADED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TradingMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class TradeStyle(str, Enum):
    INTRADAY = "INTRADAY"
    POSITIONAL = "POSITIONAL"


class SignalType(str, Enum):
    ENTER = "ENTER"
    EXIT = "EXIT"
    ADJUST = "ADJUST"    # Roll / hedge adjustment
    HEDGE = "HEDGE"


class AgentStatus(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"


class EventType(str, Enum):
    # Market data
    TICK_UPDATE = "TICK_UPDATE"
    OPTION_CHAIN_UPDATE = "OPTION_CHAIN_UPDATE"
    OHLCV_UPDATE = "OHLCV_UPDATE"

    # Greeks
    GREEKS_UPDATE = "GREEKS_UPDATE"

    # Strategy / signals
    SIGNAL_GENERATED = "SIGNAL_GENERATED"

    # Risk
    RISK_APPROVED = "RISK_APPROVED"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_ALERT = "RISK_ALERT"
    CIRCUIT_BREAKER_TRIGGERED = "CIRCUIT_BREAKER_TRIGGERED"

    # Orders
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_UPDATED = "ORDER_UPDATED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_CANCELLED = "ORDER_CANCELLED"

    # Trades
    TRADE_OPENED = "TRADE_OPENED"
    TRADE_CLOSED = "TRADE_CLOSED"
    TRADE_PNL_UPDATE = "TRADE_PNL_UPDATE"

    # System
    AGENT_STARTED = "AGENT_STARTED"
    AGENT_STOPPED = "AGENT_STOPPED"
    AGENT_ERROR = "AGENT_ERROR"
    MARKET_OPEN = "MARKET_OPEN"
    MARKET_CLOSE = "MARKET_CLOSE"
    HEARTBEAT = "HEARTBEAT"


class StrategyName(str, Enum):
    IRON_CONDOR = "IRON_CONDOR"
    BULL_CALL_SPREAD = "BULL_CALL_SPREAD"
    BEAR_PUT_SPREAD = "BEAR_PUT_SPREAD"
    SHORT_STRADDLE = "SHORT_STRADDLE"
    SHORT_STRANGLE = "SHORT_STRANGLE"
    CALENDAR_SPREAD = "CALENDAR_SPREAD"
    VWAP_MOMENTUM = "VWAP_MOMENTUM"
    DELTA_NEUTRAL = "DELTA_NEUTRAL"
    ORDER_FLOW = "ORDER_FLOW"
    VOLATILITY_ARBITRAGE = "VOLATILITY_ARBITRAGE"
    WEEKLY_THETA_DECAY = "WEEKLY_THETA_DECAY"
    GAMMA_SCALPING = "GAMMA_SCALPING"
    REGIME_BREAKOUT = "REGIME_BREAKOUT"
    OI_SHIFT_BREAKOUT = "OI_SHIFT_BREAKOUT"


class MarketCondition(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE_BOUND = "RANGE_BOUND"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
