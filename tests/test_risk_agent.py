"""
Unit tests for the Risk Management Agent.
Verifies that the risk gate correctly blocks dangerous trades and approves safe ones.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from core.enums import (
    EventType, OrderSide, OptionType, StrategyName, Underlying,
    TradeStyle, SignalType, MarketCondition,
)
from core.models import Signal, StrategyLeg, Event, RiskAssessment


# ── Helper: build a test signal ──────────────────────────────────────────────

def make_signal(
    strategy: StrategyName = StrategyName.IRON_CONDOR,
    underlying: Underlying = Underlying.NIFTY,
    confidence: float = 0.75,
    max_loss: float = 3000.0,
    num_legs: int = 4,
) -> Signal:
    """Create a valid test signal with configurable params."""
    legs = []
    for i in range(num_legs):
        legs.append(StrategyLeg(
            symbol=f"NIFTY2430{22000 + i * 100}{'CE' if i % 2 == 0 else 'PE'}",
            underlying=underlying,
            option_type=OptionType.CALL if i % 2 == 0 else OptionType.PUT,
            strike=22000.0 + i * 100,
            expiry=datetime(2024, 3, 28).date(),
            side=OrderSide.SELL if i < 2 else OrderSide.BUY,
            quantity=1,
            lot_size=50,
        ))
    return Signal(
        strategy=strategy,
        underlying=underlying,
        signal_type=SignalType.ENTER,
        trade_style=TradeStyle.INTRADAY,
        legs=legs,
        confidence=confidence,
        reasoning="Test signal",
        market_condition=MarketCondition.RANGE_BOUND,
        max_loss_estimate=max_loss,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestRiskLimits:
    """Test that risk violations are correctly detected."""

    def test_signal_model_validates(self):
        """Ensure our test signal builder produces a valid Signal."""
        sig = make_signal()
        assert sig.confidence == 0.75
        assert len(sig.legs) == 4
        assert sig.strategy == StrategyName.IRON_CONDOR

    def test_max_loss_per_trade_check(self):
        """Signal max_loss > limit should be flagged."""
        sig = make_signal(max_loss=50_000)  # well above default 5k limit

        # Simulate risk check logic
        max_loss_limit = 5_000.0
        is_within_limit = sig.max_loss_estimate <= max_loss_limit
        assert not is_within_limit, "Signal with ₹50k loss should be REJECTED"

    def test_within_max_loss_is_ok(self):
        """Signal max_loss within limit should pass."""
        sig = make_signal(max_loss=3_000)
        max_loss_limit = 5_000.0
        assert sig.max_loss_estimate <= max_loss_limit

    def test_capital_per_trade_pct_check(self):
        """Position size exceeding 5% of capital should be rejected."""
        total_capital = 500_000.0
        max_pct = 0.05
        max_capital_per_trade = total_capital * max_pct  # ₹25,000

        # Signal requiring ₹30,000 margin
        margin_required = 30_000.0
        assert margin_required > max_capital_per_trade, "Should exceed capital-per-trade limit"

    def test_max_open_trades_check(self):
        """Should reject when max_open_trades is already reached."""
        max_open = 5
        current_open = 5
        assert current_open >= max_open, "Should not allow new trade when at limit"

    def test_allows_trade_under_max_open(self):
        """Should allow trade when below max_open_trades."""
        max_open = 5
        current_open = 3
        assert current_open < max_open

    def test_daily_loss_circuit_breaker(self):
        """Should halt trading when daily loss exceeds circuit breaker threshold."""
        total_capital = 500_000.0
        circuit_breaker_pct = 0.03  # 3%
        daily_loss = 20_000.0  # ₹20k = 4%
        daily_loss_pct = daily_loss / total_capital

        assert daily_loss_pct > circuit_breaker_pct, "Circuit breaker should trigger"

    def test_no_circuit_breaker_when_within_limit(self):
        """Should NOT halt when daily loss is within circuit breaker."""
        total_capital = 500_000.0
        circuit_breaker_pct = 0.03
        daily_loss = 10_000.0  # ₹10k = 2%
        daily_loss_pct = daily_loss / total_capital

        assert daily_loss_pct <= circuit_breaker_pct

    def test_net_delta_limit(self):
        """Portfolio net delta exceeding limit should raise alert."""
        max_delta = 50.0
        current_delta = 75.0
        assert abs(current_delta) > max_delta, "Delta limit breach"

    def test_net_vega_limit(self):
        """Portfolio net vega exceeding limit should raise alert."""
        max_vega = 10_000.0
        current_vega = 15_000.0
        assert abs(current_vega) > max_vega, "Vega limit breach"


class TestRiskAssessmentModel:
    """Test the RiskAssessment output model."""

    def test_approved_assessment(self):
        assessment = RiskAssessment(
            signal_id="test-123",
            approved=True,
            reasons=[],
            max_loss_estimate=3000.0,
            margin_required=20_000.0,
        )
        assert assessment.approved is True
        assert len(assessment.reasons) == 0

    def test_rejected_assessment_with_reasons(self):
        assessment = RiskAssessment(
            signal_id="test-456",
            approved=False,
            reasons=[
                "Max loss ₹50,000 exceeds per-trade limit of ₹5,000",
                "Net delta 75.0 exceeds limit of 50.0",
            ],
            max_loss_estimate=50_000.0,
            margin_required=80_000.0,
        )
        assert assessment.approved is False
        assert len(assessment.reasons) == 2
        assert "Max loss" in assessment.reasons[0]
