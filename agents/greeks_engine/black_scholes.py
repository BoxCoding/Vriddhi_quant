"""
Black-Scholes pricing and Greeks computation for NSE options.

Supports:
  - European option pricing (BSM)
  - All 5 Greeks: Delta, Gamma, Theta, Vega, Rho
  - Implied Volatility solving via Newton-Raphson + bisection fallback
  - IV Rank & IV Percentile from a rolling window of historical IVs
"""
from __future__ import annotations

import math
from typing import List, Optional

from scipy.stats import norm

from core.enums import OptionType
from core.exceptions import IVSolverError
from core.models import Greeks


# RBI Repo Rate (risk-free rate) — update quarterly
RISK_FREE_RATE: float = 0.065  # 6.5% as of 2024


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes d1 parameter."""
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def _d2(d1: float, sigma: float, T: float) -> float:
    """Black-Scholes d2 parameter."""
    return d1 - sigma * math.sqrt(T)


def bs_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType,
) -> float:
    """
    Black-Scholes European option price.

    Args:
        S: Spot price
        K: Strike price
        T: Time to expiry in years (e.g. 7/365)
        r: Risk-free rate (annualised, e.g. 0.065)
        sigma: Implied volatility (annualised, e.g. 0.18)
        option_type: CE or PE
    Returns:
        Option price
    """
    if T <= 0 or sigma <= 0:
        # At expiry — intrinsic value only
        if option_type == OptionType.CALL:
            return max(0.0, S - K)
        return max(0.0, K - S)

    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(d1, sigma, T)

    if option_type == OptionType.CALL:
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


def compute_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: OptionType,
    iv_rank: float = 0.0,
    iv_percentile: float = 0.0,
) -> Greeks:
    """
    Compute all option Greeks for a single contract.

    Args:
        S: Spot price
        K: Strike price
        T: Time to expiry in years
        r: Risk-free rate
        sigma: Implied volatility (annualised)
        option_type: CE or PE
        iv_rank: IV Rank 0-100
        iv_percentile: IV Percentile 0-100

    Returns:
        Greeks object with delta, gamma, theta, vega, rho, iv, iv_rank, iv_percentile
    """
    if T <= 1e-6 or sigma <= 1e-6:
        # At/past expiry
        delta = 1.0 if (option_type == OptionType.CALL and S > K) else 0.0
        if option_type == OptionType.PUT:
            delta = -1.0 if S < K else 0.0
        return Greeks(delta=delta, gamma=0, theta=0, vega=0, rho=0, iv=sigma, iv_rank=iv_rank, iv_percentile=iv_percentile)

    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(d1, sigma, T)
    nd1 = norm.pdf(d1)
    nd2_cdf = norm.cdf(d2)
    nd1_cdf = norm.cdf(d1)

    # Delta
    if option_type == OptionType.CALL:
        delta = nd1_cdf
    else:
        delta = nd1_cdf - 1.0

    # Gamma (same for CE and PE)
    gamma = nd1 / (S * sigma * math.sqrt(T))

    # Theta (per calendar day)
    base_theta = (
        -(S * nd1 * sigma) / (2 * math.sqrt(T))
        - r * K * math.exp(-r * T) * norm.cdf(d2 if option_type == OptionType.CALL else -d2)
    )
    if option_type == OptionType.PUT:
        base_theta = (
            -(S * nd1 * sigma) / (2 * math.sqrt(T))
            + r * K * math.exp(-r * T) * norm.cdf(-d2)
        )
    theta = base_theta / 365.0   # Convert annualised to per-day

    # Vega (per 1% move in IV)
    vega = S * nd1 * math.sqrt(T) * 0.01

    # Rho (per 1% move in interest rate)
    if option_type == OptionType.CALL:
        rho = K * T * math.exp(-r * T) * norm.cdf(d2) * 0.01
    else:
        rho = -K * T * math.exp(-r * T) * norm.cdf(-d2) * 0.01

    return Greeks(
        delta=round(delta, 6),
        gamma=round(gamma, 6),
        theta=round(theta, 4),
        vega=round(vega, 4),
        rho=round(rho, 4),
        iv=round(sigma, 6),
        iv_rank=round(iv_rank, 2),
        iv_percentile=round(iv_percentile, 2),
    )


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: OptionType,
    max_iterations: int = 100,
    tolerance: float = 1e-5,
) -> float:
    """
    Solve for Implied Volatility using Newton-Raphson with bisection fallback.

    Returns:
        Implied volatility (annualised, e.g. 0.18 = 18%)

    Raises:
        IVSolverError: If convergence fails.
    """
    # Sanity: price must be above intrinsic value
    if option_type == OptionType.CALL:
        intrinsic = max(0.0, S - K * math.exp(-r * T))
    else:
        intrinsic = max(0.0, K * math.exp(-r * T) - S)

    if market_price <= intrinsic:
        market_price = intrinsic + 0.01   # Nudge above intrinsic

    # ── Newton-Raphson ────────────────────────────────────────────────────────
    sigma = 0.20   # Initial guess: 20% IV
    for i in range(max_iterations):
        price = bs_price(S, K, T, r, sigma, option_type)
        diff = price - market_price

        if abs(diff) < tolerance:
            return sigma

        # vega = dPrice/dSigma
        d1 = _d1(S, K, T, r, sigma)
        vega_val = S * norm.pdf(d1) * math.sqrt(T)

        if abs(vega_val) < 1e-10:
            break   # Vega too small — fall through to bisection

        sigma -= diff / vega_val
        sigma = max(0.001, min(sigma, 10.0))   # Clamp to [0.1%, 1000%]

    # ── Bisection fallback ───────────────────────────────────────────────────
    low, high = 0.001, 10.0
    for _ in range(200):
        mid = (low + high) / 2.0
        price = bs_price(S, K, T, r, mid, option_type)
        if abs(price - market_price) < tolerance:
            return mid
        if price < market_price:
            low = mid
        else:
            high = mid

    raise IVSolverError(
        f"IV solver did not converge for S={S} K={K} T={T:.4f} price={market_price}"
    )


def compute_iv_rank(current_iv: float, historical_ivs: List[float]) -> float:
    """
    IV Rank = (Current IV - 52w Low) / (52w High - 52w Low) * 100

    Returns 0-100.
    """
    if len(historical_ivs) < 2:
        return 0.0
    low, high = min(historical_ivs), max(historical_ivs)
    if high == low:
        return 0.0
    return clamp((current_iv - low) / (high - low) * 100, 0.0, 100.0)


def compute_iv_percentile(current_iv: float, historical_ivs: List[float]) -> float:
    """
    IV Percentile = % of days in past year where IV was BELOW current IV.

    Returns 0-100.
    """
    if not historical_ivs:
        return 0.0
    days_below = sum(1 for iv in historical_ivs if iv < current_iv)
    return clamp(days_below / len(historical_ivs) * 100, 0.0, 100.0)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))
