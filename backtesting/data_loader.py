"""
Backtesting data loader — replays historical option chain data for strategy validation.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from core.enums import OptionType, Underlying
from core.models import OHLCV, OptionChain, OptionTick

logger = logging.getLogger(__name__)


class DataLoader:
    """
    Load historical data for backtesting.

    Supports two sources:
      1. CSV files (offline, fastest)
      2. Dhan historical API (requires credentials)
    """

    def __init__(self, data_dir: str = "data/historical") -> None:
        self._data_dir = Path(data_dir)

    # ── OHLCV candle data ─────────────────────────────────────────────────────

    def load_ohlcv(
        self,
        underlying: Underlying,
        start_date: date,
        end_date: date,
        timeframe: str = "5m",
    ) -> pd.DataFrame:
        """
        Load OHLCV candle data for a given underlying and date range.

        Returns a Pandas DataFrame with columns:
            timestamp, open, high, low, close, volume, oi
        """
        csv_path = self._data_dir / f"{underlying.value}_{timeframe}.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path, parse_dates=["timestamp"])
            mask = (df["timestamp"].dt.date >= start_date) & (df["timestamp"].dt.date <= end_date)
            return df.loc[mask].reset_index(drop=True)

        logger.warning("No CSV found at %s — returning empty DataFrame", csv_path)
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])

    # ── Option chain snapshots ────────────────────────────────────────────────

    def load_option_chain_snapshots(
        self,
        underlying: Underlying,
        trade_date: date,
    ) -> List[Dict]:
        """
        Load intraday option chain snapshots for a single trading day.

        Each snapshot is a dict with keys:
            timestamp, spot_price, expiry, strikes: {strike -> {CE: {...}, PE: {...}}}

        Returns a list of snapshots ordered chronologically (every 1–5 minutes).
        """
        csv_path = self._data_dir / f"{underlying.value}_oc_{trade_date.isoformat()}.csv"
        if csv_path.exists():
            df = pd.read_csv(csv_path, parse_dates=["timestamp"])
            snapshots = []
            for ts, group in df.groupby("timestamp"):
                strikes = {}
                for _, row in group.iterrows():
                    strike = float(row["strike"])
                    if strike not in strikes:
                        strikes[strike] = {}
                    strikes[strike][row["option_type"]] = {
                        "ltp": row["ltp"],
                        "oi": int(row.get("oi", 0)),
                        "volume": int(row.get("volume", 0)),
                        "iv": float(row.get("iv", 0.0)),
                        "bid": float(row.get("bid", 0.0)),
                        "ask": float(row.get("ask", 0.0)),
                    }
                snapshots.append({
                    "timestamp": ts,
                    "spot_price": float(group["spot_price"].iloc[0]),
                    "expiry": str(group["expiry"].iloc[0]),
                    "strikes": strikes,
                })
            return snapshots

        logger.warning("No option chain CSV at %s", csv_path)
        return []

    # ── Synthetic data generator for testing ──────────────────────────────────

    @staticmethod
    def generate_synthetic_ohlcv(
        underlying: Underlying,
        start_date: date,
        num_days: int = 20,
        timeframe_minutes: int = 5,
        base_price: float = 22000.0,
    ) -> pd.DataFrame:
        """
        Generate synthetic OHLCV data for backtesting without real data.
        Uses a random walk model with realistic intraday patterns.
        """
        import numpy as np
        rng = np.random.default_rng(seed=42)

        records = []
        price = base_price
        for day_offset in range(num_days):
            current_date = start_date + timedelta(days=day_offset)
            if current_date.weekday() >= 5:
                continue  # Skip weekends

            # Market hours: 09:15 to 15:30
            minutes_in_session = 375  # 6h 15m
            num_candles = minutes_in_session // timeframe_minutes

            for i in range(num_candles):
                ts = datetime.combine(current_date, datetime.min.time()) + timedelta(
                    hours=9, minutes=15 + i * timeframe_minutes
                )
                # Random walk with mean reversion
                ret = rng.normal(0, 0.001) - 0.0001 * (price - base_price) / base_price
                price *= (1 + ret)
                high = price * (1 + abs(rng.normal(0, 0.0005)))
                low = price * (1 - abs(rng.normal(0, 0.0005)))
                open_price = price * (1 + rng.normal(0, 0.0003))
                volume = int(rng.integers(50_000, 500_000))
                records.append({
                    "timestamp": ts,
                    "open": round(open_price, 2),
                    "high": round(high, 2),
                    "low": round(low, 2),
                    "close": round(price, 2),
                    "volume": volume,
                    "oi": 0,
                })

        return pd.DataFrame(records)
