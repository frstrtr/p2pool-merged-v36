# -*- coding: utf-8 -*-
# Python 2.7 regression suite for the restart stale-head DOA fix:
# the canonical PERSIST serve-gate restored in p2pool/work.py.
#
# Background (kr1z1s restart-DOA): on restart the node loads the persisted OLD
# sharechain head before the p2p node has any peers and before any share has
# been downloaded. This fork had DELETED the two upstream refuse-work guards
# ("Removed peer connection check - allow solo mining") while keeping
# networks/litecoin.py PERSIST=True, so reconnecting rigs were handed work built
# on the stale head; those shares orphan once think() adopts the real network
# head -> the restart DOA spike and transient isolation.
#
# The fix restores jtoomim/p2pool's two guard sites behavior-verbatim, keyed on
# net.PERSIST: preprocess_request refuses when peerless ("p2pool is not connected
# to any peers"); get_work refuses when peerless OR when the tracker has no best
# share yet ("p2pool is downloading shares"). A default-OFF --solo/--bootstrap
# flag (allow_peerless_mining) is the dynamic equivalent of PERSIST=False and
# bypasses ONLY these two sites.
#
# These tests exercise the REAL p2pool.work.WorkerBridge methods via __new__ --
# never a replica of the guard logic (see the repo caution against replicating
# logic that lives in the source, e.g. tests/test_asymmetric_clamp.py). The only
# things faked are the node's data surface the guards read (net.PERSIST,
# p2p_node.peers, best_share_var.value) and, for the pass-through cases, a
# sentinel planted just past the guard so we can prove control reached it.

import time
import unittest

# WorkerBridge pulls in twisted + the p2pool package. On a bare interpreter with
# no deps installed those imports fail; in that case the WorkerBridge-dependent
# tests self-skip rather than error -- honest-green, matching the repo's
# self-skipping test philosophy (see test_whale_latch.py). CI installs twisted so
# these actually run.
try:
    from p2pool.work import WorkerBridge
    from p2pool.util import jsonrpc
    HAVE_WORK = True
    _WORK_IMPORT_ERR = None
except Exception as _e:  # ImportError or any transitive failure
    WorkerBridge = None
    jsonrpc = None
    HAVE_WORK = False
    _WORK_IMPORT_ERR = repr(_e)


# --------------------------------------------------------------------------
# Minimal fakes: exactly the surface the two guards read.
# --------------------------------------------------------------------------

class FakeVar(object):
    def __init__(self, value):
        self.value = value


class FakeNet(object):
    def __init__(self, persist):
        self.PERSIST = persist


class FakeP2PNode(object):
    def __init__(self, npeers):
        # WorkerBridge only reads len(self.node.p2p_node.peers).
        self.peers = dict((i, object()) for i in range(npeers))


class _Item(object):
    __slots__ = ('timestamp',)
    def __init__(self, timestamp):
        self.timestamp = timestamp


class FakeTracker(object):
    '''Only surface the stale-tip guard reads: items[best].timestamp.'''
    def __init__(self, items=None):
        self.items = items or {}


class FakeNode(object):
    def __init__(self, persist=True, npeers=0, best_share=None, tip_timestamp=None):
        self.net = FakeNet(persist)
        # p2p_node is None until the p2p server starts -- the guard treats that
        # as peerless, same as an empty peers dict.
        self.p2p_node = None if npeers is None else FakeP2PNode(npeers)
        self.best_share_var = FakeVar(best_share)
        # Tracker with a dated tip only when the caller wants the stale-tip guard
        # exercised; otherwise an empty tracker (guard reads -> miss -> not stale).
        items = {}
        if best_share is not None and tip_timestamp is not None:
            items[best_share] = _Item(tip_timestamp)
        self.tracker = FakeTracker(items)


class _Sentinel(Exception):
    '''Raised from a hook planted immediately AFTER a guard, so a test can prove
    execution passed the guard (rather than the guard silently letting it fall
    through into unstubbed machinery).'''
    pass


def make_bridge(persist=True, npeers=0, best_share=None, allow_peerless=False,
                tip_timestamp=None):
    wb = WorkerBridge.__new__(WorkerBridge)
    wb.node = FakeNode(persist=persist, npeers=npeers, best_share=best_share,
                       tip_timestamp=tip_timestamp)
    wb.allow_peerless_mining = allow_peerless
    return wb


# --------------------------------------------------------------------------
# Helper-method truth table (pure, always runnable when work.py imports).
# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class ServeGateHelpers(unittest.TestCase):
    def test_peerless_when_p2p_node_none(self):
        wb = make_bridge(npeers=None)
        self.assertTrue(wb._peerless())

    def test_peerless_when_zero_peers(self):
        wb = make_bridge(npeers=0)
        self.assertTrue(wb._peerless())

    def test_not_peerless_with_a_peer(self):
        wb = make_bridge(npeers=1)
        self.assertFalse(wb._peerless())

    def test_gate_active_on_persist_default(self):
        wb = make_bridge(persist=True, allow_peerless=False)
        self.assertTrue(wb._persist_serve_gate_active())

    def test_gate_inactive_when_persist_false(self):
        # Upstream static switch: PERSIST=False for solo / new-chain mining.
        wb = make_bridge(persist=False, allow_peerless=False)
        self.assertFalse(wb._persist_serve_gate_active())

    def test_gate_inactive_when_solo_flag(self):
        # Dynamic equivalent: --solo/--bootstrap overrides PERSIST at the gate.
        wb = make_bridge(persist=True, allow_peerless=True)
        self.assertFalse(wb._persist_serve_gate_active())


# --------------------------------------------------------------------------
# preprocess_request: real method, peerless refuse-gate.
# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class PreprocessRequestGate(unittest.TestCase):
    def _plant_post_guard_sentinel(self, wb):
        # The first thing after the peerless guard in preprocess_request is the
        # coind-liveness check reading current_work.value['last_update']; make it
        # pass, then have get_user_details raise our sentinel so reaching it
        # proves the peerless guard let the request through.
        wb.current_work = FakeVar({'last_update': time.time()})

        def _boom(*a, **k):
            raise _Sentinel()
        wb.get_user_details = _boom

    def test_refuses_when_peerless_on_persist(self):
        wb = make_bridge(persist=True, npeers=0)
        try:
            wb.preprocess_request('user')
            self.fail('expected refusal, none raised')
        except _Sentinel:
            self.fail('guard did not fire: reached post-guard code while peerless')
        except jsonrpc.Error as e:
            self.assertEqual(e.code, -12345)
            self.assertIn(u'not connected to any peers', e.message)

    def test_peerless_guard_precedes_coind_liveness(self):
        # Independence regression: with BOTH a peerless node AND a stale coind,
        # the peerless error must win -- matching upstream guard order and
        # ensuring the new guard never masks the pre-existing coind guard's slot.
        wb = make_bridge(persist=True, npeers=0)
        wb.current_work = FakeVar({'last_update': time.time() - 3600})  # stale
        try:
            wb.preprocess_request('user')
            self.fail('expected refusal, none raised')
        except jsonrpc.Error as e:
            self.assertIn(u'not connected to any peers', e.message)

    def test_coind_liveness_guard_still_fires_when_peers_present(self):
        # The pre-existing 'lost contact with coind' guard must be unaffected:
        # with a peer present the peerless guard is inert, and a stale coind
        # still raises its own error.
        wb = make_bridge(persist=True, npeers=1)
        wb.current_work = FakeVar({'last_update': time.time() - 3600})  # stale
        try:
            wb.preprocess_request('user')
            self.fail('expected refusal, none raised')
        except jsonrpc.Error as e:
            self.assertIn(u'lost contact with coind', e.message)

    def test_passes_guard_when_peer_present(self):
        wb = make_bridge(persist=True, npeers=1)
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, wb.preprocess_request, 'user')

    def test_solo_flag_bypasses_peerless_guard(self):
        wb = make_bridge(persist=True, npeers=0, allow_peerless=True)
        self._plant_post_guard_sentinel(wb)
        # Peerless but solo -> guard inert -> reaches post-guard sentinel.
        self.assertRaises(_Sentinel, wb.preprocess_request, 'user')

    def test_persist_false_bypasses_peerless_guard(self):
        wb = make_bridge(persist=False, npeers=0)
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, wb.preprocess_request, 'user')


# --------------------------------------------------------------------------
# get_work: real method, peerless + downloading-shares refuse-gate.
# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class GetWorkGate(unittest.TestCase):
    def _plant_post_guard_sentinel(self, wb):
        # The first call after the get_work guards is _build_user_specific_merged_work;
        # planting the sentinel there proves both guards let the request through.
        def _boom(*a, **k):
            raise _Sentinel()
        wb._build_user_specific_merged_work = _boom

    def _call(self, wb):
        return wb.get_work('user', 'pkh', 0, 1, 1)

    def test_refuses_when_peerless(self):
        wb = make_bridge(persist=True, npeers=0, best_share='HEAD')
        try:
            self._call(wb)
            self.fail('expected refusal')
        except _Sentinel:
            self.fail('guard did not fire while peerless')
        except jsonrpc.Error as e:
            self.assertEqual(e.code, -12345)
            self.assertIn(u'not connected to any peers', e.message)

    def test_refuses_when_downloading_shares(self):
        # Peer connected but tracker still empty -> downloading-shares refusal.
        wb = make_bridge(persist=True, npeers=1, best_share=None)
        try:
            self._call(wb)
            self.fail('expected refusal')
        except _Sentinel:
            self.fail('guard did not fire with empty tracker')
        except jsonrpc.Error as e:
            self.assertEqual(e.code, -12345)
            self.assertIn(u'downloading shares', e.message)

    def test_serves_when_peer_and_head_present(self):
        wb = make_bridge(persist=True, npeers=1, best_share='HEAD')
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, self._call, wb)

    def test_solo_flag_serves_peerless_empty_tracker(self):
        # Bootstrapping a brand-new chain: peerless + empty tracker, but --solo
        # set -> both guards inert -> reaches template build.
        wb = make_bridge(persist=True, npeers=0, best_share=None, allow_peerless=True)
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, self._call, wb)

    def test_persist_false_serves_peerless_empty_tracker(self):
        wb = make_bridge(persist=False, npeers=0, best_share=None)
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, self._call, wb)


# --------------------------------------------------------------------------
# Stale-tip serve-gate backstop (after-hours-latch fix): with peers present but
# the served sharechain tip dead >600s, refuse work so the emergency time-decay
# cannot compute drastically-too-easy work off a dead tip (the diff-corruption
# flood). Node-local serving policy; self-clears when the tip advances.
# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class StaleTipHelper(unittest.TestCase):
    def test_fresh_tip_not_stale(self):
        wb = make_bridge(npeers=1, best_share='HEAD', tip_timestamp=time.time() - 30)
        self.assertFalse(wb._tip_is_stale())

    def test_old_tip_is_stale(self):
        wb = make_bridge(npeers=1, best_share='HEAD', tip_timestamp=time.time() - 1200)
        self.assertTrue(wb._tip_is_stale())

    def test_none_best_share_not_stale(self):
        wb = make_bridge(npeers=1, best_share=None)
        self.assertFalse(wb._tip_is_stale())

    def test_missing_tracker_item_not_stale(self):
        # best set but tracker has no dated item -> read miss -> never a false refusal.
        wb = make_bridge(npeers=1, best_share='HEAD', tip_timestamp=None)
        self.assertFalse(wb._tip_is_stale())

    def test_boundary_at_max_age(self):
        wb = make_bridge(npeers=1, best_share='HEAD',
                         tip_timestamp=time.time() - (wb_max_age() + 5))
        self.assertTrue(wb._tip_is_stale())


def wb_max_age():
    return WorkerBridge._serve_stale_tip_max_age


@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class GetWorkStaleTipGate(unittest.TestCase):
    def _plant_post_guard_sentinel(self, wb):
        def _boom(*a, **k):
            raise _Sentinel()
        wb._build_user_specific_merged_work = _boom

    def _call(self, wb):
        return wb.get_work('user', 'pkh', 0, 1, 1)

    def test_refuses_when_tip_stale(self):
        # Peer connected, head present, but tip dead >600s -> stale-tip refusal.
        wb = make_bridge(persist=True, npeers=1, best_share='HEAD',
                         tip_timestamp=time.time() - 1200)
        self._plant_post_guard_sentinel(wb)
        try:
            self._call(wb)
            self.fail('expected stale-tip refusal')
        except _Sentinel:
            self.fail('guard did not fire on a stale tip')
        except jsonrpc.Error as e:
            self.assertEqual(e.code, -12345)
            self.assertIn(u'tip is stale', e.message)

    def test_serves_when_tip_fresh(self):
        # Same node, fresh tip -> guard inert, reaches template build.
        wb = make_bridge(persist=True, npeers=1, best_share='HEAD',
                         tip_timestamp=time.time() - 30)
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, self._call, wb)

    def test_self_clears_when_tip_advances(self):
        # A node that was refusing on a stale tip must serve again the instant the
        # tip advances -- proving the refusal is not a latch.
        wb = make_bridge(persist=True, npeers=1, best_share='HEAD',
                         tip_timestamp=time.time() - 1200)
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(jsonrpc.Error, self._call, wb)
        # Tip advances (fresh share): same bridge now passes the gate.
        wb.node.tracker.items['HEAD'].timestamp = time.time()
        self.assertRaises(_Sentinel, self._call, wb)

    def test_solo_flag_bypasses_stale_tip_gate(self):
        wb = make_bridge(persist=True, npeers=1, best_share='HEAD',
                         tip_timestamp=time.time() - 1200, allow_peerless=True)
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, self._call, wb)

    def test_persist_false_bypasses_stale_tip_gate(self):
        wb = make_bridge(persist=False, npeers=1, best_share='HEAD',
                         tip_timestamp=time.time() - 1200)
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, self._call, wb)


# --------------------------------------------------------------------------
# v36-0.21: the stale-tip serve-hold is now BOUNDED. v36-0.20 refused ALL work
# on a stale tip; on a majority-hashrate node that deadlocks (refusing the only
# hashrate that can advance the tip), which is exactly what starved kr1z1s's
# miners on 2026-08-30. The hold must ESCAPE for a majority node, or after two
# full windows for a minority node, and the flood must instead be capped on the
# serve side (a consensus-safe hardening) rather than by refusing.
# --------------------------------------------------------------------------

class _FakeShare(object):
    __slots__ = ('target',)
    def __init__(self, target):
        self.target = target


@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class BoundedStaleTipHoldHelper(unittest.TestCase):
    '''_stale_tip_hold_active truth table: refuse only while stale AND minority
    AND within the hold window; escape on majority or on the duration ceiling.'''

    def _stale_bridge(self):
        return make_bridge(npeers=1, best_share='HEAD', tip_timestamp=time.time() - 1200)

    def test_minority_within_window_refuses(self):
        wb = self._stale_bridge()
        wb._local_pool_fraction = lambda: 0.10   # small miner
        wb._stale_tip_hold_since = None          # hold just starting
        self.assertTrue(wb._stale_tip_hold_active())

    def test_majority_escapes_immediately(self):
        # kr1z1s was 96.9-99.0% of the pool -- refusing him is the deadlock.
        wb = self._stale_bridge()
        wb._local_pool_fraction = lambda: 0.90
        wb._stale_tip_hold_since = None
        self.assertFalse(wb._stale_tip_hold_active())

    def test_exactly_majority_frac_escapes(self):
        wb = self._stale_bridge()
        wb._local_pool_fraction = lambda: WorkerBridge._stale_tip_majority_frac
        wb._stale_tip_hold_since = None
        self.assertFalse(wb._stale_tip_hold_active())

    def test_minority_escapes_after_duration_ceiling(self):
        wb = self._stale_bridge()
        wb._local_pool_fraction = lambda: 0.0
        wb._stale_tip_hold_since = time.time() - (WorkerBridge._stale_tip_hold_max + 60)
        self.assertFalse(wb._stale_tip_hold_active())

    def test_fresh_tip_never_holds_and_resets_timer(self):
        wb = make_bridge(npeers=1, best_share='HEAD', tip_timestamp=time.time() - 30)
        wb._local_pool_fraction = lambda: 0.0
        wb._stale_tip_hold_since = time.time() - 99999  # stale timer left armed
        self.assertFalse(wb._stale_tip_hold_active())
        self.assertIsNone(wb._stale_tip_hold_since)     # cleared on a fresh tip

    def test_hold_since_is_stamped_on_first_stale_call(self):
        wb = self._stale_bridge()
        wb._local_pool_fraction = lambda: 0.0
        wb._stale_tip_hold_since = None
        wb._stale_tip_hold_active()
        self.assertIsNotNone(wb._stale_tip_hold_since)


@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class GetWorkBoundedStaleTipGate(unittest.TestCase):
    def _plant_post_guard_sentinel(self, wb):
        def _boom(*a, **k):
            raise _Sentinel()
        wb._build_user_specific_merged_work = _boom

    def _call(self, wb):
        return wb.get_work('user', 'pkh', 0, 1, 1)

    def test_majority_node_serves_despite_stale_tip(self):
        # The regression under test: v36-0.20 refused here forever; v36-0.21 serves.
        wb = make_bridge(persist=True, npeers=1, best_share='HEAD',
                         tip_timestamp=time.time() - 1200)
        wb._local_pool_fraction = lambda: 0.97
        wb._stale_tip_hold_since = None
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, self._call, wb)   # reached template build => served

    def test_minority_node_still_refuses_stale_tip(self):
        wb = make_bridge(persist=True, npeers=1, best_share='HEAD',
                         tip_timestamp=time.time() - 1200)
        wb._local_pool_fraction = lambda: 0.05
        wb._stale_tip_hold_since = None
        self._plant_post_guard_sentinel(wb)
        try:
            self._call(wb)
            self.fail('expected stale-tip refusal for a minority node')
        except _Sentinel:
            self.fail('minority node served a stale tip (should refuse)')
        except jsonrpc.Error as e:
            self.assertIn(u'tip is stale', e.message)

    def test_duration_ceiling_lets_minority_node_resume(self):
        wb = make_bridge(persist=True, npeers=1, best_share='HEAD',
                         tip_timestamp=time.time() - 1200)
        wb._local_pool_fraction = lambda: 0.05
        wb._stale_tip_hold_since = time.time() - (WorkerBridge._stale_tip_hold_max + 60)
        self._plant_post_guard_sentinel(wb)
        self.assertRaises(_Sentinel, self._call, wb)


@unittest.skipUnless(HAVE_WORK, 'p2pool.work unavailable: %s' % (_WORK_IMPORT_ERR,))
class StaleTipServeClamp(unittest.TestCase):
    '''The serve-side easing cap: under a stale tip the served target is capped
    at _stale_tip_serve_max_easing x the tip's own target, and only ever hardens
    (never eases) the share -- so it is consensus-safe.'''

    def test_clamp_hardens_easy_target_under_stale_tip(self):
        wb = make_bridge(npeers=1, best_share='HEAD', tip_timestamp=time.time() - 1200)
        tip = _FakeShare(target=1000)
        out = wb._clamp_stale_tip_serve_target(2**256 - 1, tip)
        self.assertEqual(out, 1000 * WorkerBridge._stale_tip_serve_max_easing)

    def test_clamp_keeps_a_harder_request(self):
        wb = make_bridge(npeers=1, best_share='HEAD', tip_timestamp=time.time() - 1200)
        tip = _FakeShare(target=1000)
        out = wb._clamp_stale_tip_serve_target(500, tip)
        self.assertEqual(out, 500)

    def test_clamp_is_noop_on_fresh_tip(self):
        wb = make_bridge(npeers=1, best_share='HEAD', tip_timestamp=time.time() - 30)
        tip = _FakeShare(target=1000)
        out = wb._clamp_stale_tip_serve_target(2**256 - 1, tip)
        self.assertEqual(out, 2**256 - 1)

    def test_clamp_is_noop_without_previous_share(self):
        wb = make_bridge(npeers=1, best_share='HEAD', tip_timestamp=time.time() - 1200)
        out = wb._clamp_stale_tip_serve_target(2**256 - 1, None)
        self.assertEqual(out, 2**256 - 1)


if __name__ == '__main__':
    unittest.main()
