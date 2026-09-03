# -*- coding: utf-8 -*-
# Convergence tests for the kr1z1s / p2p-spb v36 minority-fork desync
# (log span 2026-08-30..09-03, node 92.53.224.27). These drive the REAL
# chain-selection, purge, download-failover and serve-hold code paths -- never
# replicas of their logic -- and pin BOTH the diagnosed disease (so it cannot
# silently return) AND the convergence invariant the v36-0.24 fix establishes.
#
# ROOT CAUSE (measured from the node's own debug log):
#
#   * The node minted continuously on its OWN ~17.4k-share fork. Its own tip was
#     always fresh (local miners), so the v0.20/0.21 stale-tip serve-hold never
#     even engaged during the sustained desync -- the F1/F2/F4 hold patches could
#     not have fixed it. Worse, the v0.21/0.22 "majority-escape" let a node's OWN
#     local state declare itself the majority and RESUME minting on its own
#     (minority) tip: a self-desync / fork-birth door and an external attack
#     surface. v35 (jtoomim) had NO hold, NO escapes and NO emergency decay and
#     converged flawlessly with the identical chain-selection code.
#
#   * The real chain WAS bulk-downloaded (hundreds of shares/s from
#     66.151.242.154) but its verified backfill plateaued ~7000 short of
#     CHAIN_LENGTH at a wall parent the peers could not serve (bca719a4 requested
#     247x in 40 min; the c2pool peer aborts, the other peer lacked the segment).
#     Every time the download stalled >300s, clean_tracker's self-lapsing 300s
#     frontier protection purged the whole partial chain and it re-downloaded
#     from scratch -- 12,929 parent requests for 4,459 distinct hashes in 4h.
#     download_shares picked a RANDOM peer and had no memory of which peer had
#     already black-holed a hash, so one non-serving peer could trap the fetch.
#
# THE v36-0.24 FIX (three coupled, minimal changes; v35 parity is the north star):
#
#   (A) work.py -- REMOVE the stale-tip serve-hold and its majority/duration
#       escapes entirely (a node can never declare itself the majority and
#       self-sustain a fork). Restore v35 serve-always. Keep ONLY the
#       consensus-safe serve-side easing clamp, which contains the emergency-decay
#       flood without ever refusing work.
#
#   (B) node.py clean_tracker -- protect a head whose missing parent think() is
#       STILL requesting (its `desired` set), so a slow/black-hole peer can no
#       longer trigger the purge+full-redownload loop. Bounded by (C): once the
#       WHOLE peer set has failed the parent it is abandoned and reapable, so
#       memory stays bounded and a peer re-advertising an unservable fragment
#       cannot pin the tracker.
#
#   (C) node.py download_shares -- per-(hash,peer) failure memory + failover:
#       skip peers that recently failed THIS hash, prefer the advertiser, and
#       back off only when EVERY peer has failed it. This is the attack-vector
#       guarantee: no single slow/black-hole/malicious peer can wedge a fetch.
#
#   The CHAIN_LENGTH score cliff (data.py score/think) is DELIBERATELY UNCHANGED:
#   it is v35-identical anti-Sybil depth. The fix converges by making the full
#   window reliably deliverable (B+C) so the cliff is reached and the existing
#   score() adopts the real chain -- NOT by letting a partial high-work fragment
#   win best, which would itself be a short-chain desync attack vector.
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
# R1: the CHAIN_LENGTH cliff is v35-identical anti-Sybil depth and stays.
#     A raw foreign chain cannot win best until its full window is verified;
#     a *partial* high-work segment must NOT win best (that would be a
#     short-chain desync vector). Convergence comes from completing the
#     download (R2/R3), after which the existing score() adopts the chain.
# --------------------------------------------------------------------------

class ScoreCliffIsPreservedAntiSybil(unittest.TestCase):
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

    def test_raw_foreign_chain_below_cliff_never_wins_but_is_requested(self):
        '''The steady state before convergence: own fork fully verified and
        scoreable; the foreign chain raw-only, one share short of the cliff,
        with ~2^30x the work. think() keeps the own fork best (correct: the
        foreign window is not proven yet) AND keeps requesting the missing wall
        parent so the download can complete.'''
        tracker = self._tracker()
        now = time.time()
        own = _add_chain(tracker, self.OWN_TAIL, self.CHAIN_LENGTH + 8,
                         self.OWN_TARGET, 1000, now, verified=True)
        foreign = _add_chain(tracker, self.FOREIGN_WALL, self.CHAIN_LENGTH - 1,
                             self.FOREIGN_TARGET, 5000, now, verified=False)

        best, desired, decorated_heads, bad_peers, punish = self._think(tracker)

        self.assertEqual(best, own[-1])
        self.assertNotIn(foreign[-1], [h for _, h in decorated_heads])
        self.assertIn(self.FOREIGN_WALL, [d[1] for d in desired],
            'think() must keep requesting the unreachable wall parent')

    def test_control_full_window_adopts(self):
        '''CONVERGENCE END STATE: the moment the foreign chain's full
        CHAIN_LENGTH window is delivered AND verified (what the R2/R3 fixes make
        reliably reachable), the unchanged real think() adopts it. Proves the
        cliff is the only gate and it is reached by completing the download.'''
        tracker = self._tracker()
        now = time.time()
        own = _add_chain(tracker, self.OWN_TAIL, self.CHAIN_LENGTH + 8,
                         self.OWN_TARGET, 1000, now, verified=True)
        foreign = _add_chain(tracker, self.FOREIGN_WALL, self.CHAIN_LENGTH + 8,
                             self.FOREIGN_TARGET, 5000, now, verified=True)

        best, desired, decorated_heads, bad_peers, punish = self._think(tracker)
        self.assertEqual(best, foreign[-1],
            'a fully-verified higher-work foreign chain must win best')

    def test_partial_high_work_segment_must_not_win_best_sybil_guard(self):
        '''SECURITY INVARIANT (green -- the cliff is intentionally kept): a
        foreign candidate with only half a window verified must NOT win best,
        even at ~2^30x the own chain's per-share work. Letting a short high-work
        segment win would let one peer feed a cheap high-difficulty fragment and
        hijack the sharechain tip -- exactly the desync/attack class this work
        closes. Convergence is achieved by completing the download to the cliff
        (test_control_full_window_adopts), never by weakening the cliff.'''
        tracker = self._tracker()
        now = time.time()
        own = _add_chain(tracker, self.OWN_TAIL, self.CHAIN_LENGTH + 8,
                         self.OWN_TARGET, 1000, now, verified=True)
        seg = self.CHAIN_LENGTH // 2   # half a window, verified, huge work
        _add_chain(tracker, self.FOREIGN_WALL, seg,
                   self.FOREIGN_TARGET, 5000, now, verified=True)

        best, desired, decorated_heads, bad_peers, punish = self._think(tracker)
        self.assertEqual(best, own[-1],
            'a sub-CHAIN_LENGTH foreign segment must not be adoptable as best')


# --------------------------------------------------------------------------
# R2: clean_tracker must NOT purge a head whose missing parent think() is still
#     requesting (the relapse door) -- but MUST reap it once the parent is
#     abandoned (no request, or the whole peer set failed it). REAL
#     Node.clean_tracker over the same fake surface test_convergence.py uses.
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


def _make_node(tracker, p2p_node=None):
    node = p2pool_node.Node.__new__(p2pool_node.Node)
    node.tracker = tracker
    node.punish = 0
    node.bitcoind_work = _FakeVar({'previous_block': 0, 'bits': None})
    node.known_txs_var = _FakeVar({})
    node.best_share_var = _FakeVar(None)
    node.desired_var = _FakeVar([])
    node.cur_share_ver = 36
    node.p2p_node = p2p_node
    node.get_height_rel_highest = lambda pb: 0
    node.get_height = lambda pb: 0
    return node


class _FakePeer(object):
    def __init__(self, addr):
        self.addr = addr


def _make_p2pnode(peer_addrs):
    '''A real P2PNode instance (no __init__/reactor) carrying just the state the
    failover + abandonment helpers read: connected peers and the failure map.'''
    p = p2pool_node.P2PNode.__new__(p2pool_node.P2PNode)
    p._share_fetch_failures = {}
    p.peers = dict((i, _FakePeer(a)) for i, a in enumerate(peer_addrs))
    return p


class StallPurgeConverges(unittest.TestCase):
    def setUp(self):
        if not HAVE_DATA:
            self.skipTest('p2pool.node import failed: %s' % (_IMPORT_ERR,))

    def _scenario(self, frontier_age, desired_pending, p2p_node=None):
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
        node = _make_node(tracker, p2p_node=p2p_node)
        return node, tracker, FH, FT

    def test_head_with_outstanding_request_survives_peer_stall(self):
        '''CONVERGENCE INVARIANT (the fix): while think() still emits a parent
        request for a head's tail, a >300s peer-side stall must NOT destroy the
        partial download. This closes the relapse door that forced kr1z1s to
        re-download the real chain from scratch forever.'''
        node, tracker, FH, FT = self._scenario(frontier_age=301, desired_pending=True)
        node.clean_tracker()
        self.assertNotIn(FH, tracker.removed,
            'in-download head with an outstanding request must survive a stall')

    def test_abandoned_head_no_request_is_reaped(self):
        '''MEMORY BOUND: a head with a stale frontier AND no outstanding request
        (think() stopped asking) is genuinely dead and must be reaped.'''
        node, tracker, FH, FT = self._scenario(frontier_age=4000, desired_pending=False)
        node.clean_tracker()
        self.assertIn(FH, tracker.removed,
            'an abandoned head (no outstanding request) must still be reaped')

    def test_head_reaped_when_all_peers_failed_the_parent(self):
        '''MEMORY BOUND under attack: even with the parent still desired, once
        EVERY connected peer has recently failed to serve it the head is
        abandoned and reaped -- a malicious peer re-advertising an unservable
        fragment cannot pin the tracker.'''
        p2p = _make_p2pnode([('1.1.1.1', 9333), ('2.2.2.2', 9333)])
        FT = 43
        # both peers have just failed to serve the tail parent
        for peer in p2p.peers.values():
            p2p._record_fetch_failure(FT, peer)
        node, tracker, FH, _ = self._scenario(frontier_age=301, desired_pending=True,
                                              p2p_node=p2p)
        self.assertTrue(node._desired_parent_abandoned(FT))
        node.clean_tracker()
        self.assertIn(FH, tracker.removed,
            'head must be reaped once the whole peer set has failed its parent')

    def test_head_survives_when_one_peer_still_viable(self):
        '''ATTACK-VECTOR INVARIANT: if even ONE connected peer has not failed the
        parent, the download is still viable and the head must survive -- a single
        black-hole/slow/malicious peer cannot force the purge.'''
        p2p = _make_p2pnode([('1.1.1.1', 9333), ('2.2.2.2', 9333)])
        FT = 43
        # only the first peer black-holed the parent; the second is untried
        p2p._record_fetch_failure(FT, p2p.peers[0])
        node, tracker, FH, _ = self._scenario(frontier_age=301, desired_pending=True,
                                              p2p_node=p2p)
        self.assertFalse(node._desired_parent_abandoned(FT))
        node.clean_tracker()
        self.assertNotIn(FH, tracker.removed,
            'one non-serving peer must not be able to trigger the purge')


# --------------------------------------------------------------------------
# R3: the stale-tip serve-HOLD and its majority/duration escapes are GONE.
#     No node-local state can declare itself the majority and self-sustain a
#     fork. The consensus-safe serve-side clamp is retained and still contains
#     the emergency-decay flood WITHOUT ever refusing work. REAL WorkerBridge.
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


@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class StaleTipHoldRemoved(unittest.TestCase):
    _GONE = ['_stale_tip_hold_active', '_local_pool_fraction',
             '_chain_attempts_per_second', '_stale_tip_majority_frac',
             '_stale_tip_hold_max']
    _KEPT = ['_tip_is_stale', '_clamp_stale_tip_serve_target',
             '_serve_stale_tip_max_age', '_stale_tip_serve_max_easing']

    def test_majority_and_duration_escape_surface_is_gone(self):
        '''The self-declared-majority / self-sustaining-fork door is removed in
        its entirety: none of the hold or escape symbols exist any more.'''
        for name in self._GONE:
            self.assertFalse(hasattr(WorkerBridge, name),
                'WorkerBridge.%s must be removed (self-desync attack surface)' % name)

    def test_serve_side_flood_containment_is_retained(self):
        '''The consensus-safe pieces the fix keeps -- staleness read + serve-side
        clamp -- are still present, so the flood is contained without a refusal.'''
        for name in self._KEPT:
            self.assertTrue(hasattr(WorkerBridge, name),
                'WorkerBridge.%s must be retained' % name)

    def test_clamp_hardens_served_target_on_stale_tip_only(self):
        '''On a stale tip the served target is capped at 4x the tip's own target
        (only ever HARDENS -> byte-identical verification network-wide); on a
        fresh tip it is a no-op. This is the sole, consensus-safe replacement for
        the removed serve-hold.'''
        prev = type('S', (), {'target': 1000})()
        stale = WorkerBridge.__new__(WorkerBridge)
        stale.node = _HoldNode(time.time() - (WorkerBridge._serve_stale_tip_max_age + 100))
        self.assertTrue(stale._tip_is_stale())
        self.assertEqual(stale._clamp_stale_tip_serve_target(10 ** 9, prev),
                         1000 * WorkerBridge._stale_tip_serve_max_easing)

        fresh = WorkerBridge.__new__(WorkerBridge)
        fresh.node = _HoldNode(time.time())
        self.assertFalse(fresh._tip_is_stale())
        self.assertEqual(fresh._clamp_stale_tip_serve_target(10 ** 9, prev), 10 ** 9)


# --------------------------------------------------------------------------
# C: download_shares failover -- the attack-vector core. No single slow /
#    black-hole / malicious peer can wedge a parent fetch. REAL P2PNode helpers.
# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_DATA, 'p2pool.node unavailable')
class DownloadFailover(unittest.TestCase):
    H = 777

    def test_choose_skips_recently_failed_peer(self):
        p2p = _make_p2pnode([('1.1.1.1', 9333), ('2.2.2.2', 9333)])
        p2p._record_fetch_failure(self.H, p2p.peers[0])
        chosen = p2p._choose_download_peer(self.H, None)
        self.assertEqual(chosen.addr, ('2.2.2.2', 9333),
            'must fail over to the peer that has not black-holed the hash')

    def test_choose_prefers_the_advertiser(self):
        p2p = _make_p2pnode([('1.1.1.1', 9333), ('2.2.2.2', 9333), ('3.3.3.3', 9333)])
        chosen = p2p._choose_download_peer(self.H, ('3.3.3.3', 9333))
        self.assertEqual(chosen.addr, ('3.3.3.3', 9333))

    def test_choose_returns_none_when_all_peers_failed(self):
        p2p = _make_p2pnode([('1.1.1.1', 9333), ('2.2.2.2', 9333)])
        for peer in p2p.peers.values():
            p2p._record_fetch_failure(self.H, peer)
        self.assertIsNone(p2p._choose_download_peer(self.H, None),
            'all peers failed -> caller must back off, not spin on one peer')

    def test_single_blackhole_never_wedges_multi_peer_fetch(self):
        '''The operator invariant: with a black-hole peer AND an honest peer,
        the fetch always routes to the honest peer and the parent is never
        considered abandoned.'''
        p2p = _make_p2pnode([('blackhole', 9333), ('honest', 9333)])
        p2p._record_fetch_failure(self.H, p2p.peers[0])   # black-hole failed
        for _ in xrange(20):
            self.assertEqual(p2p._choose_download_peer(self.H, None).addr,
                             ('honest', 9333))
        self.assertFalse(p2p._parent_abandoned(self.H))

    def test_failure_memory_expires_after_ttl(self):
        p2p = _make_p2pnode([('1.1.1.1', 9333)])
        peer = p2p.peers[0]
        p2p._share_fetch_failures[self.H] = {peer.addr: time.time() - (p2p._fetch_failure_ttl + 1)}
        # stale record -> peer eligible again after prune
        p2p._prune_fetch_failures(time.time())
        self.assertEqual(p2p._choose_download_peer(self.H, None).addr, ('1.1.1.1', 9333))
        self.assertFalse(p2p._parent_abandoned(self.H))

    def test_parent_abandoned_requires_every_peer_to_have_failed(self):
        p2p = _make_p2pnode([('a', 9333), ('b', 9333)])
        p2p._record_fetch_failure(self.H, p2p.peers[0])
        self.assertFalse(p2p._parent_abandoned(self.H))
        p2p._record_fetch_failure(self.H, p2p.peers[1])
        self.assertTrue(p2p._parent_abandoned(self.H))

    def test_no_peers_is_not_abandoned(self):
        p2p = _make_p2pnode([])
        self.assertFalse(p2p._parent_abandoned(self.H))
        self.assertIsNone(p2p._choose_download_peer(self.H, None))


if __name__ == '__main__':
    unittest.main()
