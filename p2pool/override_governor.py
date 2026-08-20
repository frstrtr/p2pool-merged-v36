# Python 2.7. Node-local, zero consensus surface.
#
# OverrideGovernor: a generalized latch-breaker for auto-difficulty overrides.
#
# An auto-difficulty override (such as whale-departure recovery) distorts the
# chain-surviving-hashrate signal it would otherwise use to decide when to turn
# itself off -- so any exit that reads that signal can latch (the override's own
# effect suppresses its own recovery signal). This governor structurally prevents
# that class of defect: its exits read ONLY own-production counters (shares this
# node minted, including orphaned ones) and wall-clock -- quantities an easy-target
# override CANNOT itself degrade.
import time
from collections import namedtuple, deque

# The ONLY health surface an override's optional recovery hook may read.
# Every field derives from OWN production (shares this node minted, INCLUDING
# orphaned ones) or wall-clock -- quantities an easy-target override CANNOT
# itself degrade. There is DELIBERATELY no chain-surviving-hashrate field:
# self-suppression is not a bug you can write, it is a value you cannot name.
OwnHealth = namedtuple('OwnHealth',
    'own_hr baseline_own_hr own_orphan_rate own_mint_delta seconds_active')


class OverrideGovernor(object):
    T_MAX          = 1800.0   # ARM A: hard duration backstop (s)
    ORPHAN_CEILING = 0.50     # ARM B: own not-in-chain fraction deemed net-harmful
    ORPHAN_CLEAR   = 0.20     # ARM B: hysteresis reset floor
    T_ORPHAN       = 120.0    # ARM B: sustained-hot seconds before disarm (8*SHARE_PERIOD)
    ORPHAN_WINDOW  = 600.0    # rolling window for own-orphan-rate deltas
    MIN_OWN_DELTA  = 30       # warm-up: fresh own shares in window before ARM B may act
    REARM_COOLDOWN = 900.0    # suppress re-arm this long after an orphan-forced exit
    OWN_HR_WINDOW  = 1800.0   # rolling own-hr baseline window (observability / future use)
    SAMPLE_TICK    = 5.0

    def __init__(self, own_hr_fn, own_mint_fn, own_notchain_fn, clock=time.time):
        self._own_hr       = own_hr_fn        # -> float H/s incl DEAD (get_local_rates both dicts)
        self._own_mint     = own_mint_fn      # -> int total minted (len my_share_hashes)
        self._own_notchain = own_notchain_fn  # -> int own shares NOT in chain
        self._clock        = clock
        self._hr_samples   = deque()          # (ts, own_hr)
        self._orphan_samples = deque()        # (ts, mint_total, notchain_total)
        self._active       = {}               # name -> _ArmState
        self._cooldown     = {}               # name -> ts_until_rearm_allowed
        self._status       = {}               # name -> dict (web export)

    def sample(self):                         # call every poll tick (5 s), always
        now = self._clock()
        self._hr_samples.append((now, self._own_hr()))
        while self._hr_samples and now - self._hr_samples[0][0] > self.OWN_HR_WINDOW:
            self._hr_samples.popleft()
        self._orphan_samples.append((now, self._own_mint(), self._own_notchain()))
        while self._orphan_samples and now - self._orphan_samples[0][0] > self.ORPHAN_WINDOW:
            self._orphan_samples.popleft()

    def _baseline_own_hr(self):
        # py2.7: max() has no default= kwarg, so guard the empty case explicitly.
        if not self._hr_samples:
            return 0.0
        return max(hr for _, hr in self._hr_samples)

    def _windowed_orphan(self):
        # windowed delta rate + fresh-share count; None rate until warm.
        if len(self._orphan_samples) < 2:
            return None, 0
        _, m0, nc0 = self._orphan_samples[0]
        _, m1, nc1 = self._orphan_samples[-1]
        d_mint = m1 - m0
        if d_mint < self.MIN_OWN_DELTA:
            return None, d_mint
        d_nc = max(0, nc1 - nc0)
        return float(d_nc) / d_mint, d_mint

    def arm(self, name, recovery_fn=None):    # ENTRY: client decides trigger; respects cooldown
        now = self._clock()
        if now < self._cooldown.get(name, 0):
            return False
        if name not in self._active:
            self._active[name] = _ArmState(now, self._baseline_own_hr(), recovery_fn)
        return True

    def tick(self, name):                     # AUTHORITATIVE: returns True if still active
        st = self._active.get(name)
        if st is None:
            return False
        now = self._clock()
        secs = now - st.arm_ts
        rate, d_mint = self._windowed_orphan()
        own_hr = self._own_hr()
        reason = None
        # ARM A: hard duration backstop -- unconditional
        if secs >= self.T_MAX:
            reason = 'duration_bound'
        # ARM B: own-production orphan circuit-breaker, warmed + hysteresis + sustained
        elif rate is not None and rate >= self.ORPHAN_CEILING:
            if st.orphan_since is None:
                st.orphan_since = now
            elif now - st.orphan_since >= self.T_ORPHAN:
                reason = 'orphan_breaker'
        else:
            if rate is None or rate < self.ORPHAN_CLEAR:
                st.orphan_since = None
            # optional client recovery (whale passes None -> skipped)
            if reason is None and st.recovery_fn is not None and rate is not None:
                h = OwnHealth(own_hr, st.baseline_own_hr, rate, d_mint, secs)
                if st.recovery_fn(h):
                    reason = 'recovered'
        self._status[name] = dict(active=(reason is None), seconds=round(secs, 1),
            own_orphan_rate=(round(rate, 3) if rate is not None else None),
            own_hr=own_hr, baseline_own_hr=st.baseline_own_hr, exit_reason=reason,
            rearm_blocked=(now < self._cooldown.get(name, 0)))
        if reason is not None:
            del self._active[name]
            if reason in ('duration_bound', 'orphan_breaker') and reason == 'orphan_breaker':
                self._cooldown[name] = now + self.REARM_COOLDOWN   # cooldown ONLY on orphan-forced exit
            return False
        return True

    def status(self):
        return dict(self._status)


class _ArmState(object):
    __slots__ = ('arm_ts', 'baseline_own_hr', 'recovery_fn', 'orphan_since')

    def __init__(self, arm_ts, baseline_own_hr, recovery_fn):
        self.arm_ts = arm_ts
        self.baseline_own_hr = baseline_own_hr
        self.recovery_fn = recovery_fn
        self.orphan_since = None
