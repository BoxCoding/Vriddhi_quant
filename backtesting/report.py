"""
Backtesting report generator — produces backtest summaries and equity charts.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from backtesting.engine import BacktestResult

logger = logging.getLogger(__name__)


def format_report(result: BacktestResult) -> str:
    """Format a BacktestResult as a human-readable text report."""
    lines = [
        "═" * 60,
        f"  BACKTEST REPORT — {result.strategy}",
        "═" * 60,
        f"  Underlying      : {result.underlying}",
        f"  Period           : {result.start_date} → {result.end_date}",
        f"  Initial Capital  : ₹{result.initial_capital:,.2f}",
        f"  Final Capital    : ₹{result.final_capital:,.2f}",
        "",
        "── Performance ────────────────────────────────────",
        f"  Total P&L        : ₹{result.total_pnl:,.2f}",
        f"  Return           : {result.total_pnl / result.initial_capital * 100:.2f}%",
        f"  Sharpe Ratio     : {result.sharpe_ratio:.3f}",
        f"  Max Drawdown     : ₹{result.max_drawdown:,.2f} ({result.max_drawdown_pct:.2f}%)",
        f"  Profit Factor    : {result.profit_factor:.2f}",
        "",
        "── Trade Statistics ────────────────────────────────",
        f"  Total Trades     : {result.total_trades}",
        f"  Win Rate         : {result.win_rate:.1f}%",
        f"  Winning Trades   : {result.winning_trades}",
        f"  Losing Trades    : {result.losing_trades}",
        f"  Avg Win          : ₹{result.avg_win:,.2f}",
        f"  Avg Loss         : ₹{result.avg_loss:,.2f}",
        "",
    ]

    if result.trades:
        lines.append("── Top 5 Trades ───────────────────────────────")
        sorted_trades = sorted(result.trades, key=lambda t: t.pnl, reverse=True)
        for t in sorted_trades[:5]:
            emoji = "✅" if t.pnl > 0 else "🔴"
            lines.append(
                f"  {emoji} #{t.trade_id}  {t.side}  "
                f"entry={t.entry_price:.2f}  exit={t.exit_price:.2f}  "
                f"P&L=₹{t.pnl:,.2f}"
            )

    lines.append("═" * 60)
    return "\n".join(lines)


def save_report(result: BacktestResult, output_dir: str = "reports") -> str:
    """Save a backtest report to a text file and return the path."""
    from pathlib import Path

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{output_dir}/backtest_{result.strategy}_{result.underlying}_{timestamp}.txt"

    report_text = format_report(result)
    with open(filename, "w") as f:
        f.write(report_text)

    logger.info("Backtest report saved to %s", filename)
    return filename


def save_equity_curve_csv(result: BacktestResult, output_dir: str = "reports") -> str:
    """Save the equity curve to a CSV file."""
    from pathlib import Path
    import csv

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{output_dir}/equity_{result.strategy}_{result.underlying}_{timestamp}.csv"

    with open(filename, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["bar_index", "equity"])
        for i, eq in enumerate(result.equity_curve):
            writer.writerow([i, round(eq, 2)])

    logger.info("Equity curve saved to %s", filename)
    return filename
