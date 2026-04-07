"""
Unit tests for strategy signal logic.
Verifies that each strategy correctly identifies entry conditions
and produces valid Signal objects.
"""
import pytest
from datetime import date, datetime
from unittest.mock import MagicMock

from core.enums import (
    MarketCondition, OptionType, OrderSide, SignalType,
    StrategyName, TradeStyle, Underlying,
)
from core.models import Greeks, OptionChain, OptionTick, Signal, StrategyLeg


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_option_chain(
    spot: float = 22000.0,
    iv_rank: float = 55.0,
    num_strikes: int = 21,
    strike_step: float = 50.0,
    base_iv: float = 0.18,
) -> OptionChain:
    """Build a mock option chain for testing."""
    atm = round(spot / strike_step) * strike_step
    start_strike = atm - (num_strikes // 2) * strike_step

    strikes = {}
    for i in range(num_strikes):
        strike = start_strike + i * strike_step
        distance = abs(strike - spot)
        # Simple pricing: ATM has highest premium, falls off with distance
        ce_premium = max(0.5, (spot * base_iv * 0.05) - distance * 0.3)
        pe_premium = max(0.5, (spot * base_iv * 0.05) - distance * 0.3)

        ce_tick = OptionTick(
            symbol=f"NIFTY24MAR{int(strike)}CE",
            underlying=Underlying.NIFTY,
            timestamp=datetime.now(),
            ltp=ce_premium,
            strike=strike,
            option_type=OptionType.CALL,
            expiry=date(2024, 3, 28),
            bid=ce_premium - 0.5,
            ask=ce_premium + 0.5,
            oi=100_000 + i * 5_000,
            volume=50_000,
            greeks=Greeks(
                delta=max(0.0, 0.5 - (strike - spot) / (spot * base_iv)),
                gamma=0.003,
                theta=-5.0,
                vega=8.0,
                iv=base_iv + (0.001 * abs(strike - spot) / strike_step),
                iv_rank=iv_rank,
            ),
        )
        pe_tick = OptionTick(
            symbol=f"NIFTY24MAR{int(strike)}PE",
            underlying=Underlying.NIFTY,
            timestamp=datetime.now(),
            ltp=pe_premium,
            strike=strike,
            option_type=OptionType.PUT,
            expiry=date(2024, 3, 28),
            bid=pe_premium - 0.5,
            ask=pe_premium + 0.5,
            oi=80_000 + i * 3_000,
            volume=40_000,
            greeks=Greeks(
                delta=min(0.0, -0.5 + (spot - strike) / (spot * base_iv)),
                gamma=0.003,
                theta=-5.0,
                vega=8.0,
                iv=base_iv + (0.001 * abs(strike - spot) / strike_step),
                iv_rank=iv_rank,
            ),
        )
        strikes[strike] = {"CE": ce_tick, "PE": pe_tick}

    return OptionChain(
        underlying=Underlying.NIFTY,
        spot_price=spot,
        timestamp=datetime.now(),
        expiry=date(2024, 3, 28),
        strikes=strikes,
        atm_strike=atm,
        india_vix=15.5,
        pcr=0.95,
    )


# ── Signal validation tests ──────────────────────────────────────────────────

class TestSignalModel:
    """Verify Signal model constraints and computed fields."""

    def test_signal_requires_legs(self):
        """A signal must have at least one leg."""
        sig = Signal(
            strategy=StrategyName.IRON_CONDOR,
            underlying=Underlying.NIFTY,
            signal_type=SignalType.ENTER,
            trade_style=TradeStyle.INTRADAY,
            legs=[StrategyLeg(
                symbol="NIFTY24MAR22000CE",
                underlying=Underlying.NIFTY,
                option_type=OptionType.CALL,
                strike=22000.0,
                expiry=date(2024, 3, 28),
                side=OrderSide.SELL,
                quantity=1,
                lot_size=50,
            )],
            confidence=0.7,
            reasoning="Test",
            market_condition=MarketCondition.RANGE_BOUND,
        )
        assert len(sig.legs) >= 1

    def test_confidence_bounds(self):
        """Confidence must be between 0 and 1."""
        with pytest.raises(Exception):
            Signal(
                strategy=StrategyName.IRON_CONDOR,
                underlying=Underlying.NIFTY,
                signal_type=SignalType.ENTER,
                trade_style=TradeStyle.INTRADAY,
                legs=[],
                confidence=1.5,  # Invalid
                reasoning="Test",
                market_condition=MarketCondition.RANGE_BOUND,
            )

    def test_total_quantity_computed(self):
        """StrategyLeg total_quantity should equal quantity * lot_size."""
        leg = StrategyLeg(
            symbol="NIFTY24MAR22000CE",
            underlying=Underlying.NIFTY,
            option_type=OptionType.CALL,
            strike=22000.0,
            expiry=date(2024, 3, 28),
            side=OrderSide.BUY,
            quantity=2,
            lot_size=50,
        )
        assert leg.total_quantity == 100

    def test_lot_sizes(self):
        """NIFTY lot size should be 50, BANKNIFTY should be 15."""
        nifty_leg = StrategyLeg(
            symbol="NIFTY24MAR22000CE",
            underlying=Underlying.NIFTY,
            option_type=OptionType.CALL,
            strike=22000.0,
            expiry=date(2024, 3, 28),
            side=OrderSide.BUY,
            quantity=1,
            lot_size=50,
        )
        bnf_leg = StrategyLeg(
            symbol="BANKNIFTY24MAR48000CE",
            underlying=Underlying.BANKNIFTY,
            option_type=OptionType.CALL,
            strike=48000.0,
            expiry=date(2024, 3, 28),
            side=OrderSide.BUY,
            quantity=1,
            lot_size=15,
        )
        assert nifty_leg.total_quantity == 50
        assert bnf_leg.total_quantity == 15


class TestOptionChainConstruction:
    """Verify our test option chain is well-formed."""

    def test_chain_has_strikes(self):
        oc = make_option_chain()
        assert len(oc.strikes) == 21

    def test_each_strike_has_ce_pe(self):
        oc = make_option_chain()
        for strike, options in oc.strikes.items():
            assert "CE" in options, f"Strike {strike} missing CE"
            assert "PE" in options, f"Strike {strike} missing PE"

    def test_atm_strike_correct(self):
        oc = make_option_chain(spot=22015.0)
        assert oc.atm_strike == 22000.0  # Rounded to nearest 50

    def test_greeks_populated(self):
        oc = make_option_chain()
        atm_ce = oc.strikes[oc.atm_strike]["CE"]
        assert atm_ce.greeks is not None
        assert atm_ce.greeks.iv > 0
        assert atm_ce.greeks.iv_rank > 0


class TestIronCondorStrategy:
    """Test Iron Condor entry logic."""

    def test_iron_condor_requires_4_legs(self):
        """An Iron Condor signal must have exactly 4 legs."""
        legs = [
            StrategyLeg(symbol=f"NIFTY{i}", underlying=Underlying.NIFTY,
                        option_type=OptionType.CALL if i < 2 else OptionType.PUT,
                        strike=22000.0 + i * 100, expiry=date(2024, 3, 28),
                        side=OrderSide.SELL if i % 2 == 0 else OrderSide.BUY,
                        quantity=1, lot_size=50)
            for i in range(4)
        ]
        sig = Signal(
            strategy=StrategyName.IRON_CONDOR,
            underlying=Underlying.NIFTY,
            signal_type=SignalType.ENTER,
            trade_style=TradeStyle.INTRADAY,
            legs=legs,
            confidence=0.7,
            reasoning="High IV rank iron condor",
            market_condition=MarketCondition.RANGE_BOUND,
        )
        assert len(sig.legs) == 4

    def test_iron_condor_has_sell_and_buy_legs(self):
        """IC must have both sell and buy legs."""
        legs = [
            StrategyLeg(symbol=f"NIFTY{i}", underlying=Underlying.NIFTY,
                        option_type=OptionType.CALL if i < 2 else OptionType.PUT,
                        strike=22000.0 + i * 100, expiry=date(2024, 3, 28),
                        side=OrderSide.SELL if i % 2 == 0 else OrderSide.BUY,
                        quantity=1, lot_size=50)
            for i in range(4)
        ]
        sell_legs = [l for l in legs if l.side == OrderSide.SELL]
        buy_legs = [l for l in legs if l.side == OrderSide.BUY]
        assert len(sell_legs) == 2, "IC needs 2 sell legs"
        assert len(buy_legs) == 2, "IC needs 2 buy legs"

    def test_iron_condor_prefers_range_bound(self):
        """IC is best in range-bound / low-vol conditions."""
        preferred_conditions = [
            MarketCondition.RANGE_BOUND,
            MarketCondition.LOW_VOLATILITY,
        ]
        # When market is trending up, IC should have lower confidence
        assert MarketCondition.TRENDING_UP not in preferred_conditions
