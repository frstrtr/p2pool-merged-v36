# -*- coding: utf-8 -*-
# Python 2.7 regression suite for the kr1z1s minority-fork ADOPTION livelock.
#
# Background (see work.py da77f64 for the SERVE half): v36-0.20 introduced a
# stale-tip serve-hold that manufactured deep sharechain forks; v0.21 bounded the
# serve-hold (majority/duration escape) but never touched the ADOPTION path, so a
# 0.21 minority-fork node still could not download+verify+switch to the
# higher-work majority chain -- it re-downloaded the same shares forever (spb:
# 505,935 shares in 76 min, zero convergence).
#
# Two coupled defects blocked adoption, both exercised here by driving the REAL
# methods (never a replica of their logic):
#
#   F1 (data.py OkayTracker.think): the synchronous verify loops verified up to
#      CHAIN_LENGTH foreign shares in ONE reactor callback; each Share.check() is
#      O(window) for V36 merged mining, so a deep adoption blocked the reactor
#      tens of seconds -- dropping the peers serving the majority chain. The fix
#      bounds the EXPENSIVE verifications per think() (a _VerifyBudget) and
#      resumes on the next think(); progress is monotone and non-blocking.
#
#   F2 (node.py Node.clean_tracker): the "eat away at heads" purge protected a
#      still-downloading head only while it was UNVERIFIED (and only for 120s of
#      frontier freshness). The instant the (now budgeted) verifier verified the
#      head it lost protection and was purged mid-catch-up, forcing a full
#      re-download -- the livelock. The fix protects any head whose frontier
#      received a share in the last 300s regardless of verification state; the
#      window still self-lapses so dead chains are still reaped.
#
# These tests self-skip (not error) when twisted / the p2pool package cannot be
# imported on a bare interpreter -- matching the repo's honest-green philosophy
# (see test_serve_gate.py / test_whale_latch.py). CI installs the deps so they
# actually run.

import time
import unittest

try:
    from p2pool import data as p2pool_data
    from p2pool import node as p2pool_node
    HAVE_DATA = True
    _IMPORT_ERR = None
except Exception as _e:  # ImportError or any transitive failure
    p2pool_data = None
    p2pool_node = None
    HAVE_DATA = False
    _IMPORT_ERR = repr(_e)

# work.py drags in twisted + the bitcoin backend; import it separately so the
# F1/F2 suites above still run even when only the F4 majority-escape suite below
# cannot import (honest-green: it self-skips rather than erroring).
try:
    from p2pool import work as p2pool_work
    HAVE_WORK = True
    _WORK_IMPORT_ERR = None
except Exception as _e:
    p2pool_work = None
    HAVE_WORK = False
    _WORK_IMPORT_ERR = repr(_e)


# --------------------------------------------------------------------------
# F1a: the _VerifyBudget primitive in isolation (pure, deterministic).
# --------------------------------------------------------------------------

class VerifyBudgetPrimitive(unittest.TestCase):
    def setUp(self):
        if not HAVE_DATA:
            self.skipTest('p2pool.data import failed: %s' % (_IMPORT_ERR,))

    def test_count_budget_bounds_spend(self):
        b = p2pool_data._VerifyBudget(time_budget=None, count_budget=3)
        self.assertFalse(b.exhausted())
        b.spend(); b.spend()
        self.assertFalse(b.exhausted())  # 2 of 3
        b.spend()
        self.assertTrue(b.exhausted())   # 3 of 3
        self.assertEqual(b.spent, 3)

    def test_none_budget_is_unbounded(self):
        b = p2pool_data._VerifyBudget(time_budget=None, count_budget=None)
        for _ in xrange(10000):
            self.assertFalse(b.exhausted())
            b.spend()
        self.assertEqual(b.spent, 10000)

    def test_time_budget_expires(self):
        # A zero time budget is exhausted immediately (deadline == now).
        b = p2pool_data._VerifyBudget(time_budget=0.0, count_budget=None)
        # time_budget falsy -> deadline None -> NOT time-bounded; only a positive
        # budget arms the deadline. Prove a positive-but-elapsed budget expires.
        b2 = p2pool_data._VerifyBudget(time_budget=0.001, count_budget=None)
        time.sleep(0.01)
        self.assertTrue(b2.exhausted())
        self.assertFalse(b.exhausted())  # falsy time budget is a no-op, not a 0 deadline


# --------------------------------------------------------------------------
# Shared synthetic surface: a real OkayTracker over a real forest, populated
# with light fake shares carrying exactly the attributes think()/score() read.
# --------------------------------------------------------------------------

class _FakeParent(object):
    BLOCK_PERIOD = 150
    padding_bugfix = False

class _FakeNet(object):
    def __init__(self, chain_length):
        self.CHAIN_LENGTH = chain_length
        self.SHARE_PERIOD = 15
        self.PARENT = _FakeParent()

class _FakeShare(object):
    # Only the surface OkayTracker.think()/score() actually read.
    def __init__(self, hsh, previous_hash, target, time_seen):
        self.hash = hsh
        self.previous_hash = previous_hash
        self.target = target
        self.max_target = target
        self.time_seen = time_seen
        self.timestamp = int(time_seen)
        self.peer_addr = ('10.0.0.1', 9333)
        self.VERSION = 36
        self.naughty = False
        self.header = {'previous_block': 0}
    def should_punish_reason(self, previous_block, bits, tracker, known_txs):
        return 0, ''


def _build_tracker(chain_length, n_heads, head_height):
    '''Real OkayTracker over a real forest: n_heads independent chains, each
    `head_height` shares tall, each rooted at its OWN distinct missing tail (so
    every head is an UNVERIFIED head that loop-1 of think() will try to verify).
    Returns (tracker, list_of_head_hashes).'''
    net = _FakeNet(chain_length)
    tracker = p2pool_data.OkayTracker(net)
    target = 2 ** 240
    head_hashes = []
    hid = 1
    now = time.time()
    for c in xrange(n_heads):
        # distinct missing tail per chain: a hash never added as an item
        tail = 10 ** 9 + c  # sentinel parent hash, not an item
        prev = tail
        chain_hashes = []
        for h in xrange(head_height):
            hsh = hid
            hid += 1
            tracker.add(_FakeShare(hsh, prev, target, now))
            chain_hashes.append(hsh)
            prev = hsh
        head_hashes.append(chain_hashes[-1])
    return tracker, head_hashes


# --------------------------------------------------------------------------
# F1b: think() bounds the EXPENSIVE verifications per call yet converges over
#      repeated calls -- driving the REAL OkayTracker.think().
# --------------------------------------------------------------------------

class ThinkVerifyBudget(unittest.TestCase):
    def setUp(self):
        if not HAVE_DATA:
            self.skipTest('p2pool.data import failed: %s' % (_IMPORT_ERR,))

    def _instrument(self, tracker):
        '''Replace attempt_verify with a counting stub that "verifies" a whole
        chain in one call (adds head + all ancestors present in items to
        tracker.verified), so loop-2 catch-up has nothing left to do and the
        budget count reflects loop-1 head verifications 1:1. Returns a mutable
        [count] list.'''
        calls = [0]
        real_items = tracker.items
        def fake_attempt_verify(share, block_abs_height_func, known_txs):
            if share.hash in tracker.verified.items:
                return True
            calls[0] += 1
            # add this share + its ancestor spine (tail-first) to verified
            spine = []
            cur = share.hash
            while cur in real_items and cur not in tracker.verified.items:
                spine.append(cur)
                cur = real_items[cur].previous_hash
            for h in reversed(spine):
                if h not in tracker.verified.items:
                    tracker.verified.add(real_items[h])
            return True
        tracker.attempt_verify = fake_attempt_verify
        return calls

    def test_budget_bounds_per_call_and_converges(self):
        # CHAIN_LENGTH >= 16 so score()'s CHAIN_LENGTH//16 block-sample is >= 1;
        # head chains taller than CHAIN_LENGTH so loop-1 of think() actually
        # verifies (it only descends when head_height > CHAIN_LENGTH).
        CHAIN_LENGTH = 16
        N_HEADS = 8
        BUDGET = 3
        tracker, heads = _build_tracker(CHAIN_LENGTH, N_HEADS, head_height=CHAIN_LENGTH + 4)
        tracker.verify_time_budget = None      # count-only, deterministic
        tracker.verify_count_budget = BUDGET
        calls = self._instrument(tracker)

        block_rel = lambda prev_block: 0
        block_abs = lambda prev_block: 0

        # Repeatedly think(); each call must do at most BUDGET expensive verifies,
        # and all N_HEADS chains must become verified within a bounded number of
        # calls (ceil(N/BUDGET) == 3).
        per_call = []
        max_calls = 10
        for i in xrange(max_calls):
            before = calls[0]
            tracker.think(block_rel, block_abs, 0, None, {})
            spent = calls[0] - before
            per_call.append(spent)
            self.assertLessEqual(spent, BUDGET,
                'think() call %d ran %d expensive verifies, over budget %d' % (i, spent, BUDGET))
            verified_heads = sum(1 for h in heads if h in tracker.verified.items)
            if verified_heads == N_HEADS:
                break

        verified_heads = sum(1 for h in heads if h in tracker.verified.items)
        self.assertEqual(verified_heads, N_HEADS,
            'not all heads converged: %d/%d after %r' % (verified_heads, N_HEADS, per_call))
        # convergence took the expected number of budgeted rounds, proving the
        # bound actually throttled (and did not silently verify everything at once)
        self.assertGreaterEqual(len([x for x in per_call if x > 0]), 3,
            'expected >=3 throttled rounds, got %r' % (per_call,))

    def test_unbounded_budget_verifies_in_one_call(self):
        # Control: with no budget, the legacy behaviour verifies every head in a
        # single think() -- confirms the throttling above is the budget's doing.
        CHAIN_LENGTH = 16
        N_HEADS = 8
        tracker, heads = _build_tracker(CHAIN_LENGTH, N_HEADS, head_height=CHAIN_LENGTH + 4)
        tracker.verify_time_budget = None
        tracker.verify_count_budget = None
        self._instrument(tracker)
        tracker.think(lambda pb: 0, lambda pb: 0, 0, None, {})
        verified_heads = sum(1 for h in heads if h in tracker.verified.items)
        self.assertEqual(verified_heads, N_HEADS)


# --------------------------------------------------------------------------
# F2: clean_tracker must NOT purge a head whose frontier is freshly extended,
#     even after the head itself becomes verified -- driving the REAL
#     Node.clean_tracker "eat away at heads" logic against a faked tracker
#     surface (the exact surface the loop reads).
# --------------------------------------------------------------------------

class _FakeItem(object):
    def __init__(self, time_seen):
        self.time_seen = time_seen
        self.VERSION = 36

class _FakeVerified(object):
    def __init__(self, items):
        self.items = set(items)  # hashes considered "verified"
        self.removed = []
    def remove(self, h):
        self.removed.append(h)
        self.items.discard(h)

class _FakeTracker(object):
    '''Exactly the surface Node.clean_tracker's eat-away loop + drop-tails loop
    read. think() is stubbed to hand the loop a controlled decorated_heads /
    desired; everything else is real dict state the REAL loop mutates.'''
    def __init__(self, net, heads, items, reverse, verified_hashes, think_result):
        self.net = net
        self.heads = dict(heads)              # head_hash -> tail_hash
        self.items = dict(items)              # hash -> _FakeItem
        self.reverse = dict(reverse)          # tail_hash -> set(frontier hashes)
        self.verified = _FakeVerified(verified_hashes)
        self.tails = {}                       # empty -> drop-tails loop is a no-op
        self._think_result = think_result
        self.removed = []
    def think(self, *a, **k):
        return self._think_result
    def get_height(self, h):
        return 0
    def remove(self, h):
        self.removed.append(h)
        self.heads.pop(h, None)
        self.items.pop(h, None)


class _FakeEvent(object):
    def happened(self, *a, **k):
        pass

class _FakeVar(object):
    def __init__(self, value=None):
        self.value = value
        self.changed = _FakeEvent()
    def set(self, value):
        self.value = value


def _make_node_with_tracker(tracker):
    node = p2pool_node.Node.__new__(p2pool_node.Node)
    node.tracker = tracker
    node.punish = 0
    node.bitcoind_work = _FakeVar({'previous_block': 0, 'bits': None})
    node.known_txs_var = _FakeVar({})
    node.best_share_var = _FakeVar(None)   # set_best_share() at the tail of clean_tracker
    node.desired_var = _FakeVar([])
    node.cur_share_ver = 36
    node.p2p_node = None
    node.get_height_rel_highest = lambda pb: 0
    node.get_height = lambda pb: 0
    return node


class CleanTrackerFrontierRetention(unittest.TestCase):
    def setUp(self):
        if not HAVE_DATA:
            self.skipTest('p2pool.node import failed: %s' % (_IMPORT_ERR,))

    def _scenario(self, foreign_verified):
        '''6 heads: 5 fresh "top" heads (in decorated_heads[-5:], always kept) +
        1 foreign head that is being downloaded -- its OWN time_seen is stale
        (>300s) but its FRONTIER (reverse[tail]) received a share 10s ago. The
        foreign head is ranked LAST (outside top-5) so it is eligible to be
        eaten. `foreign_verified` toggles whether the foreign head hash is in
        tracker.verified.items (the exact bit master gated protection on).'''
        now = time.time()
        net = type('N', (), {'CHAIN_LENGTH': 100})()

        heads = {}
        items = {}
        reverse = {}
        decorated_heads = []  # (score, head_hash); last 5 are the protected "top"

        # 5 fresh top heads
        for i in xrange(5):
            h = 1000 + i
            t = 2000 + i
            heads[h] = t
            items[h] = _FakeItem(now)            # fresh -> also protected by the 300s own-time gate
            reverse[t] = set([h])
            decorated_heads.append((i + 10, h))  # high score

        # foreign head under download
        FH = 42
        FT = 43  # foreign tail (missing parent, being requested)
        heads[FH] = FT
        items[FH] = _FakeItem(now - 4000)        # head itself is OLD (own-time gate does NOT protect)
        # frontier: the oldest downloaded shares whose previous_hash == FT,
        # received 10s ago (active download)
        FRONTIER = 44
        items[FRONTIER] = _FakeItem(now - 10)
        reverse[FT] = set([FRONTIER])

        verified = set()
        for i in xrange(5):
            verified.add(1000 + i)
        if foreign_verified:
            verified.add(FH)

        # foreign head ranked FIRST (index 0) so it is NOT in decorated_heads[-5:]
        decorated_heads = [(0.0, FH)] + decorated_heads

        think_result = (FH, [], decorated_heads, set(), 0)
        tracker = _FakeTracker(net, heads, items, reverse, verified, think_result)
        node = _make_node_with_tracker(tracker)
        return node, tracker, FH

    def test_downloading_head_retained_when_unverified(self):
        node, tracker, FH = self._scenario(foreign_verified=False)
        node.clean_tracker()
        self.assertNotIn(FH, tracker.removed,
            'unverified freshly-extended foreign head was purged mid-download')
        self.assertIn(FH, tracker.heads)

    def test_downloading_head_retained_after_it_becomes_verified(self):
        # THE regression: on master the eat-away protection was gated on
        # "share_hash not in verified.items", so the moment the budgeted verifier
        # verified the foreign head it lost protection and was purged mid-catch-up
        # -> the 505k-share re-download livelock. With the fix the fresh frontier
        # protects it regardless of verification state.
        node, tracker, FH = self._scenario(foreign_verified=True)
        node.clean_tracker()
        self.assertNotIn(FH, tracker.removed,
            'VERIFIED freshly-extended foreign head was purged mid-catch-up '
            '(the kr1z1s adoption livelock)')
        self.assertIn(FH, tracker.heads)

    def test_dead_head_still_reaped(self):
        # Belt-and-suspenders: a head whose frontier went quiet >300s ago is NOT
        # protected -- the window self-lapses so genuinely dead forks are reaped.
        node, tracker, FH = self._scenario(foreign_verified=True)
        # age the frontier past the 300s window
        tracker.items[44].time_seen = time.time() - 4000
        node.clean_tracker()
        self.assertIn(FH, tracker.removed,
            'a head with a stale (>300s) frontier should still be reaped')


# --------------------------------------------------------------------------
# F4 (v36-0.21/0.22 majority-escape denominator) suite REMOVED in v36-0.24:
# the stale-tip serve-hold and its majority/duration escapes were deleted
# entirely (a node must never be able to declare itself the majority from its
# own local state and self-sustain a fork). See
# p2pool/test/test_minority_fork_livelock.py::StaleTipHoldRemoved for the
# removal + retained-serve-clamp assertions.
# --------------------------------------------------------------------------


if __name__ == '__main__':
    unittest.main()
