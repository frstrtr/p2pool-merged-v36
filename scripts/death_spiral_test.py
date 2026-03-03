#!/usr/bin/env python3
"""
Death Spiral Test for Phase 1b Emergency Decay

Tests the emergency time-based difficulty decay by simulating extreme
hashrate departures on the LTC testnet p2pool.

Testbed:
  node29 (192.168.86.29): bravo (~1.3 MH/s), charlie (~1.4 MH/s), cpu (~70 kH/s)
  node31 (192.168.86.31): alfa (~1.1 MH/s)
  Total: ~3.9 MH/s

Phase 1b parameters (testnet):
  SHARE_PERIOD = 4s
  EMERGENCY_THRESHOLD = SHARE_PERIOD * 20 = 80s
  DECAY_HALF_LIFE = SHARE_PERIOD * 10 = 40s

Test scenarios:
  1. 98% death spiral: block all ASICs, leave CPU only (~70 kH/s)
  2. Recovery: unblock ASICs, watch difficulty normalize
"""

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from datetime import datetime

NODE29 = "192.168.86.29"
NODE31 = "192.168.86.31"
WORKER_PORT = 19327

# Miner IPs
BRAVO_IP  = "192.168.86.22"   # bravo on node29
CHARLIE_IP = "192.168.86.249"  # charlie on node29
ALFA_IP   = "192.168.86.20"   # alfa on node31

SHARE_PERIOD = 4  # testnet
EMERGENCY_THRESHOLD = SHARE_PERIOD * 20  # 80s
DECAY_HALF_LIFE = SHARE_PERIOD * 10      # 40s


def ssh_cmd(host, cmd, timeout=10):
    """Run command on remote host via SSH."""
    try:
        r = subprocess.run(
            ["ssh", f"user0@{host}", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return "<timeout>", -1


def get_node_stats(host):
    """Get node stats from local_stats API."""
    out, rc = ssh_cmd(host, f"curl -s http://localhost:{WORKER_PORT}/local_stats")
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def get_share_count(host):
    """Get total share count from node."""
    stats = get_node_stats(host)
    if stats:
        return stats.get('shares', {}).get('total', 0)
    return 0


def get_pool_hashrate(host):
    """Get total pool hashrate from node."""
    stats = get_node_stats(host)
    if stats:
        rates = stats.get('miner_hash_rates', {})
        return sum(rates.values())
    return 0


def get_difficulty(host):
    """Get current share difficulty from node."""
    out, rc = ssh_cmd(host, f"curl -s http://localhost:{WORKER_PORT}/difficulty")
    if rc == 0 and out:
        try:
            return float(out)
        except ValueError:
            pass
    return None


def get_last_share_time(host):
    """Get timestamp of most recent share from log (UTC)."""
    out, rc = ssh_cmd(host, "tail -200 ~/p2pool-merged/data/litecoin_testnet/log | grep 'Received good share' | tail -1")
    if rc == 0 and out:
        # Extract timestamp from: "2026-03-03 06:31:04.532176 Received good share..."
        try:
            ts_str = out[:26]
            dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S.%f")
            # Node logs are UTC — use calendar.timegm to avoid local TZ bias
            import calendar
            return calendar.timegm(dt.timetuple()) + dt.microsecond / 1e6
        except (ValueError, IndexError):
            pass
    return None


def get_error_counts(host):
    """Get consensus error counts from log."""
    counts = {}
    for pattern in ['gentx', 'Share check failed', 'Traceback']:
        out, rc = ssh_cmd(host, f"grep -c '{pattern}' ~/p2pool-merged/data/litecoin_testnet/log 2>/dev/null || echo 0")
        counts[pattern] = int(out) if out.isdigit() else 0
    return counts


def block_miner(node_host, miner_ip):
    """Block a miner from connecting to a node using iptables REJECT."""
    cmd = f"sudo /usr/sbin/iptables -I INPUT -s {miner_ip} -p tcp --dport {WORKER_PORT} -j REJECT --reject-with tcp-reset"
    out, rc = ssh_cmd(node_host, cmd)
    return rc == 0


def unblock_miner(node_host, miner_ip):
    """Remove iptables block for a miner."""
    cmd = f"sudo /usr/sbin/iptables -D INPUT -s {miner_ip} -p tcp --dport {WORKER_PORT} -j REJECT --reject-with tcp-reset 2>/dev/null"
    out, rc = ssh_cmd(node_host, cmd)
    return rc == 0


def block_all_asics():
    """Block all 3 ASIC miners, leaving only CPU."""
    print("  Blocking bravo (%s) on node29..." % BRAVO_IP)
    block_miner(NODE29, BRAVO_IP)
    print("  Blocking charlie (%s) on node29..." % CHARLIE_IP)
    block_miner(NODE29, CHARLIE_IP)
    print("  Blocking alfa (%s) on node31..." % ALFA_IP)
    block_miner(NODE31, ALFA_IP)


def unblock_all_asics():
    """Unblock all 3 ASIC miners."""
    print("  Unblocking bravo (%s) on node29..." % BRAVO_IP)
    unblock_miner(NODE29, BRAVO_IP)
    print("  Unblocking charlie (%s) on node29..." % CHARLIE_IP)
    unblock_miner(NODE29, CHARLIE_IP)
    print("  Unblocking alfa (%s) on node31..." % ALFA_IP)
    unblock_miner(NODE31, ALFA_IP)


def clear_iptables():
    """Clear any leftover iptables rules."""
    for miner_ip in [BRAVO_IP, CHARLIE_IP]:
        unblock_miner(NODE29, miner_ip)
    unblock_miner(NODE31, ALFA_IP)


def poll_state(start_time):
    """Collect one snapshot of the testbed state."""
    elapsed = time.time() - start_time
    
    n29_shares = get_share_count(NODE29)
    n31_shares = get_share_count(NODE31)
    n29_hashrate = get_pool_hashrate(NODE29)
    n29_diff = get_difficulty(NODE29)
    n29_last = get_last_share_time(NODE29)
    
    gap = None
    if n29_last:
        gap = time.time() - n29_last
    
    return {
        'elapsed': elapsed,
        'n29_shares': n29_shares,
        'n31_shares': n31_shares,
        'hashrate_khs': n29_hashrate / 1000.0 if n29_hashrate else 0,
        'difficulty': n29_diff,
        'share_gap_s': gap,
    }


def print_state(state, phase, alert=""):
    """Print a single-line status update."""
    gap_str = "%.0fs" % state['share_gap_s'] if state['share_gap_s'] else "?"
    diff_str = "%.6f" % state['difficulty'] if state['difficulty'] else "?"
    alert_str = " *** %s ***" % alert if alert else ""
    
    emergency = ""
    if state['share_gap_s'] and state['share_gap_s'] > EMERGENCY_THRESHOLD:
        emergency = " [EMERGENCY ZONE: gap=%ds > threshold=%ds]" % (
            int(state['share_gap_s']), EMERGENCY_THRESHOLD)
    
    print("  [%4.0fs] %-12s | shares=%d/%d | %7.0f kH/s | diff=%s | gap=%s%s%s" % (
        state['elapsed'], phase,
        state['n29_shares'], state['n31_shares'],
        state['hashrate_khs'],
        diff_str, gap_str,
        emergency, alert_str))


def run_death_spiral_test(baseline_min=3, spiral_min=8, recovery_min=5, poll_sec=5,
                          output_csv=None):
    """
    Run the death spiral test:
    1. BASELINE: Normal mining with all miners (baseline_min minutes)
    2. SPIRAL: Block all ASICs, CPU only (spiral_min minutes)
    3. RECOVERY: Unblock ASICs, watch recovery (recovery_min minutes)
    """
    
    start_time = time.time()
    csv_rows = []
    
    # Ensure clean state
    print("\n" + "="*72)
    print("  DEATH SPIRAL TEST — Phase 1b Emergency Decay")
    print("="*72)
    print("\n  Clearing any leftover iptables rules...")
    clear_iptables()
    
    # Record initial errors
    initial_errors_29 = get_error_counts(NODE29)
    initial_errors_31 = get_error_counts(NODE31)
    print("  Initial errors node29:", initial_errors_29)
    print("  Initial errors node31:", initial_errors_31)
    
    baseline_shares_start = get_share_count(NODE29)
    baseline_diff = get_difficulty(NODE29)
    print("  Starting shares: %d, difficulty: %s" % (baseline_shares_start, baseline_diff))
    
    # ── PHASE 1: BASELINE ──
    print("\n" + "-"*72)
    print("  PHASE 1: BASELINE (%d min) — all miners active" % baseline_min)
    print("-"*72)
    
    baseline_end = start_time + baseline_min * 60
    while time.time() < baseline_end:
        state = poll_state(start_time)
        state['phase'] = 'BASELINE'
        print_state(state, 'BASELINE')
        csv_rows.append(state)
        time.sleep(poll_sec)
    
    pre_spiral_shares = get_share_count(NODE29)
    pre_spiral_diff = get_difficulty(NODE29)
    baseline_share_rate = (pre_spiral_shares - baseline_shares_start) / (baseline_min * 60)
    print("\n  Baseline complete: %d shares in %d min (%.1f shares/sec)" % (
        pre_spiral_shares - baseline_shares_start, baseline_min, baseline_share_rate))
    print("  Baseline difficulty: %s" % pre_spiral_diff)
    
    # ── PHASE 2: DEATH SPIRAL ──
    print("\n" + "-"*72)
    print("  PHASE 2: DEATH SPIRAL (%d min) — blocking all ASICs" % spiral_min)
    print("  Only CPU miner (~70 kH/s) remains — 98%% hashrate drop")
    print("  Emergency threshold: %ds, decay half-life: %ds" % (
        EMERGENCY_THRESHOLD, DECAY_HALF_LIFE))
    print("-"*72)
    
    block_time = time.time()
    block_all_asics()
    print("  All ASICs blocked at t=%.0fs" % (block_time - start_time))
    
    spiral_end = block_time + spiral_min * 60
    spiral_shares_start = get_share_count(NODE29)
    first_emergency = None
    first_share_after_gap = None
    max_gap = 0
    prev_shares = spiral_shares_start
    
    while time.time() < spiral_end:
        state = poll_state(start_time)
        state['phase'] = 'SPIRAL'
        
        # Track emergency zone entry
        if state['share_gap_s'] and state['share_gap_s'] > EMERGENCY_THRESHOLD:
            if first_emergency is None:
                first_emergency = time.time() - start_time
                print_state(state, 'SPIRAL', "EMERGENCY DECAY ACTIVE!")
            else:
                print_state(state, 'SPIRAL')
        else:
            print_state(state, 'SPIRAL')
        
        # Track max gap
        if state['share_gap_s'] and state['share_gap_s'] > max_gap:
            max_gap = state['share_gap_s']
        
        # Detect first share after gap entered emergency zone
        curr_shares = state['n29_shares']
        if curr_shares > prev_shares and first_emergency is not None and first_share_after_gap is None:
            first_share_after_gap = time.time() - start_time
            shares_gained = curr_shares - spiral_shares_start
            print("  >>> First new share at t=%.0fs (shares gained: %d)" % (
                first_share_after_gap, shares_gained))
        prev_shares = curr_shares
        
        csv_rows.append(state)
        time.sleep(poll_sec)
    
    spiral_shares_end = get_share_count(NODE29)
    spiral_diff = get_difficulty(NODE29)
    print("\n  Spiral stats:")
    print("    Shares produced during spiral: %d" % (spiral_shares_end - spiral_shares_start))
    print("    Max share gap: %.0fs" % max_gap)
    print("    Emergency zone entered at: t=%.0fs" % (first_emergency or 0))
    print("    First share in emergency: t=%.0fs" % (first_share_after_gap or 0))
    print("    Difficulty at end of spiral: %s (was %s)" % (spiral_diff, pre_spiral_diff))
    if pre_spiral_diff and spiral_diff:
        print("    Difficulty change: %.1f%%" % ((spiral_diff / pre_spiral_diff - 1) * 100))
    
    # ── PHASE 3: RECOVERY ──
    print("\n" + "-"*72)
    print("  PHASE 3: RECOVERY (%d min) — unblocking all ASICs" % recovery_min)
    print("-"*72)
    
    unblock_time = time.time()
    unblock_all_asics()
    print("  All ASICs unblocked at t=%.0fs" % (unblock_time - start_time))
    
    recovery_end = unblock_time + recovery_min * 60
    recovery_shares_start = get_share_count(NODE29)
    recovery_diff_start = get_difficulty(NODE29)
    
    while time.time() < recovery_end:
        state = poll_state(start_time)
        state['phase'] = 'RECOVERY'
        print_state(state, 'RECOVERY')
        csv_rows.append(state)
        time.sleep(poll_sec)
    
    recovery_shares_end = get_share_count(NODE29)
    recovery_diff_end = get_difficulty(NODE29)
    
    # ── FINAL REPORT ──
    final_errors_29 = get_error_counts(NODE29)
    final_errors_31 = get_error_counts(NODE31)
    
    new_errors_29 = {k: final_errors_29[k] - initial_errors_29.get(k, 0) for k in final_errors_29}
    new_errors_31 = {k: final_errors_31[k] - initial_errors_31.get(k, 0) for k in final_errors_31}
    
    print("\n" + "="*72)
    print("  DEATH SPIRAL TEST COMPLETE")
    print("="*72)
    print("\n  Phase 1b parameters:")
    print("    SHARE_PERIOD = %ds" % SHARE_PERIOD)
    print("    EMERGENCY_THRESHOLD = %ds" % EMERGENCY_THRESHOLD)
    print("    DECAY_HALF_LIFE = %ds" % DECAY_HALF_LIFE)
    print("\n  Baseline (all miners):")
    print("    Share rate: %.2f shares/sec" % baseline_share_rate)
    print("    Difficulty: %s" % pre_spiral_diff)
    print("\n  Death spiral (CPU only, 98%% drop):")
    print("    Duration: %d min" % spiral_min)
    print("    Max share gap: %.0fs" % max_gap)
    print("    Emergency threshold hit: %s" % ("YES at t=%.0fs" % first_emergency if first_emergency else "NO"))
    print("    Shares during spiral: %d" % (spiral_shares_end - spiral_shares_start))
    print("    Difficulty at exit: %s" % spiral_diff)
    if pre_spiral_diff and spiral_diff:
        ratio = spiral_diff / pre_spiral_diff
        print("    Difficulty reduction: %.1f%% (%.1f× easier)" % ((1 - ratio) * 100, 1/ratio if ratio > 0 else 0))
    print("\n  Recovery (all miners return):")
    print("    Shares during recovery: %d" % (recovery_shares_end - recovery_shares_start))
    print("    Difficulty at end: %s" % recovery_diff_end)
    if recovery_diff_start and recovery_diff_end:
        print("    Recovery ratio: %.2f×" % (recovery_diff_end / recovery_diff_start))
    print("\n  Consensus errors (new during test):")
    print("    node29:", new_errors_29)
    print("    node31:", new_errors_31)
    
    has_errors = any(v > 0 for v in new_errors_29.values() if 'Traceback' not in str(v)) or \
                 any(v > 0 for v in new_errors_31.values() if 'Traceback' not in str(v))
    
    if new_errors_29.get('gentx', 0) > 0 or new_errors_31.get('gentx', 0) > 0:
        print("\n  ✗ CONSENSUS ERROR: gentx mismatches detected!")
    elif new_errors_29.get('Share check failed', 0) > 0 or new_errors_31.get('Share check failed', 0) > 0:
        print("\n  ✗ CONSENSUS ERROR: share check failures detected!")
    else:
        print("\n  ✓ No consensus errors — Phase 1b emergency decay is deterministic")
    
    # Write CSV
    if output_csv:
        with open(output_csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=['elapsed', 'phase', 'n29_shares', 'n31_shares',
                                              'hashrate_khs', 'difficulty', 'share_gap_s'])
            w.writeheader()
            for row in csv_rows:
                w.writerow(row)
        print("\n  CSV output: %s" % output_csv)
    
    print("="*72)
    
    return {
        'max_gap': max_gap,
        'emergency_hit': first_emergency is not None,
        'spiral_shares': spiral_shares_end - spiral_shares_start,
        'baseline_diff': pre_spiral_diff,
        'spiral_diff': spiral_diff,
        'recovery_diff': recovery_diff_end,
        'consensus_errors': new_errors_29.get('gentx', 0) + new_errors_31.get('gentx', 0) +
                           new_errors_29.get('Share check failed', 0) + new_errors_31.get('Share check failed', 0),
    }


def main():
    parser = argparse.ArgumentParser(description="Death Spiral Test for Phase 1b Emergency Decay")
    parser.add_argument('--baseline-min', type=int, default=3, help='Baseline phase duration (min)')
    parser.add_argument('--spiral-min', type=int, default=8, help='Death spiral phase duration (min)')
    parser.add_argument('--recovery-min', type=int, default=5, help='Recovery phase duration (min)')
    parser.add_argument('--poll', type=int, default=5, help='Polling interval (sec)')
    parser.add_argument('--output', type=str, default=None, help='CSV output file')
    args = parser.parse_args()
    
    if args.output is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = "scripts/death_spiral_%s.csv" % ts
    
    try:
        result = run_death_spiral_test(
            baseline_min=args.baseline_min,
            spiral_min=args.spiral_min,
            recovery_min=args.recovery_min,
            poll_sec=args.poll,
            output_csv=args.output,
        )
    except KeyboardInterrupt:
        print("\n\n  Interrupted! Cleaning up iptables...")
        clear_iptables()
        print("  All miners unblocked.")
        sys.exit(1)
    except Exception as e:
        print("\n\n  ERROR: %s" % e)
        print("  Cleaning up iptables...")
        clear_iptables()
        raise


if __name__ == '__main__':
    main()
