# -*- coding: utf-8 -*-
"""
G2 fill budgets (p2pool-merged-v36): one token bucket per parent chain,
metering new-to-sharechain-window transaction bytes per share.

LOCAL POLICY, NOT CONSENSUS (g2-consensus-verdict): v34+ shares carry no
transaction hash lists on the wire; Share.check() reconstructs the gentx
with known_txs=None, so no size condition can trip during validation, and
should_punish_reason() has no size term for v34+. Peers accept our shares
regardless of what this module decides -- it only shapes what OUR node
commits into its own block candidates.

Two-phase contract (the load-bearing design point):
  grant()          pure read, used by get_work()/template builds. get_work
                   fires per miner request, many times per share; it must
                   NOT drain the budget.
  settle(spent)    the ONE debit, from got_response() when a share is
                   actually FOUND. DOA/orphaned shares settle too: their
                   bytes hit the p2p wire regardless of acceptance.
  on_block_reset() parent chain found a block: refill to burst (catch-up
                   bonus) and restart the ramp at the legacy floor.

Floor guarantee: grant() never returns less than `floor` (default: the
exact legacy v35 50 kB constant) in ANY state -- token exhaustion included.
The bucket only governs how far ABOVE the legacy envelope a share may go,
so the worst case is v35 behavior by construction.
"""

from __future__ import division

import time

# Exact legacy generation-side constant (the forrestv-era
# "only allow 50 kB of new txns/share" break). The ramp floor.
LEGACY_NEWTX_CAP = 50000


class FillBudget(object):
    def __init__(self, name, rate, burst, floor=LEGACY_NEWTX_CAP,
                 ramp_shares=4, clock=None):
        # runtime checks, not asserts: must survive python -O
        if not (0 < floor <= burst):
            raise ValueError('FillBudget %r: need 0 < floor <= burst '
                             '(floor=%r, burst=%r)' % (name, floor, burst))
        if rate <= 0:
            raise ValueError('FillBudget %r: rate must be > 0' % (name,))
        self.name = name                        # e.g. 'ltc'
        self.rate = float(rate)                 # bytes/sec sustained refill
        self.burst = int(burst)                 # capacity == max per-share cap
        self.floor = int(floor)                 # never grant less (legacy)
        self.ramp_shares = max(1, int(ramp_shares))
        self._clock = clock if clock is not None else time.time
        self.tokens = float(self.burst)         # boot full: allow catch-up
        self.last_refill = self._clock()
        self.shares_since_reset = self.ramp_shares  # boot: ramp complete

    def refill(self, now=None):
        now = self._clock() if now is None else now
        dt = now - self.last_refill
        self.last_refill = now
        if dt > 0:
            self.tokens = min(float(self.burst), self.tokens + self.rate * dt)

    def current_cap(self):
        # Linear ramp: exactly `floor` on the first share after a reset
        # (worst case == v35), reaching `burst` after ramp_shares shares.
        # Integer arithmetic -- deterministic for the KATs.
        k = self.shares_since_reset
        if k >= self.ramp_shares:
            return self.burst
        return self.floor + (self.burst - self.floor) * k // self.ramp_shares

    def grant(self, now=None):
        """Max new-tx bytes the template being built may commit. Pure read;
        never below floor (see module docstring)."""
        self.refill(now)
        return int(max(self.floor, min(self.current_cap(), self.tokens)))

    def settle(self, spent_bytes, now=None):
        """A share was FOUND committing spent_bytes of new txs: the one
        debit. Advances the ramp."""
        self.refill(now)
        self.tokens = max(0.0, self.tokens - spent_bytes)
        self.shares_since_reset += 1

    def on_block_reset(self, now=None):
        """Parent (or ridden) chain found a block: mempool churn incoming.
        Full catch-up bonus + ramp restart."""
        self.refill(now)
        self.tokens = float(self.burst)
        self.shares_since_reset = 0

    def snapshot(self):
        """For the truth-layer metric emitter (T1: t1.fill.grant_bytes,
        t1.fill.tokens, t1.fill.shares_since_reset)."""
        return dict(name=self.name, tokens=int(self.tokens),
                    cap=self.current_cap(), floor=self.floor,
                    burst=self.burst, rate=self.rate,
                    shares_since_reset=self.shares_since_reset)


class FillBudgetBook(object):
    """Registry + rider wiring ("DOGE rides litecoin"): aux buckets, if
    ever registered, refill/reset on the parent chain's events -- one
    clock, N buckets. v36 DOGE is daemon-assembled auxpow (only the aux
    hash is committed in our coinbase; no aux tx bytes in OUR shares), so
    today only the parent bucket is registered and riders are dormant
    machinery for a future local-aux-assembly path."""

    def __init__(self):
        self.buckets = {}
        self.riders = {}    # parent tag -> [aux tags]

    def register(self, tag, bucket, rides=None):
        self.buckets[tag] = bucket
        if rides is not None:
            self.riders.setdefault(rides, []).append(tag)
        return bucket

    def get(self, tag):
        return self.buckets[tag]

    def on_block_reset(self, tag, now=None):
        self.buckets[tag].on_block_reset(now)
        for aux_tag in self.riders.get(tag, ()):
            self.buckets[aux_tag].on_block_reset(now)


def budget_from_net(net, clock=None):
    """Defaults derived from the parent chain; every knob overridable via
    an optional attribute on the p2pool network module.
    # TODO(maintainer): tune from fleet telemetry (t1.fill.*); defaults are
    # deliberately modest per the G2 brief (burst 200-400 kB band,
    # rate a few kB/s, floor exactly legacy).
    """
    bmax = getattr(net, 'BLOCK_MAX_SIZE', 1000000)          # p2pool net attr
    bperiod = getattr(net.PARENT, 'BLOCK_PERIOD', 600)      # coin net attr
    # Sustained refill ~= the parent chain's own byte throughput
    # (LTC: 1 MB / 150 s ~= 6.7 kB/s).
    rate = getattr(net, 'FILL_RATE_BYTES_PER_SEC', max(4000, bmax // bperiod))
    # Modest burst: 250 kB for 1 MB chains, clamped to [2*floor, 400 kB].
    burst = getattr(net, 'FILL_BURST_BYTES',
                    max(2 * LEGACY_NEWTX_CAP, min(bmax // 4, 400000)))
    return FillBudget(
        name=net.PARENT.SYMBOL.lower(),
        rate=rate,
        burst=burst,
        floor=getattr(net, 'FILL_FLOOR_BYTES', LEGACY_NEWTX_CAP),
        ramp_shares=getattr(net, 'FILL_RAMP_SHARES', 4),
        clock=clock)
