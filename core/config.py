"""
Centralised application configuration loaded from environment variables.
Use a .env file for local development; use secrets manager in production.
"""
from functools import lru_cache
from typing import List, Optional
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from core.enums import TradingMode, Underlying


class DhanConfig(BaseSettings):
    """Dhan broker API credentials."""
    model_config = SettingsConfigDict(env_prefix="DHAN_", env_file=".env", extra="ignore")

    client_id: str
    access_token: str


class GeminiConfig(BaseSettings):
    """Google Gemini LLM configuration."""
    model_config = SettingsConfigDict(env_prefix="GEMINI_", env_file=".env", extra="ignore")

    api_key: str
    model: str = "gemini-2.0-flash-exp"
    temperature: float = 0.2
    max_tokens: int = 8192


class OllamaConfig(BaseSettings):
    """Local Ollama LLM configuration for low-latency tasks."""
    model_config = SettingsConfigDict(env_prefix="OLLAMA_", env_file=".env", extra="ignore")

    base_url: str = "http://localhost:11434"
    model: str = "llama3"
    temperature: float = 0.1


class RedisConfig(BaseSettings):
    """Redis connection config (used as event bus + cache)."""
    model_config = SettingsConfigDict(env_prefix="REDIS_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    stream_max_len: int = 10_000   # Trim streams to last N messages


class DatabaseConfig(BaseSettings):
    """TimescaleDB / PostgreSQL connection config."""
    model_config = SettingsConfigDict(env_prefix="DB_", env_file=".env", extra="ignore")

    host: str = "localhost"
    port: int = 5432
    name: str = "nse_options"
    user: str = "trader"
    password: str = "trader_pass"

    @property
    def url(self) -> str:
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class RiskConfig(BaseSettings):
    """Risk management limits (overridable at runtime via config/risk.yaml)."""
    model_config = SettingsConfigDict(env_prefix="RISK_", env_file=".env", extra="ignore")

    # Capital
    total_capital: float = 500_000.0          # Rs. 5 Lakhs default
    max_capital_per_trade_pct: float = 0.05   # 5% of capital per trade
    max_open_trades: int = 5

    # Loss limits
    max_loss_per_trade: float = 5_000.0       # Rs. 5,000 hard stop per trade
    daily_loss_circuit_breaker_pct: float = 0.03  # Halt if daily loss > 3% of capital

    # Greeks limits
    max_net_delta: float = 50.0               # Net portfolio delta
    max_net_vega: float = 10_000.0            # Net portfolio vega

    # Margin
    min_margin_buffer_pct: float = 0.20       # Keep 20% margin free at all times

    # SL thresholds
    intraday_sl_pct: float = 0.30             # 30% of premium collected
    positional_sl_pct: float = 0.50           # 50% of premium for positionals


class StrategyConfig(BaseSettings):
    """Strategy-level settings."""
    model_config = SettingsConfigDict(env_prefix="STRATEGY_", env_file=".env", extra="ignore")

    enabled_underlyings: str = "NIFTY,BANKNIFTY"
    min_iv_rank_for_selling: float = 50.0     # Only sell options if IV rank >= 50
    min_days_to_expiry: int = 2               # Don't enter within 2 days of expiry (positional)
    max_days_to_expiry: int = 30
    re_evaluation_interval_seconds: int = 300  # Re-evaluate strategy every 5 min

    @property
    def underlyings(self) -> List[str]:
        return [u.strip() for u in self.enabled_underlyings.split(",")]


class NotificationsConfig(BaseSettings):
    """Telegram / notification settings."""
    model_config = SettingsConfigDict(env_prefix="TELEGRAM_", env_file=".env", extra="ignore")

    bot_token: str = ""
    chat_id: str = ""


class AppConfig(BaseSettings):
    """Top-level application configuration."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    trading_mode: TradingMode = TradingMode.PAPER
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    cors_origins: str = "http://localhost:5173"  # Vite dev server

    # Market hours (IST)
    market_open_time: str = "09:15"
    market_close_time: str = "15:30"
    intraday_squareoff_time: str = "15:20"    # Square off 10 min before close
    pre_market_warmup_time: str = "08:45"     # Start data warmup

    # Heartbeat
    agent_heartbeat_interval: int = 30        # seconds

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @field_validator("trading_mode", mode="before")
    @classmethod
    def validate_mode(cls, v):
        return TradingMode(v.upper()) if isinstance(v, str) else v


# ── Composed settings ────────────────────────────────────────────────────────

class Settings:
    """Composed settings — single import point for the whole application."""

    def __init__(self):
        self.app = AppConfig()
        self.dhan = DhanConfig()
        self.gemini = GeminiConfig()
        self.ollama = OllamaConfig()
        self.redis = RedisConfig()
        self.db = DatabaseConfig()
        self.risk = RiskConfig()
        self.strategy = StrategyConfig()
        self.notifications = NotificationsConfig()

    @property
    def trading_mode(self) -> str:
        """Convenience shortcut: settings.trading_mode"""
        return self.app.trading_mode.value.lower()

    @property
    def redis_url(self) -> str:
        """Full Redis URL for the event bus."""
        r = self.redis
        if r.password:
            return f"redis://:{r.password}@{r.host}:{r.port}/{r.db}"
        return f"redis://{r.host}:{r.port}/{r.db}"

    @property
    def is_live(self) -> bool:
        return self.app.trading_mode == TradingMode.LIVE

    @property
    def is_paper(self) -> bool:
        return self.app.trading_mode == TradingMode.PAPER


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached singleton Settings instance."""
    return Settings()


settings = get_settings()
