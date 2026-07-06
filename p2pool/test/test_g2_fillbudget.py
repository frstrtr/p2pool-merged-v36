# -*- coding: utf-8 -*-
"""
G2 fill-budget KATs. Offline and deterministic: injected clock, integer
ramp arithmetic, refill deltas exact in IEEE floats (6666*15 == 99990.0).
Run from the repo root:  python2 -m unittest p2pool.test.test_g2_fillbudget
"""

from __future__ import division

import unittest

from p2pool import fillbudget


class FakeClock(object):
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


def ltc_bucket(clock):
    # The derived LTC defaults: rate=1MB//150s=6666 B/s, burst=250 kB,
    # floor=legacy 50 kB, ramp=4 shares.
    return fillbudget.FillBudget('ltc', rate=6666, burst=250000,
                                 floor=50000, ramp_shares=4, clock=clock)


SATURATED = 10 ** 9


class G2FillBudgetKAT(unittest.TestCase):

    # KAT-1 (the headline number): one saturated 150 s LTC block window,
    # 15 s shares. The bucket admits 1,099,920 new bytes where the fixed
    # legacy 50 kB cap admits 500,000 -- fill ratio rises 2.2x, with the
    # first post-reset share pinned to exactly the legacy 50 kB.
    def test_fill_ratio_rises_vs_legacy_cap(self):
        clock = FakeClock()
        b = ltc_bucket(clock)
        b.on_block_reset()
        grants = []
        for _ in range(10):
            clock.advance(15)
            g = b.grant()
            b.settle(g)
            grants.append(g)
        self.assertEqual(grants,
            [50000, 100000, 150000, 199980] + [99990] * 6)
        self.assertEqual(sum(grants), 1099920)
        self.assertTrue(sum(grants) > 10 * fillbudget.LEGACY_NEWTX_CAP)

    # KAT-2 (floor invariant / "never worse than v35"): in ANY state --
    # token exhaustion, mid-ramp, fast shares -- grant() >= 50000.
    def test_floor_never_violated(self):
        clock = FakeClock()
        b = ltc_bucket(clock)
        for dt in [0, 1, 3, 15, 0, 200, 1, 1, 1, 1, 300, 3]:
            clock.advance(dt)
            g = b.grant()
            self.assertTrue(g >= 50000, 'grant %d < legacy floor' % g)
            b.settle(SATURATED)          # worst case: massive over-spend
            if dt == 200:
                b.on_block_reset()

    # KAT-3 (get_work polling must not drain): grant() is a pure read.
    def test_polling_does_not_drain(self):
        clock = FakeClock()
        b = ltc_bucket(clock)
        b.on_block_reset()
        clock.advance(15)
        g0 = b.grant()
        for _ in range(1000):
            self.assertEqual(b.grant(), g0)
        self.assertEqual(int(b.tokens), 250000)

    # KAT-4 (parent-block boundary): first grant after a reset is EXACTLY
    # the legacy constant, regardless of how full the bucket is.
    def test_reset_restarts_ramp_at_legacy(self):
        clock = FakeClock()
        b = ltc_bucket(clock)
        for _ in range(5):
            clock.advance(15)
            b.settle(b.grant())
        b.on_block_reset()
        clock.advance(1)
        self.assertEqual(b.grant(), fillbudget.LEGACY_NEWTX_CAP)

    # KAT-5 (rate binds above the floor): fast 3 s share run from a full
    # bucket -- one burst-sized share, then the floor (v35-equivalent flow);
    # sustained above-floor throughput is bounded by rate.
    def test_fast_share_run_bounded(self):
        clock = FakeClock()
        b = ltc_bucket(clock)           # boot: full, ramp complete
        grants = []
        for _ in range(5):
            clock.advance(3)
            g = b.grant()
            b.settle(g)
            grants.append(g)
        self.assertEqual(grants[0], 250000)
        self.assertEqual(grants[1:], [50000] * 4)

    # KAT-6 (rider wiring): an aux bucket registered as riding the parent
    # resets on the parent's event -- no timer of its own.
    def test_rider_resets_with_parent(self):
        clock = FakeClock()
        book = fillbudget.FillBudgetBook()
        ltc = book.register('ltc', ltc_bucket(clock))
        doge = book.register('doge', fillbudget.FillBudget(
            'doge', rate=16666, burst=250000, floor=50000,
            ramp_shares=4, clock=clock), rides='ltc')
        for _ in range(3):
            clock.advance(15)
            ltc.settle(ltc.grant())
            doge.settle(doge.grant())
        book.on_block_reset('ltc')
        self.assertEqual(doge.shares_since_reset, 0)
        self.assertEqual(doge.tokens, 250000.0)


if __name__ == '__main__':
    unittest.main()
