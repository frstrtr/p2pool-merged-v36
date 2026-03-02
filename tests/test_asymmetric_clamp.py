#!/usr/bin/env python
"""Tests for Phase 1a: Asymmetric Difficulty Clamp (§7.1.1).

Verifies the asymmetric share difficulty retarget clamp in
data.py generate_transaction() — faster downward adjustment
when hashrate drops suddenly, normal ±10% otherwise.

Run: pypy test_asymmetric_clamp.py
     python test_asymmetric_clamp.py
"""
from __future__ import division
import os
import sys
import unittest

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from p2pool.util.math import clip


def asymmetric_clamp(pre_target, prev_max_target):
    """Replicate the asymmetric clamp logic from data.py generate_transaction().

    This is the exact logic from the Phase 1a implementation:
    - Normal (target wants to stay within ±50% of previous): ±10% clamp
    - Extreme drop (target wants to rise >150% of previous): allow up to 167%
      (= ~40% difficulty drop per share)
    """
    clamp_lo = prev_max_target * 9 // 10
    if pre_target > prev_max_target * 3 // 2:
        # Extreme ratio (>1.5×): allow target up to 167% = ~40% diff drop
        clamp_hi = prev_max_target * 5 // 3
    else:
        # Normal adjustment: ±10%
        clamp_hi = prev_max_target * 11 // 10
    return clip(pre_target, (clamp_lo, clamp_hi))


class TestAsymmetricClampNormalOperation(unittest.TestCase):
    """Test normal operation where symmetric ±10% clamp applies."""

    def test_no_change(self):
        """pre_target == prev → no adjustment needed."""
        prev = 1000000
        result = asymmetric_clamp(prev, prev)
        self.assertEqual(result, prev)

    def test_small_increase_target(self):
        """pre_target slightly above prev → clamped to +10%."""
        prev = 1000000
        pre_target = prev + 50000  # +5%
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, pre_target)  # Within ±10%, no clamp

    def test_small_decrease_target(self):
        """pre_target slightly below prev → clamped to -10%."""
        prev = 1000000
        pre_target = prev - 50000  # -5%
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, pre_target)  # Within ±10%, no clamp

    def test_target_rise_capped_at_10pct_normal(self):
        """pre_target wants +30% (< 150% threshold) → clamped to +10%."""
        prev = 1000000
        pre_target = prev * 13 // 10  # +30% rise (hashrate dropped ~23%)
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, prev * 11 // 10)  # Clamped to +10%

    def test_target_drop_capped_at_10pct(self):
        """pre_target wants -30% → always clamped to -10%."""
        prev = 1000000
        pre_target = prev * 7 // 10  # -30% (hashrate rose a lot)
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, prev * 9 // 10)  # Clamped to -10%

    def test_exactly_at_upper_bound_normal(self):
        """pre_target exactly at 110% → equals bound."""
        prev = 1000000
        pre_target = prev * 11 // 10
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, pre_target)

    def test_exactly_at_lower_bound(self):
        """pre_target exactly at 90% → equals bound."""
        prev = 1000000
        pre_target = prev * 9 // 10
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, pre_target)


class TestAsymmetricClampExtremeDrops(unittest.TestCase):
    """Test extreme hashrate drops where asymmetric clamp activates."""

    def test_threshold_boundary_below(self):
        """pre_target at exactly 150% — NOT extreme (uses normal +10%)."""
        prev = 1000000
        pre_target = prev * 3 // 2  # Exactly 150%
        result = asymmetric_clamp(pre_target, prev)
        # ratio == 150%, NOT > 150%, so normal clamp applies
        self.assertEqual(result, prev * 11 // 10)

    def test_threshold_boundary_above(self):
        """pre_target at 151% — extreme mode activates, target passes through."""
        prev = 1000000
        pre_target = prev * 3 // 2 + 1  # Just over 150%
        result = asymmetric_clamp(pre_target, prev)
        # Extreme mode: clamp_hi = 5//3 = 1666666
        # pre_target = 1500001 < 1666666, so it passes through unclamped
        self.assertEqual(result, pre_target)

    def test_semiwhale_3x_departure(self):
        """Semiwhale (3×) leaves → target wants to triple, clamped to 167%."""
        prev = 1000000
        pre_target = prev * 3  # 3× (hashrate dropped to 1/3)
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, prev * 5 // 3)  # Clamped to 167%

    def test_whale_10x_departure(self):
        """Whale (10×) leaves → target wants 10×, clamped to 167%."""
        prev = 1000000
        pre_target = prev * 10
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, prev * 5 // 3)

    def test_whale_100x_departure(self):
        """True whale (100×) leaves → target wants 100×, clamped to 167%."""
        prev = 1000000
        pre_target = prev * 100
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, prev * 5 // 3)

    def test_extreme_clamp_allows_40pct_diff_drop(self):
        """Verify the 5//3 factor equals approximately 40% difficulty drop.

        target_new / target_old = 5/3
        difficulty = 1/target (approximately)
        diff_new / diff_old = target_old / target_new = 3/5 = 0.6
        So difficulty drops to 60% = a 40% reduction per share.
        """
        prev = 3000000  # Use multiple of 3 for clean division
        result = asymmetric_clamp(prev * 100, prev)
        self.assertEqual(result, 5000000)  # 3M * 5/3 = 5M
        # Difficulty ratio: prev/result = 3M/5M = 0.6 → 40% drop
        self.assertAlmostEqual(float(prev) / result, 0.6, places=2)


class TestAsymmetricClampRecoverySequence(unittest.TestCase):
    """Test multi-share recovery sequences (simulating iterative retarget)."""

    def test_semiwhale_recovery_faster_than_symmetric(self):
        """Simulate recovery after 3× hashrate departure.

        With symmetric ±10%: needs ~11 shares to double target
        With asymmetric: needs ~2 shares to double target (40% per share)
        """
        target = 1000000
        # Each share after departure: target wants to go to 3M (3× prev)
        # but is clamped by the retarget rule.

        # Symmetric recovery (old behavior):
        sym_target = target
        sym_shares = 0
        while sym_target < target * 2:
            sym_target = clip(target * 3, (sym_target * 9 // 10, sym_target * 11 // 10))
            sym_shares += 1
            if sym_shares > 100:
                break  # safety

        # Asymmetric recovery (new behavior):
        asym_target = target
        asym_shares = 0
        while asym_target < target * 2:
            asym_target = asymmetric_clamp(target * 3, asym_target)
            asym_shares += 1
            if asym_shares > 100:
                break

        # Asymmetric should recover in fewer shares
        self.assertLess(asym_shares, sym_shares)
        # Specific: symmetric needs ~8, asymmetric needs ~2
        self.assertLessEqual(asym_shares, 3)
        self.assertGreaterEqual(sym_shares, 7)

    def test_whale_recovery_10x(self):
        """Simulate recovery after 10× hashrate departure.

        Target needs to rise 10× from starting point.
        Each share (extreme mode): target rises by 5/3 ≈ 1.667×
        1.667^n = 10 → n = ln(10)/ln(5/3) ≈ 4.5 → ~5 shares
        """
        target = 1000000
        desired_target = target * 10

        cur_target = target
        shares = 0
        while cur_target < desired_target * 9 // 10:  # within 10%
            cur_target = asymmetric_clamp(desired_target, cur_target)
            shares += 1
            if shares > 50:
                break

        # Should recover in ~5 shares
        self.assertLessEqual(shares, 6)

    def test_normal_hashrate_increase_unchanged(self):
        """Normal hashrate increase: target wants to drop, clamped at -10%.

        This should be identical to the old symmetric behavior.
        """
        target = 1000000
        desired = target // 2  # Hashrate doubled

        # Symmetric behavior
        sym = clip(desired, (target * 9 // 10, target * 11 // 10))
        # Asymmetric behavior
        asym = asymmetric_clamp(desired, target)

        # Both should clamp to -10%
        self.assertEqual(sym, asym)
        self.assertEqual(asym, target * 9 // 10)


class TestAsymmetricClampEdgeCases(unittest.TestCase):
    """Edge cases and boundary conditions."""

    def test_zero_prev_target(self):
        """Prev target of 0 (shouldn't happen, but be safe)."""
        # With prev=0, all clamp bounds are 0
        result = asymmetric_clamp(1000, 0)
        self.assertEqual(result, 0)  # clip(1000, (0, 0)) = 0

    def test_very_large_targets(self):
        """Test with realistic 256-bit target values."""
        prev = 2**240  # Realistic large target
        pre_target = prev * 200  # Massive departure

        result = asymmetric_clamp(pre_target, prev)
        expected = prev * 5 // 3
        self.assertEqual(result, expected)

    def test_minimum_target(self):
        """Test with minimum target (highest difficulty)."""
        prev = 1
        pre_target = 10  # Big relative jump but tiny absolute

        result = asymmetric_clamp(pre_target, prev)
        # 10 > 1 * 3 // 2 = 1, so extreme mode
        # clamp_hi = 1 * 5 // 3 = 1 (integer division)
        self.assertEqual(result, 1)  # 5//3 of 1 = 1

    def test_pre_target_below_lower_bound(self):
        """pre_target way below lower bound → clamped to 90%."""
        prev = 1000000
        pre_target = 1  # Basically zero
        result = asymmetric_clamp(pre_target, prev)
        self.assertEqual(result, prev * 9 // 10)

    def test_integer_arithmetic_no_float(self):
        """Verify all operations are pure integer — no float involved."""
        prev = 999999  # Not cleanly divisible
        pre_target = prev * 200

        result = asymmetric_clamp(pre_target, prev)
        self.assertIsInstance(result, (int, long) if sys.version_info[0] == 2 else int)


class TestAsymmetricClampSymmetryPreserved(unittest.TestCase):
    """Verify that non-extreme cases are identical to old symmetric clamp."""

    def test_all_normal_ratios_match_symmetric(self):
        """For ratios 0.5 to 1.5, asymmetric == symmetric."""
        prev = 1000000
        for pct in range(50, 151):  # 50% to 150%
            pre_target = prev * pct // 100
            sym_result = clip(pre_target, (prev * 9 // 10, prev * 11 // 10))
            asym_result = asymmetric_clamp(pre_target, prev)
            self.assertEqual(
                sym_result, asym_result,
                "Mismatch at %d%%: sym=%d asym=%d" % (pct, sym_result, asym_result)
            )


if __name__ == '__main__':
    unittest.main()
