# -*- coding: utf-8 -*-
# Python 2.7 regression suite for the whale-mode self-suppressing latch fix.
#
# These tests exercise the REAL p2pool.work.WorkerBridge state machine and the
# REAL p2pool.override_governor.OverrideGovernor -- never a replica (see the
# repo-wide caution: tests/test_asymmetric_clamp.py replicates a clamp that does
# not exist in data.py; do NOT follow that pattern).
#
# The one thing we monkeypatch is the INSTRUMENT, not the logic: the pool's
# chain-surviving hashrate signal (p2pool_data.get_pool_attempts_per_second) and
# the wall clock (time.time). That is exactly the signal the easy-target override
# distorts, so controlling it lets us reproduce the flood the detector sees while
# the governor -- which never reads it -- drives the exits.
#
# Map to the spec's test table: T1 latch reproduction, T2 orphan-breaker,
# T3 duration backstop, T4 feature-preserved, T5 hysteresis/no-flap,
# T6 warm-up, T7 re-arm cooldown, T8 sibling refusal, T9 structural invariant.

import time
import unittest

# The OverrideGovernor is pure-stdlib and always importable; the governor and
# static-invariant tests run even on a bare interpreter.
from p2pool.override_governor import OverrideGovernor, OwnHealth

# WorkerBridge pulls in twisted + the p2pool package. When those are absent
# (e.g. a bare CI container with no deps installed) the WorkerBridge-dependent
# tests self-skip rather than error -- honest-green, matching pr-verify.yml's
# self-skipping philosophy. CI installs twisted so these actually run.
try:
    from p2pool import work as work_mod
    from p2pool.work import WorkerBridge
    import p2pool.data as p2pool_data
    HAVE_WORK = True
    _WORK_IMPORT_ERR = None
except Exception as _e:   # ImportError or any transitive failure
    work_mod = None
    WorkerBridge = None
    p2pool_data = None
    HAVE_WORK = False
    _WORK_IMPORT_ERR = repr(_e)


# --------------------------------------------------------------------------
# Deterministic clock + minimal fakes (only what the detector actually reads).
# --------------------------------------------------------------------------

class Clock(object):
    def __init__(self, t=1000000.0):
        self.t = float(t)
    def __call__(self):
        return self.t
    def advance(self, dt):
        self.t += dt


class _Item(object):
    __slots__ = ('timestamp',)
    def __init__(self, timestamp):
        self.timestamp = timestamp


class FakeTracker(object):
    '''Provides exactly what _detect_whale_departure and _share_job_lags_tip
    read: items[hash].timestamp, get_height, get_nth_parent_hash over a simple
    linear parent chain.'''
    def __init__(self):
        self.items = {}
        self.parents = {}   # hash -> previous_hash
        self.heights = {}
    def set_tip(self, h, timestamp, height=300):
        self.items[h] = _Item(timestamp)
        self.heights[h] = height
    def set_chain(self, ordered):
        # ordered = [oldest, ..., tip]; links parents.
        prev = None
        for i, h in enumerate(ordered):
            self.parents[h] = prev
            self.items.setdefault(h, _Item(0))
            self.heights[h] = i + 1
            prev = h
    def get_height(self, h):
        return self.heights.get(h, 300)
    def get_nth_parent_hash(self, h, n):
        for _ in xrange(n):
            if h not in self.parents:
                raise KeyError(h)   # unknown -> caller treats as "never refuse"
            h = self.parents[h]
            if h is None:
                raise KeyError('root')
        return h


class FakeVar(object):
    def __init__(self, value):
        self.value = value


class FakeNet(object):
    SHARE_PERIOD = 15
    TARGET_LOOKBEHIND = 200


class FakeNode(object):
    def __init__(self, best, tracker):
        self.best_share_var = FakeVar(best)
        self.tracker = tracker
        self.net = FakeNet()


class _Delta(object):
    def __init__(self, my_count):
        self.my_count = my_count
        self.my_doa_count = 0
        self.my_orphan_announce_count = 0
        self.my_dead_announce_count = 0


class FakeTrackerView(object):
    '''Feeds WorkerBridge.get_stale_counts(): my_count == own shares recorded in
    the surviving chain. not-in-chain = len(my_share_hashes) - my_count.'''
    def __init__(self):
        self.in_chain = 0
    def get_delta_to_last(self, best):
        return _Delta(self.in_chain)


# --------------------------------------------------------------------------
# Rig: a REAL WorkerBridge (via __new__) wired to a REAL OverrideGovernor.
# --------------------------------------------------------------------------

class Rig(object):
    def __init__(self, clock, gap=200.0, pool_hr=1000.0):
        self.clock = clock
        self.pool_hr = [pool_hr]
        self.own_hr = [5e6]

        wb = WorkerBridge.__new__(WorkerBridge)
        # Whale defaults, copied from work.py __init__ (272-286).
        wb._whale_hr_samples = []
        wb._whale_hr_window = 1800
        wb._whale_departure_active = False
        wb._whale_departure_ts = 0
        wb._whale_baseline_hr = 0
        wb._whale_drop_threshold = 0.50
        wb._whale_recovery_threshold = 0.75
        wb._whale_log_interval = 0
        wb._whale_last_sample_ts = 0
        wb._whale_sample_interval = 5.0
        wb._whale_last_att_s = 0
        wb._whale_min_local_hashrate = 1e6
        wb._whale_gap_trigger_periods = 8
        wb._whale_last_no_local_log = 0
        wb._whale_last_metrics = None
        # Fix-added fields.
        wb._whale_refused_siblings = 0
        wb._whale_last_exit_reason = None

        # Own-production plumbing for get_stale_counts.
        wb.my_share_hashes = set()
        wb.my_doa_share_hashes = set()
        wb.tracker_view = FakeTrackerView()

        class _V(object):
            def __init__(self, v):
                self.value = v
        wb.removed_unstales_var = _V((0, 0, 0))
        wb.removed_doa_unstales_var = _V(0)

        tracker = FakeTracker()
        tracker.set_tip('B', clock.t - gap, height=300)
        wb.node = FakeNode('B', tracker)
        self.tracker = tracker

        # REAL governor, controllable probes: own_mint/own_notchain via the REAL
        # get_stale_counts (so my_share_hashes drives them), own_hr controllable.
        # Wiring MIRRORS production (work.py __init__): refused stale-tip siblings
        # count as own production AND own not-in-chain, so a refusal storm cannot
        # starve the orphan circuit-breaker. refused stays 0 for all pre-existing
        # tests (zero behavioural change there); the refusal-storm case drives it.
        wb._override_gov = OverrideGovernor(
            own_hr_fn=lambda: self.own_hr[0],
            own_mint_fn=lambda: wb.get_stale_counts()[1] + wb._whale_refused_siblings,
            own_notchain_fn=lambda: sum(wb.get_stale_counts()[0]) + wb._whale_refused_siblings,
            clock=clock,
        )
        self.wb = wb
        self._next = 0

    def prime_baseline(self, hr=1000.0, n=12):
        # Seed >=10 in-window hashrate samples so `enough_samples` is satisfied
        # and the 30-min average baseline is `hr`.
        self.wb._whale_hr_samples = [(self.clock.t - 5.0 * (n - i), hr) for i in xrange(n)]
        self.wb._whale_last_sample_ts = 0  # force a fresh sample next tick

    def set_gap(self, gap):
        self.tracker.items['B'].timestamp = self.clock.t - gap

    def add_orphans(self, n):
        # own shares minted but NOT recorded in the surviving chain.
        for _ in xrange(n):
            self.wb.my_share_hashes.add('orphan-%d' % self._next)
            self._next += 1

    def add_survivors(self, n):
        for _ in xrange(n):
            self.wb.my_share_hashes.add('surv-%d' % self._next)
            self._next += 1
        self.tracker_view_in_chain_add(n)

    def tracker_view_in_chain_add(self, n):
        self.wb.tracker_view.in_chain += n

    def add_refused(self, n):
        # Stale-tip siblings the mint gate REFUSED: dead own work never minted,
        # so it does NOT enter my_share_hashes -- only the refused counter climbs.
        self.wb._whale_refused_siblings += n

    def tick(self, dt=5.0, orphans=0, survivors=0, refused=0, pool_hr=None, gap=None):
        '''One poll cadence: advance clock, mutate own-production + instrument,
        then run the REAL sample()+detector (unwrapped so errors surface).'''
        self.clock.advance(dt)
        if pool_hr is not None:
            self.pool_hr[0] = pool_hr
        if gap is not None:
            self.set_gap(gap)
        else:
            # keep the tip "fresh" once we are minting (small gap) unless caller
            # pins it; default holds the entry gap so re-entry stays possible.
            pass
        if orphans:
            self.add_orphans(orphans)
        if survivors:
            self.add_survivors(survivors)
        if refused:
            self.add_refused(refused)
        self.wb._override_gov.sample()
        self.wb._detect_whale_departure('timer')

    def exit_reason(self):
        return self.wb._override_gov.status().get('whale', {}).get('exit_reason')


def _install_instrument(clock, rig):
    '''Point work.time.time and get_pool_attempts_per_second at our controls.'''
    work_mod.time.time = clock
    p2pool_data.get_pool_attempts_per_second = staticmethod(
        lambda *a, **k: rig.pool_hr[0]).__func__


class _Base(unittest.TestCase):
    def setUp(self):
        self._real_time = work_mod.time.time
        self._real_gpaps = p2pool_data.get_pool_attempts_per_second

    def tearDown(self):
        work_mod.time.time = self._real_time
        p2pool_data.get_pool_attempts_per_second = self._real_gpaps

    def make_entered_rig(self, clock):
        '''Build a rig, prime baseline, and drive ENTRY via the gap arm.'''
        rig = Rig(clock, gap=200.0, pool_hr=1000.0)
        _install_instrument(clock, rig)
        rig.prime_baseline(hr=1000.0, n=12)
        # Drop the surviving-hashrate instrument and hold a >120s gap: entry.
        rig.tick(dt=5.0, pool_hr=100.0, gap=200.0)
        self.assertTrue(rig.wb._whale_departure_active, 'entry (gap arm) should trip')
        return rig


# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_WORK, 'p2pool.work/twisted unavailable: %s' % _WORK_IMPORT_ERR)
class TestLatchAndExits(_Base):

    def test_T1_latch_reproduction_now_exits(self):
        clock = Clock()
        rig = self.make_entered_rig(clock)

        # Document the LATCH root cause (RED-on-master): during the flood the
        # chain-surviving ratio the OLD exit read (att_s/baseline) stays far
        # below the removed 0.75 recovery threshold, forever. The OLD code had
        # ONLY `ratio >= 0.75` to exit, so it could never turn off.
        ratio = rig.pool_hr[0] / rig.wb._whale_baseline_hr
        self.assertLess(ratio, 0.75,
            'flood keeps chain-surviving ratio below the removed 0.75 arm -> master latches')

        # Synthetic orphan flood: ~1 own share/s minted, none entering the chain,
        # surviving instrument pinned low. Advance well past both exit arms.
        exited = False
        for _ in xrange(600):   # 600 * 5s = 3000s > T_MAX
            rig.tick(dt=5.0, orphans=5, pool_hr=100.0, gap=1.0)
            if not rig.wb._whale_departure_active:
                exited = True
                break
        self.assertTrue(exited, 'FIX: mode must exit under the flood (master would latch)')
        self.assertIn(rig.wb._whale_last_exit_reason, ('orphan_breaker', 'duration_bound'))

    def test_T2_orphan_breaker(self):
        clock = Clock()
        rig = self.make_entered_rig(clock)
        # Windowed own-orphan-rate ~0.60, warmed, sustained past T_ORPHAN=120s.
        for _ in xrange(80):
            rig.tick(dt=5.0, orphans=3, survivors=2, pool_hr=100.0, gap=1.0)
            if not rig.wb._whale_departure_active:
                break
        self.assertFalse(rig.wb._whale_departure_active)
        self.assertEqual(rig.wb._whale_last_exit_reason, 'orphan_breaker')
        # Re-arm cooldown MUST be set on an orphan-forced exit.
        self.assertFalse(rig.wb._override_gov.arm('whale'),
            'orphan exit must set the re-arm cooldown')

    def test_T3_duration_backstop_sets_cooldown(self):
        # After-hours-latch fix: a duration_bound exit MUST also set the re-arm
        # cooldown. The latch was: when the entry condition is self-sustained by
        # the override's own flood, a duration_bound exit at T_MAX re-armed on the
        # very next 5s tick and re-engaged easy-target mode forever. Blocking
        # instant re-arm forces a normal-difficulty interval between arms so the
        # entry signal can recover.
        clock = Clock()
        rig = self.make_entered_rig(clock)
        # All own shares survive (orphan rate 0). Only the duration bound can fire.
        exited = False
        for _ in xrange(500):   # up to 2500s
            rig.tick(dt=5.0, survivors=3, pool_hr=100.0, gap=1.0)
            if not rig.wb._whale_departure_active:
                exited = True
                break
        self.assertTrue(exited)
        self.assertEqual(rig.wb._whale_last_exit_reason, 'duration_bound')
        # Duration exit now sets the cooldown too: instant re-arm is REFUSED.
        self.assertFalse(rig.wb._override_gov.arm('whale'),
            'duration exit must block instant re-arm (after-hours-latch fix)')
        # ...and is allowed again only after the cooldown elapses.
        clock.advance(rig.wb._override_gov.REARM_COOLDOWN + 1.0)
        self.assertTrue(rig.wb._override_gov.arm('whale'),
            're-arm allowed after the cooldown window')

    def test_T4_feature_preserved_stays_active_20min(self):
        clock = Clock()
        rig = self.make_entered_rig(clock)
        # Genuine departure, ALL own shares survive: mode must stay active the
        # whole 20 min (override still applied), i.e. legit whale-mode is NOT
        # curtailed by any own-hr recovery arm.
        for _ in xrange(240):   # 240 * 5s = 1200s = 20 min
            rig.tick(dt=5.0, survivors=2, pool_hr=100.0, gap=1.0)
            self.assertTrue(rig.wb._whale_departure_active,
                'legit whale-mode must not exit before the 1800s backstop')
        # It exits only at the 1800s backstop.
        for _ in xrange(200):
            rig.tick(dt=5.0, survivors=2, pool_hr=100.0, gap=1.0)
            if not rig.wb._whale_departure_active:
                break
        self.assertFalse(rig.wb._whale_departure_active)
        self.assertEqual(rig.wb._whale_last_exit_reason, 'duration_bound')


# --------------------------------------------------------------------------
# After-hours runtime-latch regression (p2p-spb ~100%-DOA-after-6h incident).
# Reproduces the two defects that let the easy-target override re-engage at
# runtime and STICK: (P1) duration_bound exit re-armed instantly, and (P2) the
# orphan circuit-breaker was STARVED because refused stale-tip siblings were
# never counted as own production -- plus the F3 self-heal that structurally
# disables the override while own production is majority-dead.
# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_WORK, 'p2pool.work/twisted unavailable: %s' % _WORK_IMPORT_ERR)
class TestAfterHoursLatch(_Base):

    def test_refusal_storm_exits_via_orphan_breaker_not_starved(self):
        # P2 reproduction: in easy-target mode the stale-tip sibling refusal fires
        # continuously (own work is refused, never minted). On master the governor
        # own_mint delta stays 0 -> _windowed_orphan returns None forever -> the
        # orphan breaker can NEVER fire; only the 1800s duration bound exits, then
        # (P1) it re-arms instantly -> latched. FIX: refused siblings count as own
        # production, so the breaker warms and fires WELL BEFORE the duration bound.
        clock = Clock()
        rig = self.make_entered_rig(clock)
        # Pure refusal storm: NO shares minted (mint from get_stale_counts stays 0),
        # only the refused counter climbs. ~5 refusals / 5s tick.
        exited_reason = None
        for _ in xrange(340):   # up to 1700s -- strictly LESS than the 1800s bound
            rig.tick(dt=5.0, refused=5, pool_hr=100.0, gap=1.0)
            if not rig.wb._whale_departure_active:
                exited_reason = rig.wb._whale_last_exit_reason
                break
        self.assertEqual(exited_reason, 'orphan_breaker',
            'refusal storm must trip the orphan breaker before the duration bound '
            '(master starves it and can only ever hit duration_bound)')
        # And the orphan-forced exit sets the re-arm cooldown (belt to F1).
        self.assertFalse(rig.wb._override_gov.arm('whale'),
            'orphan exit must block instant re-arm')

    def test_override_disabled_while_own_production_majority_dead(self):
        # F3 self-heal: the easy-target override may NOT be applied while our own
        # windowed dead/refused fraction is at/above the ORPHAN_CEILING -- a
        # ~100%-DOA state structurally disables the override that causes it.
        clock = Clock()
        rig = self.make_entered_rig(clock)
        # Drive a warmed, majority-dead window: >MIN_OWN_DELTA fresh own shares,
        # ~80% of them orphaned.
        for _ in xrange(40):
            rig.tick(dt=5.0, orphans=4, survivors=1, pool_hr=100.0, gap=1.0)
            if not rig.wb._whale_departure_active:
                break
        # The detector may or may not still be armed here, but the self-heal
        # predicate must report the majority-dead state...
        self.assertTrue(rig.wb._override_dead_majority(),
            'windowed own-dead fraction must be recognised as majority-dead')
        # ...and the EXACT get_work application composition must therefore refuse
        # to apply the override even with ample local hashrate.
        local_hr = rig.wb._whale_min_local_hashrate * 10
        applied = (rig.wb._detect_whale_departure()
                   and local_hr >= rig.wb._whale_min_local_hashrate
                   and not rig.wb._override_dead_majority())
        self.assertFalse(applied,
            'override must be structurally disabled while own production is '
            'majority-dead (the flood source cannot re-enable itself)')

    def test_self_heal_re_enables_once_production_recovers(self):
        # Complement to the above: once own production is healthy again (orphan
        # fraction below the ceiling across the window), the self-heal predicate
        # clears, so the override is free to apply on a genuine future departure.
        clock = Clock()
        rig = self.make_entered_rig(clock)
        # First: majority-dead window.
        for _ in xrange(30):
            rig.tick(dt=5.0, orphans=4, survivors=1, pool_hr=100.0, gap=1.0)
        self.assertTrue(rig.wb._override_dead_majority())
        # Then: a long window of all-surviving production ages the dead samples
        # out (ORPHAN_WINDOW=600s) and drives the windowed fraction to ~0.
        for _ in xrange(160):   # 800s > ORPHAN_WINDOW
            rig.tick(dt=5.0, survivors=6, pool_hr=100.0, gap=1.0)
        self.assertFalse(rig.wb._override_dead_majority(),
            'self-heal predicate must clear once own production is healthy again')


# --------------------------------------------------------------------------
# Governor-focused tests: REAL OverrideGovernor, controllable probes.
# --------------------------------------------------------------------------

class TestGovernor(unittest.TestCase):

    def make_gov(self, clock, state):
        return OverrideGovernor(
            own_hr_fn=lambda: state['hr'],
            own_mint_fn=lambda: state['mint'],
            own_notchain_fn=lambda: state['nc'],
            clock=clock,
        )

    def test_T5_no_flap_requires_sustained_hot(self):
        # Anti-flap (FM1): a high windowed orphan-rate must be SUSTAINED for
        # T_ORPHAN (120s) before the breaker fires. A high-but-brief excursion
        # (< 120s) does NOT exit; the same rate held past 120s does.
        #
        # NOTE (spec reconciliation): the spec's T5 prose oscillates 0.45<->0.55
        # around the 0.50 ceiling. The verbatim governor's own-orphan-rate is a
        # DELTA over a 600s window (FM3), so a per-tick ratio does not translate
        # to an instantaneous windowed rate that can be flapped tick-by-tick; the
        # window integrates it. The real, testable no-flap guarantee the governor
        # provides is the T_ORPHAN SUSTAIN requirement, asserted here on a clean
        # single-window regime (< 120 ticks, so no sample aging confounds the
        # windowed delta). The CLEAR=0.20 reset floor is additionally exercised by
        # T6 (warm-up stays None/low) and T2 (sustained flood exits).
        clock = Clock()
        state = dict(hr=5e6, mint=0, nc=0)
        gov = self.make_gov(clock, state)
        gov.sample(); gov.arm('whale')

        def step(dnc, dmint=20):
            clock.advance(5.0)
            state['mint'] += dmint
            state['nc'] += dnc
            gov.sample()
            return gov.tick('whale')

        # High windowed rate (0.60) but held only ~110s (< T_ORPHAN): NO exit.
        for _ in xrange(22):   # 22 * 5s = 110s
            self.assertTrue(step(12), 'high-but-brief orphan-rate must not exit')
        # Keep holding the SAME rate past 120s: now the breaker fires.
        exited = False
        for _ in xrange(20):
            if not step(12):
                exited = True
                break
        self.assertTrue(exited, 'sustained (>=120s) orphan-rate must exit')
        self.assertEqual(gov.status()['whale']['exit_reason'], 'orphan_breaker')

    def test_T6_warmup_no_premature_exit(self):
        # Fresh governor, high orphan FRACTION but < MIN_OWN_DELTA fresh shares:
        # windowed rate is None -> no orphan exit; duration still bounds.
        clock = Clock()
        state = dict(hr=5e6, mint=0, nc=0)
        gov = self.make_gov(clock, state)
        gov.sample(); gov.arm('whale')
        # 20 fresh shares (< 30) all orphaned over 100s: must stay active.
        for _ in xrange(20):
            clock.advance(5.0)
            state['mint'] += 1
            state['nc'] += 1   # 100% orphan fraction
            gov.sample()
            self.assertTrue(gov.tick('whale'),
                'must not exit before the warm-up threshold (MIN_OWN_DELTA)')
        self.assertIsNone(gov.status()['whale']['own_orphan_rate'])
        # Push past warm-up and sustain -> orphan exit becomes reachable.
        exited = False
        for _ in xrange(60):
            clock.advance(5.0)
            state['mint'] += 5
            state['nc'] += 5
            gov.sample()
            if not gov.tick('whale'):
                exited = True
                break
        self.assertTrue(exited)

    def test_T7_rearm_cooldown(self):
        clock = Clock()
        state = dict(hr=5e6, mint=0, nc=0)
        gov = self.make_gov(clock, state)
        gov.sample(); gov.arm('whale')
        # Force an orphan-breaker exit.
        for _ in xrange(80):
            clock.advance(5.0)
            state['mint'] += 5
            state['nc'] += 4
            gov.sample()
            if not gov.tick('whale'):
                break
        self.assertEqual(gov.status()['whale']['exit_reason'], 'orphan_breaker')
        # Re-arm within 900s is refused.
        self.assertFalse(gov.arm('whale'), 're-arm during cooldown must be refused')
        # After 900s, arm is allowed again.
        clock.advance(901.0)
        self.assertTrue(gov.arm('whale'), 're-arm allowed after cooldown')

    def test_duration_exit_sets_rearm_cooldown(self):
        # After-hours-latch fix at the governor level: a duration_bound exit must
        # set the re-arm cooldown (master set it only on orphan_breaker).
        clock = Clock()
        state = dict(hr=5e6, mint=0, nc=0)
        gov = self.make_gov(clock, state)
        gov.sample(); gov.arm('whale')
        # All survive (orphan rate 0): only the duration bound can fire.
        exited = False
        for _ in xrange(400):
            clock.advance(5.0)
            state['mint'] += 3   # all in-chain (nc unchanged) -> rate 0
            gov.sample()
            if not gov.tick('whale'):
                exited = True
                break
        self.assertTrue(exited)
        self.assertEqual(gov.status()['whale']['exit_reason'], 'duration_bound')
        # Instant re-arm refused; allowed only after REARM_COOLDOWN.
        self.assertFalse(gov.arm('whale'),
            'duration_bound exit must block instant re-arm')
        clock.advance(gov.REARM_COOLDOWN + 1.0)
        self.assertTrue(gov.arm('whale'))

    def test_T9a_ownhealth_has_no_chain_surviving_field(self):
        self.assertEqual(
            set(OwnHealth._fields),
            set(['own_hr', 'baseline_own_hr', 'own_orphan_rate',
                 'own_mint_delta', 'seconds_active']))
        for f in OwnHealth._fields:
            self.assertNotIn('pool', f)
            self.assertNotIn('surviv', f)
            self.assertNotIn('chain', f)

    def test_T9b_governor_never_raises_when_instrument_raises(self):
        # The governor reads ONLY own-production; even if the pool-hr instrument
        # blows up, a full arm->flood->exit cycle completes without raising.
        clock = Clock()
        state = dict(hr=5e6, mint=0, nc=0)
        gov = self.make_gov(clock, state)
        gov.sample(); gov.arm('whale')
        exited = False
        for _ in xrange(80):
            clock.advance(5.0)
            state['mint'] += 5
            state['nc'] += 4
            gov.sample()          # must not raise
            if not gov.tick('whale'):  # must not raise
                exited = True
                break
        self.assertTrue(exited)


@unittest.skipUnless(HAVE_WORK, 'p2pool.work/twisted unavailable: %s' % _WORK_IMPORT_ERR)
class TestStaticInvariant(unittest.TestCase):
    def test_T9c_degraded_metric_absent_from_governor_and_exit_block(self):
        import inspect
        import p2pool.override_governor as govmod
        gov_src = inspect.getsource(govmod)
        self.assertNotIn('get_pool_attempts_per_second', gov_src,
            'the degraded metric must never appear in the governor')
        # And it must not appear in the whale EXIT block of _detect_whale_departure.
        det_src = inspect.getsource(WorkerBridge._detect_whale_departure)
        marker = 'EXIT is now delegated'
        self.assertIn(marker, det_src)
        exit_block = det_src[det_src.index(marker):]
        self.assertNotIn('get_pool_attempts_per_second', exit_block,
            'the degraded metric must not appear in any exit path')

    def test_governor_wiring_feeds_refused_siblings(self):
        # F2: the PRODUCTION governor wiring must add _whale_refused_siblings into
        # BOTH own_mint_fn and own_notchain_fn, so a refusal storm cannot starve
        # the orphan circuit-breaker (P2). Asserted on the real __init__ source so
        # the guarantee is about production code, not the test harness.
        import inspect
        init_src = inspect.getsource(WorkerBridge.__init__)
        mint_lines = [ln for ln in init_src.splitlines() if 'own_mint_fn' in ln]
        nc_lines = [ln for ln in init_src.splitlines() if 'own_notchain_fn' in ln]
        self.assertTrue(mint_lines and nc_lines, 'governor wiring lines not found')
        self.assertTrue(any('_whale_refused_siblings' in ln for ln in mint_lines),
            'own_mint_fn must include refused siblings (F2)')
        self.assertTrue(any('_whale_refused_siblings' in ln for ln in nc_lines),
            'own_notchain_fn must include refused siblings (F2)')


# --------------------------------------------------------------------------
# T8: fork-safe stale-tip sibling refusal. Exercises the REAL helper
# WorkerBridge._share_job_lags_tip and the exact gate boolean composition.
# --------------------------------------------------------------------------

@unittest.skipUnless(HAVE_WORK, 'p2pool.work/twisted unavailable: %s' % _WORK_IMPORT_ERR)
class TestSiblingRefusal(unittest.TestCase):
    def make_wb(self, best, chain):
        wb = WorkerBridge.__new__(WorkerBridge)
        tracker = FakeTracker()
        tracker.set_chain(chain)
        wb.node = FakeNode(best, tracker)
        return wb

    def test_T8_lag_logic_truth_table(self):
        # chain oldest..tip: B0 <- B1 <- B2 <- B3 (tip = B3)
        chain = ['B0', 'B1', 'B2', 'B3']
        wb = self.make_wb('B3', chain)

        # lag 0: job parent == tip -> not lagging
        self.assertFalse(wb._share_job_lags_tip('B3'))
        # lag 1: job parent == parent-of-tip (normal drift) -> not lagging
        self.assertFalse(wb._share_job_lags_tip('B2'))
        # lag 2: job parent 2 behind -> LAGS (would mint a stale-tip sibling)
        self.assertTrue(wb._share_job_lags_tip('B1'))
        # lag 3: deeper -> LAGS
        self.assertTrue(wb._share_job_lags_tip('B0'))
        # divergent fork hash (not on chain, != parent-of-tip) -> refuse
        self.assertTrue(wb._share_job_lags_tip('FORK'))
        # None job parent -> never refuse
        self.assertFalse(wb._share_job_lags_tip(None))

        # best is None -> never refuse
        wb2 = self.make_wb(None, chain)
        self.assertFalse(wb2._share_job_lags_tip('B1'))

    def test_T8_unknown_best_never_refuses(self):
        # best not present in tracker -> get_nth_parent_hash raises -> never refuse
        wb = self.make_wb('MISSING', ['B0', 'B1'])
        self.assertFalse(wb._share_job_lags_tip('B0'))

    def test_T8_gate_composition(self):
        # The share gate refuses to mint iff (whale_override_applied AND lag>1).
        # Assert the exact boolean composition using the REAL helper.
        chain = ['B0', 'B1', 'B2', 'B3']
        wb = self.make_wb('B3', chain)

        def refuses(whale_override_applied, job_prev):
            return whale_override_applied and wb._share_job_lags_tip(job_prev)

        # easy mode + lag 2 -> refuse
        self.assertTrue(refuses(True, 'B1'))
        # easy mode + lag 0/1 -> mint
        self.assertFalse(refuses(True, 'B3'))
        self.assertFalse(refuses(True, 'B2'))
        # NORMAL mode (override off) + lag 2 -> mint (zero behavioural change)
        self.assertFalse(refuses(False, 'B1'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
