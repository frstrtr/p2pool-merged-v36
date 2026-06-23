"""
Cross-implementation parity test: V36 AutoRatchet activation tail guard.

c2pool #288 ("ratchet-mint-accept-coupling") made c2pool's AutoRatchet
activation tail guard WORK-WEIGHTED so a node activates V36 at the EXACT
cross point its (and every peer's) accept gate -- Share.check(), the
"canonical 60% weighted version-switch rule" -- would accept a V36 share.

This test proves the Python reference impl computes the SAME cross point:

1. The activation tail guard in AutoRatchet.get_share_version and the accept
   gate in Share.check() both derive their verdict from the SAME function
   (get_desired_version_counts, which weights each vote by
   target_to_average_attempts(target) -- WORK, not flat share count) over the
   SAME [CHAIN_LENGTH*9//10, CHAIN_LENGTH] tail window. So for any vote
   distribution they reach an identical accept/reject verdict.

2. The work-weighted guard correctly DIVERGES from the old flat-count guard:
   when the oldest 10% is >=95% V36 by COUNT but <60% V36 by WORK (a few
   high-difficulty V35 holdouts), the node must NOT activate -- otherwise a
   95%-by-count activation outruns the 60%-by-work accept gate and wedges
   the crossing (the live #97 wedge this fix closes).

The C++ counterpart is c2pool src/impl/ltc/auto_ratchet.hpp (the WORK-WEIGHTED
tail guard) + share_check.hpp step 2 (the accept gate). Both must agree.
"""

import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
from p2pool import data as p2pool_data
from p2pool.data import (AutoRatchet, MergedMiningShare, PaddingBugfixShare,
                         get_desired_version_counts)
from p2pool.bitcoin import data as bitcoin_data


def target_for_attempts(a):
    """Inverse of target_to_average_attempts: pick a target whose work is ~a."""
    return (2 ** 256) // a - 1


class FakeShare(object):
    def __init__(self, desired_version, version, attempts):
        self.desired_version = desired_version
        self.VERSION = version
        self.target = target_for_attempts(attempts)


class FakeNet(object):
    NAME = 'litecoin_testnet'
    CHAIN_LENGTH = 100  # tail (oldest 10%) = 10 shares, enough to diverge flat vs work


class FakeTracker(object):
    """Linear chain; hash == index, 0 = oldest (genesis), N-1 = head."""
    def __init__(self, shares):
        self.shares = shares  # oldest..newest
        self.items = {i: s for i, s in enumerate(shares)}

    def get_height(self, h):
        return h + 1

    def get_nth_parent_hash(self, h, n):
        return h - n

    def get_chain(self, h, count):
        # newest-first, starting at h going back `count`
        for i in range(count):
            idx = h - i
            if idx < 0:
                return
            yield self.shares[idx]


def accept_gate_verdict(tracker, net, head):
    """Replicate Share.check() step 2 exactly (data.py:1399-1405): a V36
    successor is accepted iff the oldest 10% has >=60% WORK-weighted V36."""
    tail_ancestor = tracker.get_nth_parent_hash(head, net.CHAIN_LENGTH * 9 // 10)
    counts = get_desired_version_counts(tracker, tail_ancestor, net.CHAIN_LENGTH // 10)
    return not (counts.get(36, 0) < sum(counts.itervalues()) * 60 // 100)


def flat_count_tail_pct(tracker, net, head):
    """The OLD (buggy) flat-count tail guard, for the divergence assertion."""
    tail_ancestor = tracker.get_nth_parent_hash(head, net.CHAIN_LENGTH * 9 // 10)
    v36 = total = 0
    for s in tracker.get_chain(tail_ancestor, net.CHAIN_LENGTH // 10):
        total += 1
        if getattr(s, 'desired_version', s.VERSION) >= 36:
            v36 += 1
    return (v36 * 100 // total) if total else 0


def build_chain(holdout_attempts):
    """101-share chain (CHAIN_LENGTH=100). Window = indices 1..100; oldest 10%
    = indices 1..10. Four heavy V35 holdouts sit in the tail; everything else
    votes V36 light. So flat vote_pct over the window is 96% (>=95% to reach
    the activation branch) and the flat tail is 60% (the old guard would ALLOW),
    while the WORK-weighted tail varies with the holdouts' difficulty."""
    shares = []
    for i in range(101):
        if i == 0:
            shares.append(FakeShare(36, 35, 1000))                 # outside window
        elif 1 <= i <= 4:
            shares.append(FakeShare(35, 35, holdout_attempts))     # heavy V35 holdouts (tail)
        else:
            shares.append(FakeShare(36, 35, 1000))                 # light V36 voters
    return FakeTracker(shares)


class TestActivationTailGuardParity(unittest.TestCase):
    def setUp(self):
        self.net = FakeNet()
        self.head = 100  # head index

    def test_blocks_when_tail_underweight_by_work(self):
        # Tail flat-count is 60% V36 but the V35 holdouts carry 10x the work each
        # -> work-weighted V36 = 6000/46000 ~= 13% < 60%.  vote_pct is 96%.
        tracker = build_chain(holdout_attempts=10000)
        ar = AutoRatchet(None)
        cls, ver = ar.get_share_version(tracker, self.head, self.net)
        self.assertIs(cls, PaddingBugfixShare, "must WAIT: tail underweight by work")
        self.assertEqual(ar.state, AutoRatchet.STATE_VOTING)
        # Guard verdict must equal the accept gate verdict (the whole point).
        self.assertFalse(accept_gate_verdict(tracker, self.net, self.head))

    def test_divergence_from_flat_count(self):
        # SAME distribution: the OLD flat-count guard ALLOWS activation (tail
        # flat-count == 60%, not < 60%), the WORK-weighted guard blocks it.
        # This is exactly the count-vs-work wedge #288 closes.
        tracker = build_chain(holdout_attempts=10000)
        self.assertGreaterEqual(flat_count_tail_pct(tracker, self.net, self.head), 60,
                                "old flat-count guard would have ALLOWED")
        self.assertFalse(accept_gate_verdict(tracker, self.net, self.head),
                         "work-weighted gate rejects where flat-count would pass")

    def test_activates_when_tail_weight_sufficient(self):
        # Holdouts are now light (same work as voters): weighted tail == flat ==
        # 60% >= 60% -> activate, in agreement with the accept gate.
        tracker = build_chain(holdout_attempts=1000)
        ar = AutoRatchet(None)
        cls, ver = ar.get_share_version(tracker, self.head, self.net)
        self.assertIs(cls, MergedMiningShare, "must ACTIVATE: tail >=60% by work")
        self.assertTrue(accept_gate_verdict(tracker, self.net, self.head))

    def test_guard_matches_accept_gate_across_distributions(self):
        # Exhaustive parity: for a spread of holdout weights, the activation
        # decision (activated vs waiting) tracks the accept gate exactly.
        for attempts in (500, 1000, 1500, 1600, 1700, 2000, 5000, 50000, 100000):
            tracker = build_chain(holdout_attempts=attempts)
            ar = AutoRatchet(None)
            cls, _ = ar.get_share_version(tracker, self.head, self.net)
            activated = (cls is MergedMiningShare)
            self.assertEqual(activated, accept_gate_verdict(tracker, self.net, self.head),
                             "guard/accept-gate disagree at holdout_attempts=%d" % attempts)


if __name__ == '__main__':
    unittest.main()
