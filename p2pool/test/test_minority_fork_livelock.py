# -*- coding: utf-8 -*-
# Deterministic reproduction of the kr1z1s / p2p-spb v36 desync (log span
# 2026-08-30..09-03, node 92.53.224.27), driving the REAL chain-selection,
# purge and serve-hold code paths -- never replicas of their logic.
#
# What the log shows (all numbers measured from the node's own debug log):
#
#   * the node mints continuously on its OWN ~17.4k-share fork (own tip is
#     always fresh, so the v0.20/0.21 stale-tip hold NEVER engages);
#   * the tracker total oscillates 17.4k <-> 26k+ every few minutes: the
#     foreign (real) chain is bulk-downloaded (hundreds of shares/second from
#     66.151.242.154), its verified backfill plateaus ~7000 short of
#     CHAIN_LENGTH=8640, then the whole partial download is purged and
#     re-downloaded -- 12,929 parent requests for 4,459 distinct hashes in 4h,
#     one wall parent (bca719a4) re-requested 247 times over 40 minutes while
#     both peers failed to serve it (the c2pool peer aborts the connection,
#     the python peer lacks the segment);
#   * best_share flaps to the foreign chain only in the brief windows where its
#     verified height crosses CHAIN_LENGTH, and relapses to the own fork as
#     soon as the download stalls >300s and clean_tracker eats it.
#
# The three coupled mechanisms reproduced below:
#
#   R1 (data.py OkayTracker.think + score): a foreign chain CANNOT win best
#      until CHAIN_LENGTH of its shares are verified locally -- score() returns
#      (height, None) below the cliff, which loses to ANY scoreable own chain
#      regardless of how much more work the foreign chain carries. With peers
#      that cannot deliver the full window, adoption is structurally
#      unreachable and the node mints its own fork forever.
#
#   R2 (node.py Node.clean_tracker): the eat-away purge reaps an in-download
#      head 300s after its frontier stalls, even while a parent request for
#      that exact head is still outstanding in `desired` -- forcing a full
#      re-download (the 505,935-shares-in-76-min loop F2 measured; still the
#      relapse door on v36-0.23 whenever peers stall longer than 300s).
#
#   R3 (work.py WorkerBridge._stale_tip_hold_active): even with the F4-correct
#      minority determination (fraction ~0.0), the duration-escape resumes
#      minting on the node's own stale minority fork after _stale_tip_hold_max
#      seconds, the first own share re-freshens the tip, and the hold can never
#      re-arm -- the fork is self-sustaining by construction. The serve-hold
#      stack therefore cannot prevent the disease R1/R2 sustain.
#
# Tests named test_invariant_* assert the CONVERGENCE INVARIANT the fix must
# establish and are marked expectedFailure -- they are RED on master by
# design; the fix removes the decorators. All other tests PASS on master and
# pin the diagnosed mechanism so it cannot drift silently.
#
# Self-skips (not errors) on a bare interpreter, matching test_convergence.py.

import time
import unittest

try:
    from p2pool import data as p2pool_data
    from p2pool import node as p2pool_node
    HAVE_DATA = True
    _IMPORT_ERR = None
except Exception as _e:
    p2pool_data = None
    p2pool_node = None
    HAVE_DATA = False
    _IMPORT_ERR = repr(_e)

try:
    from p2pool import work as p2pool_work
    from p2pool.work import WorkerBridge
    HAVE_WORK = True
    _WORK_IMPORT_ERR = None
except Exception as _e:
    p2pool_work = None
    WorkerBridge = None
    HAVE_WORK = False
    _WORK_IMPORT_ERR = repr(_e)


# --------------------------------------------------------------------------
# Shared synthetic surface (same shape as test_convergence.py): a REAL
# OkayTracker over a real forest, populated with light fake shares carrying
# exactly the attributes think()/score() read.
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


def _add_chain(tracker, first_parent, n, target, start_hash_id, now, verified=False):
    '''Append n shares to tracker (and optionally tracker.verified), linked
    from first_parent. Returns list of hashes, head last.'''
    hashes = []
    prev = first_parent
    for i in xrange(n):
        hsh = start_hash_id + i
        share = _FakeShare(hsh, prev, target, now - (n - i) * 15)
        tracker.add(share)
        if verified:
            tracker.verified.add(share)
        hashes.append(hsh)
        prev = hsh
    return hashes


# --------------------------------------------------------------------------
# R1: the CHAIN_LENGTH score cliff blocks adoption of a strictly-higher-work
#     foreign chain, no matter the work ratio -- REAL think()/score().
# --------------------------------------------------------------------------

class ScoreCliffBlocksAdoption(unittest.TestCase):
    CHAIN_LENGTH = 16
    OWN_TARGET = 2 ** 250            # low work/share (the whale fork's easy diff)
    FOREIGN_TARGET = 2 ** 220        # 2^30x more work per share
    OWN_TAIL = 10 ** 9               # sentinel: own genesis-ish missing parent
    FOREIGN_WALL = 10 ** 9 + 1       # sentinel: the parent peers never serve
                                     # (kr1z1s: bca719a4, requested 247x in 40min)

    def setUp(self):
        if not HAVE_DATA:
            self.skipTest('p2pool.data import failed: %s' % (_IMPORT_ERR,))

    def _tracker(self):
        net = _FakeNet(self.CHAIN_LENGTH)
        tracker = p2pool_data.OkayTracker(net)
        tracker.verify_time_budget = None
        tracker.verify_count_budget = None
        return tracker

    def _think(self, tracker):
        return tracker.think(lambda pb: 0, lambda pb: 0, 0, None, {})

    def test_foreign_chain_below_cliff_never_wins_despite_more_work(self):
        '''PINS THE DISEASE (passes on master): own fork fully verified and
        scoreable; foreign chain raw-only, one share short of the cliff, with
        ~2^30x the work -- think() still selects the own fork and merely
        requests the unreachable wall parent. This is the kr1z1s steady state:
        chain=17.4k own fork wins every cycle while the foreign download can
        never complete.'''
        tracker = self._tracker()
        now = time.time()
        own = _add_chain(tracker, self.OWN_TAIL, self.CHAIN_LENGTH + 8,
                         self.OWN_TARGET, 1000, now, verified=True)
        foreign = _add_chain(tracker, self.FOREIGN_WALL, self.CHAIN_LENGTH - 1,
                             self.FOREIGN_TARGET, 5000, now, verified=False)

        best, desired, decorated_heads, bad_peers, punish = self._think(tracker)

        self.assertEqual(best, own[-1],
            'expected the own fork to (wrongly, but per current design) stay best')
        # the foreign chain is not even a candidate: its verified height is 0
        self.assertNotIn(foreign[-1], [h for _, h in decorated_heads])
        # and think() is stuck requesting the wall parent forever
        self.assertIn(self.FOREIGN_WALL, [d[1] for d in desired],
            'think() must be requesting the unreachable wall parent')

    def test_control_full_window_adopts(self):
        '''CONTROL (passes on master): the moment the foreign chain's full
        CHAIN_LENGTH window is deliverable AND verified, real think() adopts
        it. Proves the blocker in the disease test is exactly the missing
        window, not the work comparison.'''
        tracker = self._tracker()
        now = time.time()
        own = _add_chain(tracker, self.OWN_TAIL, self.CHAIN_LENGTH + 8,
                         self.OWN_TARGET, 1000, now, verified=True)
        foreign = _add_chain(tracker, self.FOREIGN_WALL, self.CHAIN_LENGTH + 8,
                             self.FOREIGN_TARGET, 5000, now, verified=True)

        best, desired, decorated_heads, bad_peers, punish = self._think(tracker)
        self.assertEqual(best, foreign[-1],
            'a fully-verified higher-work foreign chain must win best')

    @unittest.expectedFailure
    def test_invariant_higher_work_reachable_chain_must_not_lose_forever(self):
        '''CONVERGENCE INVARIANT (RED on master -- remove the decorator with
        the fix): when a foreign candidate holds a verifiable contiguous
        segment of at least CHAIN_LENGTH//4 shares whose realized work rate
        strictly exceeds the own chain's, the node must stop treating the own
        fork as unconditionally best (adopt, or at minimum stop minting new
        own-fork shares) instead of discarding the candidate because the
        full window is not yet complete. Exact mechanism is the implementer's
        choice; this pins the outcome: best must NOT remain the own head.'''
        tracker = self._tracker()
        now = time.time()
        own = _add_chain(tracker, self.OWN_TAIL, self.CHAIN_LENGTH + 8,
                         self.OWN_TARGET, 1000, now, verified=True)
        seg = self.CHAIN_LENGTH // 2   # half a window, all verified, huge work
        foreign = _add_chain(tracker, self.FOREIGN_WALL, seg,
                             self.FOREIGN_TARGET, 5000, now, verified=True)

        best, desired, decorated_heads, bad_peers, punish = self._think(tracker)
        self.assertNotEqual(best, own[-1],
            'own minority fork stayed best against a live, verified, '
            'vastly-higher-work foreign segment -- the kr1z1s livelock')


# --------------------------------------------------------------------------
# R2: clean_tracker reaps a stalled in-download head even while its parent
#     request is still outstanding -- the relapse door. REAL Node.clean_tracker
#     over the same fake surface test_convergence.py uses.
# --------------------------------------------------------------------------

class _FakeItem(object):
    def __init__(self, time_seen):
        self.time_seen = time_seen
        self.VERSION = 36

class _FakeVerified(object):
    def __init__(self, items):
        self.items = set(items)
        self.removed = []
    def remove(self, h):
        self.removed.append(h)
        self.items.discard(h)

class _FakeCleanTracker(object):
    def __init__(self, net, heads, items, reverse, verified_hashes, think_result):
        self.net = net
        self.heads = dict(heads)
        self.items = dict(items)
        self.reverse = dict(reverse)
        self.verified = _FakeVerified(verified_hashes)
        self.tails = {}
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


def _make_node(tracker):
    node = p2pool_node.Node.__new__(p2pool_node.Node)
    node.tracker = tracker
    node.punish = 0
    node.bitcoind_work = _FakeVar({'previous_block': 0, 'bits': None})
    node.known_txs_var = _FakeVar({})
    node.best_share_var = _FakeVar(None)
    node.desired_var = _FakeVar([])
    node.cur_share_ver = 36
    node.p2p_node = None
    node.get_height_rel_highest = lambda pb: 0
    node.get_height = lambda pb: 0
    return node


class StallPurgeRelapse(unittest.TestCase):
    def setUp(self):
        if not HAVE_DATA:
            self.skipTest('p2pool.node import failed: %s' % (_IMPORT_ERR,))

    def _scenario(self, frontier_age, desired_pending):
        '''5 fresh top heads + 1 foreign in-download head whose frontier last
        received a share `frontier_age` seconds ago. When `desired_pending`,
        think() reports an outstanding parent request for that head's tail --
        the exact kr1z1s state while the wall parent times out on every peer.'''
        now = time.time()
        net = type('N', (), {'CHAIN_LENGTH': 100})()
        heads, items, reverse = {}, {}, {}
        decorated_heads = []
        for i in xrange(5):
            h, t = 1000 + i, 2000 + i
            heads[h] = t
            items[h] = _FakeItem(now)
            reverse[t] = set([h])
            decorated_heads.append((i + 10, h))
        FH, FT, FRONTIER = 42, 43, 44
        heads[FH] = FT
        items[FH] = _FakeItem(now - 4000)
        items[FRONTIER] = _FakeItem(now - frontier_age)
        reverse[FT] = set([FRONTIER])
        verified = set(1000 + i for i in xrange(5))
        decorated_heads = [(0.0, FH)] + decorated_heads
        desired = [(('10.0.0.1', 9333), FT, 0, 0)] if desired_pending else []
        think_result = (FH, desired, decorated_heads, set(), 0)
        tracker = _FakeCleanTracker(net, heads, items, reverse, verified, think_result)
        node = _make_node(tracker)
        return node, tracker, FH

    def test_stalled_download_is_reaped_on_master(self):
        '''PINS THE DISEASE (passes on master): a >300s peer stall reaps the
        in-download head even though its parent is still in `desired` -- the
        download restarts from scratch (the 12,929-request loop).'''
        node, tracker, FH = self._scenario(frontier_age=301, desired_pending=True)
        node.clean_tracker()
        self.assertIn(FH, tracker.removed,
            'expected master to reap the stalled in-download head (the relapse door)')

    @unittest.expectedFailure
    def test_invariant_head_with_outstanding_request_survives_stall(self):
        '''CONVERGENCE INVARIANT (RED on master -- remove the decorator with
        the fix): while think() still emits a parent request for a head's tail
        (the node has NOT given up downloading it), a peer-side stall must not
        destroy the partial download. Reaping is for chains the node no longer
        wants, not for chains the peers are slow to serve.'''
        node, tracker, FH = self._scenario(frontier_age=301, desired_pending=True)
        node.clean_tracker()
        self.assertNotIn(FH, tracker.removed,
            'in-download head with an outstanding desired request was purged '
            'on a peer stall -- forces the eternal re-download loop')

    def test_abandoned_head_still_reaped(self):
        '''GUARD for the fix: a head with a stale frontier AND no outstanding
        request (think() stopped asking) is genuinely dead and must be reaped.
        This must stay green after the fix -- memory stays bounded.'''
        node, tracker, FH = self._scenario(frontier_age=4000, desired_pending=False)
        node.clean_tracker()
        self.assertIn(FH, tracker.removed,
            'an abandoned head (no outstanding request) must still be reaped')


# --------------------------------------------------------------------------
# R3: the serve-hold stack cannot contain the fork -- the duration-escape
#     resumes own-fork minting for a CORRECTLY-determined minority node, and
#     the first own share disarms the hold forever. REAL WorkerBridge methods.
# --------------------------------------------------------------------------

class _Tick(object):
    __slots__ = ('timestamp',)
    def __init__(self, timestamp):
        self.timestamp = timestamp

class _HoldNet(object):
    PERSIST = True

class _HoldP2P(object):
    def __init__(self):
        self.peers = {0: object()}

class _HoldNode(object):
    def __init__(self, tip_timestamp):
        self.net = _HoldNet()
        self.p2p_node = _HoldP2P()
        self.best_share_var = _FakeVar('HEAD')
        self.tracker = type('T', (), {})()
        self.tracker.items = {'HEAD': _Tick(tip_timestamp)}


def _make_hold_bridge(tip_age):
    wb = WorkerBridge.__new__(WorkerBridge)
    wb.node = _HoldNode(time.time() - tip_age)
    wb.allow_peerless_mining = False
    return wb


@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class DurationEscapeSelfForkDoor(unittest.TestCase):
    def test_minority_hold_engages_then_duration_escape_resumes_minting(self):
        '''PINS THE DISEASE (passes on master v36-0.23): with the F4-correct
        minority determination (fraction 0.0 -- a live majority chain exists
        elsewhere), the hold engages... and _stale_tip_hold_max seconds later
        the duration-escape resumes serving work ON THE MINORITY FORK anyway.
        The escape stack cannot keep a minority node off its own fork.'''
        wb = _make_hold_bridge(tip_age=WorkerBridge._serve_stale_tip_max_age + 100)
        wb._local_pool_fraction = lambda: 0.0        # F4-correct: true minority
        self.assertTrue(wb._tip_is_stale())
        self.assertTrue(wb._stale_tip_hold_active(), 'hold must engage first')
        # two full stale windows pass with no adoption (the R1/R2 livelock)
        wb._stale_tip_hold_since = time.time() - (WorkerBridge._stale_tip_hold_max + 1)
        self.assertFalse(wb._stale_tip_hold_active(),
            'duration-escape resumed minting on the minority fork')

    def test_first_own_share_disarms_the_hold_forever(self):
        '''PINS THE DISEASE (passes on master): after resumption, the first
        own-minted share re-freshens the tip; _tip_is_stale() goes False, the
        hold state resets, and nothing ever pauses own-fork minting again --
        the fork is self-sustaining (kr1z1s: own tip age never exceeded ~45s
        while the node stayed 4 days deep on its own fork).'''
        wb = _make_hold_bridge(tip_age=WorkerBridge._serve_stale_tip_max_age + 100)
        wb._local_pool_fraction = lambda: 0.0
        self.assertTrue(wb._stale_tip_hold_active())
        # own miner finds a share on the fork -> tip timestamp = now
        wb.node.tracker.items['HEAD'] = _Tick(time.time())
        self.assertFalse(wb._stale_tip_hold_active(),
            'fresh own-fork tip must clear the hold (per current design)')
        self.assertIsNone(wb._stale_tip_hold_since,
            'hold timer must have been reset by the fresh tip')
        # tip stays fresh as long as local miners mint -> hold can never re-arm
        self.assertFalse(wb._tip_is_stale())


if __name__ == '__main__':
    unittest.main()
