#!/usr/bin/env python3
"""
Phase 1a Asymmetric Clamp - Pool Hopping Test Monitor
=====================================================
Polls p2pool nodes every INTERVAL seconds and logs:
  - Per-address share counts, hash rates, payout proportions
  - Share difficulty (retarget tracking)
  - Pool hash rate
  - Phase annotations (user can mark phases via stdin)

Usage:
    python3 scripts/hopper_monitor.py [--interval 5] [--output hopper_test.csv]

Press keys during monitoring:
    B = mark Baseline phase
    D = mark Departure phase (hopper leaves)
    R = mark Recovery phase
    A = mark Arrival phase (hopper returns)
    F = mark Final departure phase
    Q = quit
"""

import argparse
import csv
import json
import os
import select
import sys
import time
import urllib.request
from datetime import datetime

# Test addresses
ADDR_HONEST_CPU = "tltc1qktdaszj95rqhzw92jxgrpv295kzs6nct3k45x7"
ADDR_ANCHOR     = "tltc1qw3nfd6xwv8ecwz0xq4expjgdlnsv8qmwvxqyqh"
ADDR_HOPPER     = "tltc1qaj0tyr6zckzavhq984kzz6p96djsvcv9wahfc3"
ADDR_OLD        = "QZQGeMoG3MaLmWwRTcbMwuxYenkHE2zhUN"  # legacy combined addr

NODES = [
    ("node29", "http://192.168.86.29:19327"),
    ("node31", "http://192.168.86.31:19327"),
]

PHASE_KEYS = {
    'b': 'BASELINE',
    'd': 'DEPARTURE',
    'r': 'RECOVERY',
    'a': 'ARRIVAL',
    'f': 'FINAL_DEPARTURE',
}


def fetch_json(url, timeout=5):
    """Fetch JSON from a URL with timeout."""
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode())
    except Exception as e:
        return None


def get_pool_stats(base_url):
    """Get combined stats from a p2pool node."""
    stats = fetch_json(base_url + "/local_stats")
    payouts = fetch_json(base_url + "/current_payouts")
    global_stats = fetch_json(base_url + "/global_stats")
    return stats, payouts, global_stats


def classify_address(addr):
    """Map an address (or address.worker) to a role."""
    base = addr.split('.')[0]
    if base == ADDR_HONEST_CPU:
        return "honest_cpu"
    elif base == ADDR_ANCHOR:
        return "anchor"
    elif base == ADDR_HOPPER:
        return "hopper"
    elif base == ADDR_OLD:
        return "old_combined"
    else:
        return "unknown"


def aggregate_by_role(hash_rates):
    """Aggregate hash rates by role."""
    roles = {"honest_cpu": 0, "anchor": 0, "hopper": 0, "old_combined": 0, "unknown": 0}
    for worker, rate in hash_rates.items():
        role = classify_address(worker)
        roles[role] += rate
    return roles


def aggregate_payouts_by_role(payouts):
    """Aggregate payout proportions by role."""
    roles = {"honest_cpu": 0.0, "anchor": 0.0, "hopper": 0.0, "old_combined": 0.0, "unknown": 0.0}
    if not payouts:
        return roles
    for addr, prop in payouts.items():
        role = classify_address(addr)
        roles[role] += prop
    return roles


def main():
    parser = argparse.ArgumentParser(description="Pool hopping test monitor")
    parser.add_argument("--interval", type=int, default=5, help="Poll interval in seconds")
    parser.add_argument("--output", type=str, default="hopper_test.csv", help="Output CSV file")
    args = parser.parse_args()

    csv_path = args.output
    is_new = not os.path.exists(csv_path)

    csvfile = open(csv_path, 'a', newline='')
    writer = csv.writer(csvfile)

    if is_new:
        writer.writerow([
            "timestamp", "elapsed_s", "phase",
            "pool_hashrate_khs", "share_difficulty",
            "hr_honest_cpu_khs", "hr_anchor_khs", "hr_hopper_khs", "hr_old_khs",
            "payout_honest_cpu", "payout_anchor", "payout_hopper", "payout_old",
            "shares_total", "shares_orphan", "shares_dead",
            "node"
        ])

    # Try to set stdin to non-blocking for phase key detection
    import termios, tty
    old_settings = termios.tcgetattr(sys.stdin)
    tty.setcbreak(sys.stdin.fileno())

    current_phase = "SETUP"
    start_time = time.time()
    sample = 0

    print("=" * 72)
    print("Phase 1a Asymmetric Clamp - Hopper Test Monitor")
    print("=" * 72)
    print(f"Logging to: {csv_path}")
    print(f"Poll interval: {args.interval}s")
    print()
    print("Keys: [B]aseline [D]eparture [R]ecovery [A]rrival [F]inal [Q]uit")
    print("=" * 72)

    try:
        while True:
            # Check for keypress (non-blocking)
            if select.select([sys.stdin], [], [], 0)[0]:
                key = sys.stdin.read(1).lower()
                if key == 'q':
                    print("\n>>> QUIT requested")
                    break
                elif key in PHASE_KEYS:
                    current_phase = PHASE_KEYS[key]
                    elapsed = time.time() - start_time
                    print(f"\n>>> Phase changed to: {current_phase} at {elapsed:.0f}s")

            elapsed = time.time() - start_time
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for node_name, base_url in NODES:
                stats, payouts, global_stats = get_pool_stats(base_url)
                if not stats:
                    continue

                # Hash rates by role
                roles_hr = aggregate_by_role(stats.get("miner_hash_rates", {}))
                roles_payout = aggregate_payouts_by_role(payouts)

                pool_hr = global_stats.get("pool_nonstale_hash_rate", 0) if global_stats else 0
                share_diff = list(stats.get("miner_last_difficulties", {}).values())
                avg_diff = sum(share_diff) / len(share_diff) if share_diff else 0

                shares = stats.get("shares", {})

                row = [
                    now, f"{elapsed:.0f}", current_phase,
                    f"{pool_hr/1000:.1f}", f"{avg_diff:.8f}",
                    f"{roles_hr['honest_cpu']/1000:.1f}",
                    f"{roles_hr['anchor']/1000:.1f}",
                    f"{roles_hr['hopper']/1000:.1f}",
                    f"{roles_hr['old_combined']/1000:.1f}",
                    f"{roles_payout['honest_cpu']:.6f}",
                    f"{roles_payout['anchor']:.6f}",
                    f"{roles_payout['hopper']:.6f}",
                    f"{roles_payout['old_combined']:.6f}",
                    shares.get("total", 0),
                    shares.get("orphan", 0),
                    shares.get("dead", 0),
                    node_name
                ]
                writer.writerow(row)

            csvfile.flush()
            sample += 1

            # Print summary line
            if stats:
                hr_total = sum(roles_hr.values())
                print(f"\r[{now}] {elapsed:5.0f}s {current_phase:16s} | "
                      f"pool={pool_hr/1000:.0f}kH/s diff={avg_diff:.6f} | "
                      f"cpu={roles_hr['honest_cpu']/1000:.0f} "
                      f"anchor={roles_hr['anchor']/1000:.0f} "
                      f"hopper={roles_hr['hopper']/1000:.0f} "
                      f"old={roles_hr['old_combined']/1000:.0f}kH/s",
                      end='', flush=True)

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n>>> Interrupted")
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        csvfile.close()
        print(f"\nData saved to {csv_path} ({sample} samples)")


if __name__ == "__main__":
    main()
