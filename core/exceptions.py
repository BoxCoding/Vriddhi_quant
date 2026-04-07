"""
Custom exception hierarchy for the NSE Options Trading system.
"""


class TradingSystemError(Exception):
    """Base exception for all trading system errors."""
    pass


# ── Broker / API ─────────────────────────────────────────────────────────────

class BrokerError(TradingSystemError):
    """Error communicating with the broker API."""
    pass


class AuthenticationError(BrokerError):
    """Invalid or expired broker credentials."""
    pass


class OrderRejectedError(BrokerError):
    """Broker rejected the order."""
    def __init__(self, message: str, reason: str = ""):
        super().__init__(message)
        self.reason = reason


class InsufficientMarginError(BrokerError):
    """Insufficient margin to place the order."""
    pass


# ── Risk Management ──────────────────────────────────────────────────────────

class RiskViolationError(TradingSystemError):
    """A risk limit has been breached."""
    def __init__(self, message: str, limit_name: str = "", current_value: float = 0.0, limit_value: float = 0.0):
        super().__init__(message)
        self.limit_name = limit_name
        self.current_value = current_value
        self.limit_value = limit_value


class CircuitBreakerError(RiskViolationError):
    """Daily loss circuit breaker triggered — trading is halted."""
    pass


class MaxLossExceededError(RiskViolationError):
    """Per-trade max loss limit exceeded."""
    pass


class PositionSizeLimitError(RiskViolationError):
    """Position size exceeds allowed limit."""
    pass


class DeltaLimitError(RiskViolationError):
    """Portfolio net delta exceeds allowed limit."""
    pass


# ── Market Data ──────────────────────────────────────────────────────────────

class MarketDataError(TradingSystemError):
    """Error fetching or processing market data."""
    pass


class OptionChainNotAvailableError(MarketDataError):
    """Option chain data is not available for the requested instrument."""
    pass


class StaleDataError(MarketDataError):
    """Market data is stale / outdated."""
    def __init__(self, message: str, age_seconds: float = 0.0):
        super().__init__(message)
        self.age_seconds = age_seconds


# ── Greeks ───────────────────────────────────────────────────────────────────

class GreeksComputationError(TradingSystemError):
    """Error computing option Greeks."""
    pass


class IVSolverError(GreeksComputationError):
    """Could not solve for Implied Volatility (Newton-Raphson failed to converge)."""
    pass


# ── Strategy ─────────────────────────────────────────────────────────────────

class StrategyError(TradingSystemError):
    """Error within a trading strategy."""
    pass


class InsufficientLiquidityError(StrategyError):
    """Not enough liquidity at the desired strike to enter the strategy."""
    pass


class NoSignalError(StrategyError):
    """Strategy found no valid signal under current market conditions."""
    pass


# ── Agent / System ───────────────────────────────────────────────────────────

class AgentError(TradingSystemError):
    """Generic agent runtime error."""
    pass


class EventBusError(TradingSystemError):
    """Error publishing/consuming events on the event bus."""
    pass


class ConfigurationError(TradingSystemError):
    """Invalid configuration value."""
    pass
