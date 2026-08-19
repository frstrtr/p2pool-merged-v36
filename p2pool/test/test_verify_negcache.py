"""Regression tests for the OkayTracker negative-verify cache.

Background
----------
OkayTracker.think() re-runs attempt_verify() on every unverified head EVERY
cycle (fires ~1/s and on every share/block).  For V36 merged mining,
Share.check() rebuilds the canonical merged (DOGE) coinbase from PPLNS weights
-- an O(window) computation -- so a single stuck foreign / mis-versioned share
that fails the merged-coinbase check would be re-verified forever, pegging CPU.

The fix negatively caches ONLY deterministic check() failures (a verdict that
is a pure function of the share's own immutable content + its immutable
ancestors' PPLNS weights).  A DETERMINISTIC failure raises
data.DeterministicShareCheckFailure; a TRANSIENT failure (missing parent,
chain-not-long-enough, future timestamp) raises anything else and is NOT cached,
so it is retried once the missing data arrives.

think() invokes attempt_verify() once per unverified head per cycle, so a loop
of N attempt_verify() calls on a persistently-present head is exactly N think()
cycles for that head.
"""

import unittest

from p2pool import data
from p2pool.networks import litecoin as litecoin_net


class FakeShare(object):
    """Minimal share stand-in the forest tracker + attempt_verify accept.

    check() increments a counter and either raises a caller-chosen exception or
    returns a sentinel gentx on success.  A genesis share (previous_hash=None)
    keeps attempt_verify's height/AssertionError guard from firing so control
    always reaches check().
    """
    def __init__(self, hash, exc, previous_hash=None, target=2**249):
        self.hash = hash
        self.previous_hash = previous_hash
        self.share_data = dict(previous_share_hash=previous_hash)
        self.target = target
        self.max_target = target
        self.peer_addr = None
        self.exc = exc            # exception instance to raise, or None => success
        self.check_calls = 0
        self.gentx = None

    def check(self, tracker, known_txs, block_abs_height_func=None):
        self.check_calls += 1
        if self.exc is not None:
            raise self.exc
        return 'GENTX-OK'


class Test(unittest.TestCase):
    def _tracker(self):
        return data.OkayTracker(litecoin_net)

    def test_deterministic_failure_checked_once_across_many_cycles(self):
        # A foreign / mis-versioned share whose merged-coinbase check
        # deterministically fails must be check()'d exactly ONCE no matter how
        # many think() cycles hit it.
        t = self._tracker()
        share = FakeShare(hash=1,
                          exc=data.DeterministicShareCheckFailure(
                              'merged coinbase verification failed: canonical '
                              'merkle_root != header merkle_root'))
        t.add(share)

        CYCLES = 50
        for _ in xrange(CYCLES):
            self.assertFalse(t.attempt_verify(share, None, {}))

        # check() ran once; the other 49 cycles short-circuited on the cache.
        self.assertEqual(share.check_calls, 1)
        self.assertIn(share.hash, t._negative_verify_cache)
        self.assertNotIn(share.hash, t.verified.items)

    def test_identical_share_rearrival_stays_cached(self):
        # Even a brand-new object with the SAME hash (an identical foreign share
        # re-arriving from a peer) must not trigger a recompute -- the verdict is
        # keyed by hash, which commits to all content.
        t = self._tracker()
        s1 = FakeShare(hash=7,
                       exc=data.DeterministicShareCheckFailure('share_info invalid'))
        t.add(s1)
        self.assertFalse(t.attempt_verify(s1, None, {}))
        self.assertEqual(s1.check_calls, 1)

        # Simulate re-arrival: same hash, fresh object, fresh counter.
        s2 = FakeShare(hash=7,
                       exc=data.DeterministicShareCheckFailure('share_info invalid'))
        self.assertFalse(t.attempt_verify(s2, None, {}))
        self.assertEqual(s2.check_calls, 0)  # never even entered check()

    def test_transient_failure_not_cached_and_retried_after_parent_arrives(self):
        # A transient failure (e.g. missing parent -> KeyError) must NOT be
        # cached: it is retried every cycle, and succeeds once the data arrives.
        t = self._tracker()
        share = FakeShare(hash=2, exc=KeyError('previous_share_hash not in tracker'))
        t.add(share)

        CYCLES = 10
        for _ in xrange(CYCLES):
            self.assertFalse(t.attempt_verify(share, None, {}))

        # Retried every single cycle -- never negatively cached.
        self.assertEqual(share.check_calls, CYCLES)
        self.assertNotIn(share.hash, t._negative_verify_cache)

        # Parent arrives: the very same share now verifies.
        share.exc = None
        self.assertTrue(t.attempt_verify(share, None, {}))
        self.assertEqual(share.check_calls, CYCLES + 1)
        self.assertIn(share.hash, t.verified.items)
        self.assertNotIn(share.hash, t._negative_verify_cache)

    def test_transient_ValueError_is_not_cached(self):
        # 'share chain not long enough' and 'timestamp in the future' raise plain
        # ValueError (transient); DeterministicShareCheckFailure subclasses
        # ValueError, so the classifier must distinguish by TYPE, not by being a
        # ValueError.  A plain ValueError must stay uncached.
        t = self._tracker()
        share = FakeShare(hash=3,
                          exc=ValueError('share chain not long enough (height=5, need 8640)'))
        t.add(share)
        for _ in xrange(5):
            self.assertFalse(t.attempt_verify(share, None, {}))
        self.assertEqual(share.check_calls, 5)
        self.assertNotIn(share.hash, t._negative_verify_cache)

    def test_negative_cache_is_bounded(self):
        # The cache evicts oldest entries so a flood of distinct bad shares can
        # not grow it without bound.
        t = self._tracker()
        t._negative_verify_cache_max = 100
        for h in xrange(250):
            s = FakeShare(hash=1000 + h,
                          exc=data.DeterministicShareCheckFailure('gentx mismatch'))
            t.add(s)
            t.attempt_verify(s, None, {})
        self.assertLessEqual(len(t._negative_verify_cache), 100)
        # Newest survive, oldest evicted.
        self.assertIn(1000 + 249, t._negative_verify_cache)
        self.assertNotIn(1000 + 0, t._negative_verify_cache)


if __name__ == '__main__':
    unittest.main()
