"""
Backtesting engine — replays historical data through strategy + risk agents.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from backtesting.data_loader import DataLoader
from core.enums import OptionType, OrderSide, StrategyName, Underlying

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """Single completed trade in a backtest."""
    trade_id: int
    strategy: str
    underlying: str
    entry_time: datetime
    exit_time: Optional[datetime] = None
    entry_price: float = 0.0
    exit_price: float = 0.0
    quantity: int = 0
    side: str = "BUY"
    pnl: float = 0.0
    is_open: bool = True


@dataclass
class BacktestResult:
    """Aggregated results from a backtest run."""
    start_date: date
    end_date: date
    underlying: str
    strategy: str
    initial_capital: float
    final_capital: float
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)


class BacktestEngine:
    """
    Core backtesting engine.

    Replays historical OHLCV data bar-by-bar through a strategy callback function,
    simulating entries, exits, and P&L.

    Usage:
        engine = BacktestEngine(capital=500_000)
        result = engine.run(
            underlying=Underlying.NIFTY,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
            strategy_fn=my_strategy_logic,
        )
        print(result.total_pnl, result.win_rate)
    """

    def __init__(
        self,
        capital: float = 500_000.0,
        max_risk_per_trade_pct: float = 0.02,
        commission_per_order: float = 20.0,
        slippage_bps: float = 5.0,
        slippage_model: str = "fixed",
        data_dir: str = "data/historical",
    ) -> None:
        self._initial_capital = capital
        self._capital = capital
        self._max_risk_pct = max_risk_per_trade_pct
        self._commission = commission_per_order
        self._slippage_bps = slippage_bps
        self._slippage_model = slippage_model
        self._loader = DataLoader(data_dir=data_dir)
        self._trades: List[BacktestTrade] = []
        self._equity_curve: List[float] = [capital]
        self._trade_counter = 0

    def run(
        self,
        underlying: Underlying,
        start_date: date,
        end_date: date,
        strategy_fn: Any,
        timeframe: str = "5m",
        use_synthetic: bool = True,
        simulate_ticks: bool = False,
    ) -> BacktestResult:
        """
        Run a backtest.

        Args:
            underlying: Which index to trade (NIFTY / BANKNIFTY)
            start_date: Backtest start date
            end_date: Backtest end date
            strategy_fn: Callable(row, state) -> {"action": "BUY"|"SELL"|"HOLD", ...}
            timeframe: Candle timeframe
            use_synthetic: Generate synthetic data if no CSV available
        """
        logger.info(
            "Starting backtest: %s %s → %s (capital=₹%.0f)",
            underlying.value, start_date, end_date, self._capital,
        )

        # Load data
        if use_synthetic:
            df = self._loader.generate_synthetic_ohlcv(
                underlying, start_date, num_days=(end_date - start_date).days
            )
        else:
            df = self._loader.load_ohlcv(underlying, start_date, end_date, timeframe)

        if df.empty:
            logger.warning("No data loaded — aborting backtest")
            return self._build_result(underlying.value, "N/A", start_date, end_date)

        # State dict shared between strategy calls
        state: Dict[str, Any] = {
            "position": None,
            "capital": self._capital,
            "trades": [],
        }

        # Bar-by-bar replay
        for idx, row in df.iterrows():
            if simulate_ticks:
                # Stub: Create 4 synthetic ticks from OHLC and process them sequentially
                ticks = [
                    {"timestamp": row["timestamp"], "price": row["open"]},
                    {"timestamp": row["timestamp"], "price": row["high"]},
                    {"timestamp": row["timestamp"], "price": row["low"]},
                    {"timestamp": row["timestamp"], "price": row["close"]},
                ]
                # In full implementation, pass ticks to tick-level logic
            
            signal = strategy_fn(row, state)
            if signal is None:
                continue

            action = signal.get("action", "HOLD")
            if action == "BUY" and state["position"] is None:
                self._open_trade(row, state, "BUY", signal)
            elif action == "SELL" and state["position"] is None:
                self._open_trade(row, state, "SELL", signal)
            elif action == "CLOSE" and state["position"] is not None:
                self._close_trade(row, state)

            self._equity_curve.append(self._capital)

        # Close any remaining position at last bar
        if state["position"] is not None and not df.empty:
            last_row = df.iloc[-1]
            self._close_trade(last_row, state)

        strategy_name = getattr(strategy_fn, "__name__", "custom")
        return self._build_result(underlying.value, strategy_name, start_date, end_date)

    # ── Trade management ──────────────────────────────────────────────────────

    def _get_dynamic_slippage(self, row: Any) -> float:
        if self._slippage_model == "dynamic" and "high" in row and "low" in row:
            spread_factor = (row["high"] - row["low"]) / row["close"]
            return max(self._slippage_bps, self._slippage_bps * spread_factor * 100)
        return self._slippage_bps

    def _open_trade(self, row: Any, state: Dict, side: str, signal: Dict) -> None:
        self._trade_counter += 1
        qty = signal.get("quantity", 50)  # Default 1 lot NIFTY
        effective_slippage = self._get_dynamic_slippage(row)
        entry_price = row["close"] * (1 + effective_slippage / 10000)
        cost = self._commission

        trade = BacktestTrade(
            trade_id=self._trade_counter,
            strategy=signal.get("strategy", "custom"),
            underlying=signal.get("underlying", "NIFTY"),
            entry_time=row["timestamp"],
            entry_price=entry_price,
            quantity=qty,
            side=side,
        )
        state["position"] = trade
        self._capital -= cost

    def _close_trade(self, row: Any, state: Dict) -> None:
        trade: BacktestTrade = state["position"]
        effective_slippage = self._get_dynamic_slippage(row)
        exit_price = row["close"] * (1 - effective_slippage / 10000)
        trade.exit_price = exit_price
        trade.exit_time = row["timestamp"]
        trade.is_open = False

        if trade.side == "BUY":
            trade.pnl = (exit_price - trade.entry_price) * trade.quantity
        else:
            trade.pnl = (trade.entry_price - exit_price) * trade.quantity

        trade.pnl -= self._commission  # Exit commission
        self._capital += trade.pnl
        self._trades.append(trade)
        state["position"] = None

    # ── Result builder ─────────────────────────────────────────────────────────

    def _build_result(self, underlying: str, strategy: str, start: date, end: date) -> BacktestResult:
        import math

        pnls = [t.pnl for t in self._trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        n = len(pnls)

        # Max drawdown
        peak = self._initial_capital
        max_dd = 0.0
        for eq in self._equity_curve:
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd

        # Sharpe ratio
        if n > 1:
            mean = sum(pnls) / n
            std = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (n - 1))
            sharpe = (mean / std * math.sqrt(252)) if std > 0 else 0.0
        else:
            sharpe = 0.0

        return BacktestResult(
            start_date=start,
            end_date=end,
            underlying=underlying,
            strategy=strategy,
            initial_capital=self._initial_capital,
            final_capital=round(self._capital, 2),
            total_trades=n,
            winning_trades=len(wins),
            losing_trades=len(losses),
            total_pnl=round(sum(pnls), 2),
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd / self._initial_capital * 100, 2) if self._initial_capital > 0 else 0,
            sharpe_ratio=round(sharpe, 3),
            win_rate=round(len(wins) / n * 100, 2) if n > 0 else 0.0,
            profit_factor=round(abs(sum(wins) / sum(losses)), 2) if sum(losses) != 0 else float("inf"),
            avg_win=round(sum(wins) / len(wins), 2) if wins else 0.0,
            avg_loss=round(sum(losses) / len(losses), 2) if losses else 0.0,
            trades=self._trades,
            equity_curve=self._equity_curve,
        )
