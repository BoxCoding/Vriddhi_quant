"""
Unit tests for the Black-Scholes Greeks engine.
Validates against known analytical solutions.
"""
import math
import pytest
from agents.greeks_engine.black_scholes import (
    bs_price,
    compute_greeks,
    implied_volatility,
    compute_iv_rank,
    compute_iv_percentile,
)
from core.enums import OptionType
from core.exceptions import IVSolverError


# ── Black-Scholes price ───────────────────────────────────────────────────────

class TestBSPrice:
    """Verify BS prices against textbook examples."""

    def test_call_price_atm(self):
        """ATM call with standard parameters should be ≈ 8-9% of spot."""
        price = bs_price(S=22000, K=22000, T=30/365, r=0.065, sigma=0.18, option_type=OptionType.CALL)
        assert 1500 < price < 2500, f"Unexpected ATM call price: {price:.2f}"

    def test_put_price_atm(self):
        """ATM put should be close to ATM call (put-call parity)."""
        call = bs_price(S=22000, K=22000, T=30/365, r=0.065, sigma=0.18, option_type=OptionType.CALL)
        put  = bs_price(S=22000, K=22000, T=30/365, r=0.065, sigma=0.18, option_type=OptionType.PUT)
        # Put-call parity: C - P = S - K*exp(-rT)
        S, K, T, r = 22000, 22000, 30/365, 0.065
        parity = S - K * math.exp(-r * T)
        assert abs((call - put) - parity) < 1.0, "Put-call parity violated"

    def test_deep_itm_call(self):
        """Deep ITM call ≈ intrinsic value (S - K)."""
        price = bs_price(S=22000, K=20000, T=1/365, r=0.065, sigma=0.18, option_type=OptionType.CALL)
        assert abs(price - 2000) < 20, f"Deep ITM call mismatch: {price:.2f}"

    def test_deep_otm_call_near_zero(self):
        """Deep OTM short-dated call should be nearly zero."""
        price = bs_price(S=22000, K=25000, T=1/365, r=0.065, sigma=0.18, option_type=OptionType.CALL)
        assert price < 1.0, f"Deep OTM call should be near 0, got {price:.4f}"

    def test_zero_time_intrinsic(self):
        """At T=0, should return intrinsic value only."""
        call = bs_price(S=22000, K=21000, T=0, r=0.065, sigma=0.18, option_type=OptionType.CALL)
        assert call == 1000.0

        put = bs_price(S=21000, K=22000, T=0, r=0.065, sigma=0.18, option_type=OptionType.PUT)
        assert put == 1000.0

    def test_non_negative_prices(self):
        """Prices must never be negative."""
        for K in range(19000, 25001, 500):
            for otype in [OptionType.CALL, OptionType.PUT]:
                price = bs_price(S=22000, K=K, T=7/365, r=0.065, sigma=0.16, option_type=otype)
                assert price >= 0, f"Negative price: {price:.4f} for K={K} {otype}"


# ── Greeks ────────────────────────────────────────────────────────────────────

class TestGreeks:
    def test_call_delta_range(self):
        """Call delta must be in [0, 1]."""
        g = compute_greeks(S=22000, K=22000, T=7/365, r=0.065, sigma=0.16, option_type=OptionType.CALL)
        assert 0.0 <= g.delta <= 1.0, f"Call delta out of range: {g.delta}"

    def test_put_delta_range(self):
        """Put delta must be in [-1, 0]."""
        g = compute_greeks(S=22000, K=22000, T=7/365, r=0.065, sigma=0.16, option_type=OptionType.PUT)
        assert -1.0 <= g.delta <= 0.0, f"Put delta out of range: {g.delta}"

    def test_call_delta_plus_abs_put_delta_equals_one(self):
        """For same strike: |delta_call| + |delta_put| ≈ 1 (approximately)."""
        call_g = compute_greeks(S=22000, K=22000, T=7/365, r=0.065, sigma=0.16, option_type=OptionType.CALL)
        put_g  = compute_greeks(S=22000, K=22000, T=7/365, r=0.065, sigma=0.16, option_type=OptionType.PUT)
        assert abs(call_g.delta + put_g.delta - 1.0) < 0.01, "Delta symmetry broken"

    def test_gamma_positive(self):
        """Gamma is always positive for both calls and puts."""
        for otype in [OptionType.CALL, OptionType.PUT]:
            g = compute_greeks(S=22000, K=22000, T=7/365, r=0.065, sigma=0.18, option_type=otype)
            assert g.gamma >= 0, f"Gamma negative: {g.gamma}"

    def test_theta_negative_long_option(self):
        """Theta (time decay) should be negative for long positions."""
        for otype in [OptionType.CALL, OptionType.PUT]:
            g = compute_greeks(S=22000, K=22000, T=14/365, r=0.065, sigma=0.18, option_type=otype)
            assert g.theta < 0, f"Theta should be negative, got {g.theta}"

    def test_vega_positive(self):
        """Vega is always positive (options gain value when IV rises)."""
        for otype in [OptionType.CALL, OptionType.PUT]:
            g = compute_greeks(S=22000, K=22000, T=14/365, r=0.065, sigma=0.18, option_type=otype)
            assert g.vega >= 0, f"Vega negative: {g.vega}"

    def test_atm_delta_approx_half(self):
        """ATM call delta should be ≈ 0.5 for short-dated options."""
        g = compute_greeks(S=22000, K=22000, T=7/365, r=0.065, sigma=0.15, option_type=OptionType.CALL)
        assert 0.45 <= g.delta <= 0.60, f"ATM delta deviates: {g.delta}"


# ── Implied Volatility solver ─────────────────────────────────────────────────

class TestIVSolver:
    def test_round_trip(self):
        """Solve IV from a BS price and compare back."""
        S, K, T, r, sigma = 22000, 22000, 14/365, 0.065, 0.18
        market_price = bs_price(S, K, T, r, sigma, OptionType.CALL)
        solved_iv = implied_volatility(market_price, S, K, T, r, OptionType.CALL)
        assert abs(solved_iv - sigma) < 1e-4, f"IV round-trip failed: {solved_iv:.6f} vs {sigma:.6f}"

    def test_iv_for_put(self):
        """IV solver should work for puts too."""
        S, K, T, r, sigma = 22000, 22500, 14/365, 0.065, 0.20
        market_price = bs_price(S, K, T, r, sigma, OptionType.PUT)
        solved_iv = implied_volatility(market_price, S, K, T, r, OptionType.PUT)
        assert abs(solved_iv - sigma) < 1e-4

    def test_different_strikes(self):
        """IV solver must converge for a range of strikes."""
        S, T, r, sigma = 22000, 7/365, 0.065, 0.16
        for K in [20000, 21000, 22000, 23000, 24000]:
            for otype in [OptionType.CALL, OptionType.PUT]:
                price = bs_price(S, K, T, r, sigma, otype)
                if price < 0.05:
                    continue  # Skip near-zero prices
                iv = implied_volatility(price, S, K, T, r, otype)
                assert abs(iv - sigma) < 0.005, f"IV error at K={K}: {iv:.4f} vs {sigma:.4f}"


# ── IV Rank / Percentile ──────────────────────────────────────────────────────

class TestIVRankPercentile:
    def test_iv_rank_max(self):
        """Current IV at historical high → rank = 100."""
        hist = [0.10, 0.12, 0.14, 0.16, 0.18]
        assert compute_iv_rank(0.18, hist) == 100.0

    def test_iv_rank_min(self):
        """Current IV at historical low → rank = 0."""
        hist = [0.10, 0.12, 0.14, 0.16, 0.18]
        assert compute_iv_rank(0.10, hist) == 0.0

    def test_iv_rank_mid(self):
        hist = [0.10, 0.20]
        assert compute_iv_rank(0.15, hist) == 50.0

    def test_iv_percentile(self):
        """IV above 80% of history → percentile ≈ 80."""
        hist = [0.10] * 8 + [0.20, 0.25]
        pct = compute_iv_percentile(0.19, hist)
        assert abs(pct - 80.0) < 1.0
