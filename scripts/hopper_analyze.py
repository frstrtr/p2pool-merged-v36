#!/usr/bin/env python3
"""
Phase 1a Asymmetric Clamp - Test Results Analyzer
==================================================
Reads the CSV from hopper_test.py and generates a summary report.

Usage:
    python3 scripts/hopper_analyze.py hopper_test_YYYYMMDD_HHMMSS.csv
"""

import csv
import sys
from collections import defaultdict


def load_csv(filepath):
    """Load test data grouped by phase."""
    phases = defaultdict(list)
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            phases[row['phase']].append(row)
    return phases


def phase_stats(rows, node_filter="node29"):
    """Compute stats for a phase (filtering to one node to avoid double-counting)."""
    filtered = [r for r in rows if r['node'] == node_filter]
    if not filtered:
        filtered = rows  # fallback

    diffs = [float(r['share_difficulty']) for r in filtered if float(r['share_difficulty']) > 0]
    pool_hrs = [float(r['pool_hashrate_khs']) for r in filtered]
    hopper_hrs = [float(r['hr_hopper_khs']) for r in filtered]
    anchor_hrs = [float(r['hr_anchor_khs']) for r in filtered]
    cpu_hrs = [float(r['hr_honest_cpu_khs']) for r in filtered]

    pay_hopper = [float(r['payout_hopper']) for r in filtered]
    pay_anchor = [float(r['payout_anchor']) for r in filtered]
    pay_cpu = [float(r['payout_honest_cpu']) for r in filtered]

    def avg(lst):
        return sum(lst) / len(lst) if lst else 0

    def minmax(lst):
        return (min(lst), max(lst)) if lst else (0, 0)

    start_t = float(filtered[0]['elapsed_s'])
    end_t = float(filtered[-1]['elapsed_s'])

    return {
        'duration_s': end_t - start_t,
        'samples': len(filtered),
        'diff_avg': avg(diffs),
        'diff_min': minmax(diffs)[0],
        'diff_max': minmax(diffs)[1],
        'diff_start': diffs[0] if diffs else 0,
        'diff_end': diffs[-1] if diffs else 0,
        'pool_hr_avg': avg(pool_hrs),
        'hopper_hr_avg': avg(hopper_hrs),
        'anchor_hr_avg': avg(anchor_hrs),
        'cpu_hr_avg': avg(cpu_hrs),
        'payout_hopper_end': pay_hopper[-1] if pay_hopper else 0,
        'payout_anchor_end': pay_anchor[-1] if pay_anchor else 0,
        'payout_cpu_end': pay_cpu[-1] if pay_cpu else 0,
    }


def compute_recovery_speed(rows, node_filter="node29"):
    """Measure how quickly difficulty dropped after departure.
    Returns: list of (elapsed_s, diff) tuples and the estimated recovery shares.
    """
    filtered = [r for r in rows if r['node'] == node_filter]
    if not filtered:
        return [], 0

    diffs = [(float(r['elapsed_s']), float(r['share_difficulty'])) for r in filtered
             if float(r['share_difficulty']) > 0]

    if len(diffs) < 2:
        return diffs, 0

    start_diff = diffs[0][1]
    # Find when difficulty dropped to ~50% of start (indicating recovery is well underway)
    half_target = start_diff * 0.5
    recovery_time = None
    for t, d in diffs:
        if d <= half_target:
            recovery_time = t - diffs[0][0]
            break

    return diffs, recovery_time


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/hopper_analyze.py <csv_file>")
        sys.exit(1)

    filepath = sys.argv[1]
    phases = load_csv(filepath)

    print("=" * 72)
    print("  Phase 1a Asymmetric Clamp - Test Analysis")
    print(f"  Data file: {filepath}")
    print("=" * 72)

    phase_order = ["BASELINE", "DEPARTURE", "ARRIVAL", "FINAL_DEPARTURE"]

    for phase_name in phase_order:
        if phase_name not in phases:
            continue

        rows = phases[phase_name]
        s = phase_stats(rows)

        print(f"\n{'─'*72}")
        print(f"  Phase: {phase_name}")
        print(f"  Duration: {s['duration_s']:.0f}s ({s['duration_s']/60:.1f} min), {s['samples']} samples")
        print(f"{'─'*72}")
        print(f"  Share Difficulty:")
        print(f"    Start: {s['diff_start']:.8f}")
        print(f"    End:   {s['diff_end']:.8f}")
        print(f"    Min:   {s['diff_min']:.8f}")
        print(f"    Max:   {s['diff_max']:.8f}")
        print(f"    Avg:   {s['diff_avg']:.8f}")
        if s['diff_start'] > 0:
            change = (s['diff_end'] - s['diff_start']) / s['diff_start'] * 100
            print(f"    Change: {change:+.1f}%")

        print(f"  Hash Rates (avg kH/s):")
        print(f"    Pool:    {s['pool_hr_avg']:.0f}")
        print(f"    Hopper:  {s['hopper_hr_avg']:.0f}")
        print(f"    Anchor:  {s['anchor_hr_avg']:.0f}")
        print(f"    CPU:     {s['cpu_hr_avg']:.1f}")

        print(f"  Payouts (end of phase, tLTC):")
        print(f"    Hopper:  {s['payout_hopper_end']:.6f}")
        print(f"    Anchor:  {s['payout_anchor_end']:.6f}")
        print(f"    CPU:     {s['payout_cpu_end']:.6f}")

    # Recovery analysis
    for phase_name in ["DEPARTURE", "FINAL_DEPARTURE"]:
        if phase_name not in phases:
            continue
        diffs, recovery_time = compute_recovery_speed(phases[phase_name])
        print(f"\n{'─'*72}")
        print(f"  RECOVERY ANALYSIS: {phase_name}")
        print(f"{'─'*72}")
        if diffs:
            start_diff = diffs[0][1]
            end_diff = diffs[-1][1]
            print(f"  Difficulty: {start_diff:.8f} -> {end_diff:.8f}")
            if start_diff > 0:
                ratio = end_diff / start_diff
                print(f"  Ratio: {ratio:.3f}x ({(1-ratio)*100:.1f}% reduction)")
            if recovery_time is not None:
                print(f"  Time to 50% reduction: {recovery_time:.0f}s")
                # Estimate shares (SHARE_PERIOD=4s for testnet)
                est_shares = recovery_time / 4
                print(f"  Estimated shares for 50% reduction: ~{est_shares:.0f}")
                print(f"  (Symmetric 10% would need ~7 shares = ~28s)")
                print(f"  (Asymmetric 40% should need ~2 shares = ~8s)")
            else:
                print(f"  Difficulty did not reach 50% of start value")

        # Show difficulty trajectory
        if diffs:
            print(f"\n  Difficulty trajectory (sampled every poll interval):")
            for i, (t, d) in enumerate(diffs[:20]):
                bar_len = int(d / diffs[0][1] * 40) if diffs[0][1] > 0 else 0
                bar = '█' * bar_len
                print(f"    {t:6.0f}s  {d:.8f}  {bar}")
            if len(diffs) > 20:
                print(f"    ... ({len(diffs)-20} more samples)")

    # Hopper advantage analysis
    if "BASELINE" in phases and "ARRIVAL" in phases:
        print(f"\n{'─'*72}")
        print(f"  HOPPER ADVANTAGE ANALYSIS")
        print(f"{'─'*72}")
        baseline = phase_stats(phases["BASELINE"])
        arrival = phase_stats(phases["ARRIVAL"])

        if baseline['diff_avg'] > 0 and arrival['diff_start'] > 0:
            discount = (1 - arrival['diff_start'] / baseline['diff_avg']) * 100
            print(f"  Baseline avg difficulty:  {baseline['diff_avg']:.8f}")
            print(f"  Arrival start difficulty: {arrival['diff_start']:.8f}")
            print(f"  Hopper gets {discount:.1f}% easier shares at arrival")

            if arrival['hopper_hr_avg'] > 0 and baseline['hopper_hr_avg'] > 0:
                # Fair share based on hash rate
                hopper_frac = arrival['hopper_hr_avg'] / (arrival['pool_hr_avg'] or 1)
                print(f"  Hopper hash fraction during arrival: {hopper_frac*100:.1f}%")

    print(f"\n{'='*72}")
    print(f"  Analysis complete")
    print(f"{'='*72}")


if __name__ == "__main__":
    main()
