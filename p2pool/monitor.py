"""Phase 3L: Log-based pool monitoring for attack detection.

Emits structured [MONITOR] log lines every status cycle (~30s).
All output is grep-friendly — no HTTP endpoints, no web.py changes.

Log line prefixes:
  [MONITOR-HASHRATE]    — pool hashrate vs moving average
  [MONITOR-CONC]        — per-address work concentration
  [MONITOR-EMERGENCY]   — emergency decay triggers / share gaps
  [MONITOR-DIFF]        — difficulty anomaly detection
  [MONITOR-SUMMARY]     — one-line health summary

Grep examples:
  grep '\\[MONITOR-' log                   # all monitoring
  grep '\\[MONITOR-CONC\\].*ALERT' log     # concentration alerts only
  grep '\\[MONITOR-EMERGENCY\\]' log       # emergency events
"""

from __future__ import division
import time
import sys

from p2pool import data as p2pool_data
from p2pool.bitcoin import data as bitcoin_data


class PoolMonitor(object):
    """Lightweight log-based monitoring — runs from status_thread() in main.py."""

    # Thresholds (configurable)
    CONCENTRATION_WARN_PCT = 25     # single address > 25% of window
    CONCENTRATION_ALERT_PCT = 40    # single address > 40% of window
    HASHRATE_SPIKE_FACTOR = 1.5     # pool hashrate > 150% of moving average
    HASHRATE_DROP_FACTOR = 0.5      # pool hashrate < 50% of moving average
    DIFF_ANOMALY_FACTOR = 2.0       # target deviation > 200% from expected

    def __init__(self, net):
        self.net = net
        self._hashrate_history = []     # [(timestamp, hashrate)] — 1h rolling
        self._emergency_count = 0
        self._last_emergency_ts = 0
        self._last_share_gap = 0
        self._cycle = 0

    def run_cycle(self, tracker, best_share_hash, bitcoind_work_value):
        """Run one monitoring cycle. Called from status_thread() every ~30s.

        Returns list of alert strings (empty = healthy).
        """
        self._cycle += 1
        alerts = []

        if best_share_hash is None:
            return alerts

        height = tracker.get_height(best_share_hash)
        if height < 10:
            return alerts

        try:
            alerts += self._check_hashrate(tracker, best_share_hash, height)
        except Exception as e:
            print >> sys.stderr, '[MONITOR] hashrate check error: %s' % e

        try:
            alerts += self._check_concentration(tracker, best_share_hash, height)
        except Exception as e:
            print >> sys.stderr, '[MONITOR] concentration check error: %s' % e

        try:
            alerts += self._check_share_gap(tracker, best_share_hash)
        except Exception as e:
            print >> sys.stderr, '[MONITOR] share gap check error: %s' % e

        try:
            alerts += self._check_difficulty(tracker, best_share_hash, height,
                                             bitcoind_work_value)
        except Exception as e:
            print >> sys.stderr, '[MONITOR] difficulty check error: %s' % e

        # Summary line every cycle
        status = 'OK' if not alerts else 'ALERT(%d)' % len(alerts)
        best = tracker.items[best_share_hash]
        gap = max(0, int(time.time()) - best.timestamp)
        print '[MONITOR-SUMMARY] cycle=%d height=%d gap=%ds status=%s alerts=%d' % (
            self._cycle, height, gap, status, len(alerts))

        return alerts

    # ── Hashrate spike/drop detection ──────────────────────────────

    def _check_hashrate(self, tracker, best_share_hash, height):
        alerts = []
        lookbehind = min(height - 1, 60 * 60 // self.net.SHARE_PERIOD)
        if lookbehind < 2:
            return alerts

        try:
            stale_prop = p2pool_data.get_average_stale_prop(
                tracker, best_share_hash, lookbehind)
            raw_att_s = p2pool_data.get_pool_attempts_per_second(
                tracker, best_share_hash, lookbehind)
            real_att_s = raw_att_s / (1 - stale_prop) if stale_prop < 1 else raw_att_s
        except Exception:
            return alerts

        now = time.time()
        self._hashrate_history.append((now, real_att_s))
        # Keep 1 hour of history
        cutoff = now - 3600
        self._hashrate_history = [(t, h) for t, h in self._hashrate_history if t > cutoff]

        if len(self._hashrate_history) < 3:
            return alerts

        avg_hr = sum(h for _, h in self._hashrate_history) / len(self._hashrate_history)

        if avg_hr > 0:
            ratio = real_att_s / avg_hr
            if ratio > self.HASHRATE_SPIKE_FACTOR:
                msg = '[MONITOR-HASHRATE] ALERT spike=%.1fx current=%sH/s avg_1h=%sH/s' % (
                    ratio, _fmt(real_att_s), _fmt(avg_hr))
                print msg
                alerts.append(msg)
            elif ratio < self.HASHRATE_DROP_FACTOR:
                msg = '[MONITOR-HASHRATE] ALERT drop=%.1fx current=%sH/s avg_1h=%sH/s' % (
                    ratio, _fmt(real_att_s), _fmt(avg_hr))
                print msg
                alerts.append(msg)
            elif self._cycle % 10 == 0:
                # Periodic (every ~5 min) — healthy status
                print '[MONITOR-HASHRATE] ok ratio=%.2f current=%sH/s avg_1h=%sH/s samples=%d' % (
                    ratio, _fmt(real_att_s), _fmt(avg_hr), len(self._hashrate_history))

        return alerts

    # ── Per-address work concentration ─────────────────────────────

    def _check_concentration(self, tracker, best_share_hash, height):
        alerts = []

        # Check over 3 windows: short (recent 100), medium (720), full chain
        windows = [
            ('short', min(height, 100)),
            ('medium', min(height, 720)),
        ]
        if height >= self.net.REAL_CHAIN_LENGTH:
            windows.append(('full', min(height, self.net.REAL_CHAIN_LENGTH)))

        for label, depth in windows:
            addr_work = {}
            total_work = 0
            for share in tracker.get_chain(best_share_hash, depth):
                w = bitcoin_data.target_to_average_attempts(share.target)
                addr = share.address
                addr_work[addr] = addr_work.get(addr, 0) + w
                total_work += w

            if total_work == 0:
                continue

            # Find top addresses by concentration
            for addr, work in addr_work.iteritems():
                pct = 100.0 * work / total_work
                if pct >= self.CONCENTRATION_ALERT_PCT:
                    msg = '[MONITOR-CONC] ALERT addr=%s pct=%.1f%% window=%s(%d)' % (
                        addr[:30], pct, label, depth)
                    print msg
                    alerts.append(msg)
                elif pct >= self.CONCENTRATION_WARN_PCT:
                    msg = '[MONITOR-CONC] WARN addr=%s pct=%.1f%% window=%s(%d)' % (
                        addr[:30], pct, label, depth)
                    print msg

            # Every 10th cycle (~5min), log top-3 miners for visibility
            if self._cycle % 10 == 0:
                top = sorted(addr_work.items(), key=lambda x: -x[1])[:3]
                top_str = ' '.join('%s:%.1f%%' % (a[:16], 100.0*w/total_work) for a, w in top)
                print '[MONITOR-CONC] top3 window=%s(%d) %s' % (label, depth, top_str)

        return alerts

    # ── Share gap / emergency decay detection ──────────────────────

    def _check_share_gap(self, tracker, best_share_hash):
        alerts = []
        best = tracker.items[best_share_hash]
        now = int(time.time())
        gap = max(0, now - best.timestamp)
        emergency_threshold = self.net.SHARE_PERIOD * 20

        self._last_share_gap = gap

        if gap > emergency_threshold:
            self._emergency_count += 1
            self._last_emergency_ts = now
            msg = '[MONITOR-EMERGENCY] ALERT gap=%ds threshold=%ds emergency_count=%d' % (
                gap, emergency_threshold, self._emergency_count)
            print msg
            alerts.append(msg)
        elif gap > self.net.SHARE_PERIOD * 10:
            msg = '[MONITOR-EMERGENCY] WARN gap=%ds (approaching threshold=%ds)' % (
                gap, emergency_threshold)
            print msg

        return alerts

    # ── Difficulty anomaly detection ───────────────────────────────

    def _check_difficulty(self, tracker, best_share_hash, height, bitcoind_work_value):
        alerts = []
        best = tracker.items[best_share_hash]

        # Compare share difficulty with expected from pool hashrate
        lookbehind = min(height - 1, self.net.TARGET_LOOKBEHIND)
        if lookbehind < 2:
            return alerts

        try:
            att_s = p2pool_data.get_pool_attempts_per_second(
                tracker, best_share_hash, lookbehind, min_work=True, integer=True)
        except Exception:
            return alerts

        if att_s <= 0:
            return alerts

        expected_target = 2**256 // (self.net.SHARE_PERIOD * att_s) - 1
        actual_target = best.max_target

        if expected_target <= 0:
            return alerts

        ratio = float(actual_target) / float(expected_target)

        if ratio > self.DIFF_ANOMALY_FACTOR:
            msg = '[MONITOR-DIFF] ALERT target_ratio=%.2f (actual easier than expected by %.0f%%)' % (
                ratio, (ratio - 1) * 100)
            print msg
            alerts.append(msg)
        elif ratio < 1.0 / self.DIFF_ANOMALY_FACTOR:
            msg = '[MONITOR-DIFF] ALERT target_ratio=%.2f (actual harder than expected by %.0f%%)' % (
                ratio, (1.0/ratio - 1) * 100)
            print msg
            alerts.append(msg)
        elif self._cycle % 10 == 0:
            print '[MONITOR-DIFF] ok target_ratio=%.2f pool=%sH/s' % (ratio, _fmt(att_s))

        return alerts


def _fmt(n):
    """Format large numbers with SI suffix."""
    for suffix in ['', 'k', 'M', 'G', 'T', 'P']:
        if abs(n) < 1000:
            return '%.1f%s' % (n, suffix)
        n /= 1000.0
    return '%.1f%s' % (n, 'E')
