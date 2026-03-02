#!/usr/bin/env python3
"""
Phase 2a Exponential PPLNS Decay — Payout Efficiency Analysis
=============================================================
Computes hopper vs anchor payout-per-hash efficiency ratios
across all test phases, and compares with Phase 1a baseline.
"""

import csv
import sys
from collections import defaultdict

def load_csv(path):
    rows = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
    return rows

def analyze_phase(rows, phase_name):
    """Compute stats for a single phase."""
    phase_rows = [r for r in rows if r['phase'] == phase_name and r['node'] == 'node29']
    if not phase_rows:
        return None

    # Take start/end snapshots
    start = phase_rows[0]
    end = phase_rows[-1]

    # Average hash rates over the phase
    hopper_hrs = [float(r['hr_hopper_khs']) for r in phase_rows]
    anchor_hrs = [float(r['hr_anchor_khs']) for r in phase_rows]
    cpu_hrs = [float(r['hr_honest_cpu_khs']) for r in phase_rows]

    avg_hopper_hr = sum(hopper_hrs) / len(hopper_hrs) if hopper_hrs else 0
    avg_anchor_hr = sum(anchor_hrs) / len(anchor_hrs) if anchor_hrs else 0
    avg_cpu_hr = sum(cpu_hrs) / len(cpu_hrs) if cpu_hrs else 0

    # Payout snapshots (what each miner would get if block found NOW)
    payout_hopper_start = float(start['payout_hopper'])
    payout_anchor_start = float(start['payout_anchor'])
    payout_hopper_end = float(end['payout_hopper'])
    payout_anchor_end = float(end['payout_anchor'])

    # Difficulty trajectory
    diffs = [float(r['share_difficulty']) for r in phase_rows]
    diff_start = diffs[0]
    diff_end = diffs[-1]

    # Payout efficiency (payout per kH/s of hash rate)
    total_payout_end = payout_hopper_end + payout_anchor_end + float(end.get('payout_honest_cpu', 0)) + float(end.get('payout_old', 0))
    
    # Payout fraction
    hopper_frac = payout_hopper_end / total_payout_end if total_payout_end > 0 else 0
    anchor_frac = payout_anchor_end / total_payout_end if total_payout_end > 0 else 0

    # Hash fraction during this phase
    total_hr = avg_hopper_hr + avg_anchor_hr + avg_cpu_hr
    hopper_hr_frac = avg_hopper_hr / total_hr if total_hr > 0 else 0
    anchor_hr_frac = avg_anchor_hr / total_hr if total_hr > 0 else 0

    # Payout Efficiency Ratio (PER): payout_fraction / hash_fraction
    # A PER of 1.0 means fair. > 1.0 means overpaid. < 1.0 means underpaid.
    hopper_per = hopper_frac / hopper_hr_frac if hopper_hr_frac > 0 else float('inf')
    anchor_per = anchor_frac / anchor_hr_frac if anchor_hr_frac > 0 else float('inf')

    # Hopper Advantage Ratio (HAR): hopper_PER / anchor_PER
    har = hopper_per / anchor_per if anchor_per > 0 else float('inf')

    return {
        'phase': phase_name,
        'samples': len(phase_rows),
        'avg_hopper_hr': avg_hopper_hr,
        'avg_anchor_hr': avg_anchor_hr,
        'avg_cpu_hr': avg_cpu_hr,
        'payout_hopper': payout_hopper_end,
        'payout_anchor': payout_anchor_end,
        'hopper_frac': hopper_frac,
        'anchor_frac': anchor_frac,
        'hopper_hr_frac': hopper_hr_frac,
        'anchor_hr_frac': anchor_hr_frac,
        'hopper_per': hopper_per,
        'anchor_per': anchor_per,
        'har': har,
        'diff_start': diff_start,
        'diff_end': diff_end,
        'total_payout': total_payout_end,
    }

def print_analysis(stats):
    if stats is None:
        return
    print(f"\n{'─'*70}")
    print(f"  Phase: {stats['phase']}  ({stats['samples']} samples)")
    print(f"{'─'*70}")
    print(f"  Avg Hash Rates:  hopper={stats['avg_hopper_hr']:.0f} kH/s  anchor={stats['avg_anchor_hr']:.0f} kH/s  cpu={stats['avg_cpu_hr']:.1f} kH/s")
    print(f"  Hash Fractions:  hopper={stats['hopper_hr_frac']*100:.1f}%  anchor={stats['anchor_hr_frac']*100:.1f}%")
    print(f"  End Payouts:     hopper={stats['payout_hopper']:.6f}  anchor={stats['payout_anchor']:.6f}  total={stats['total_payout']:.6f} tLTC")
    print(f"  Payout Frac:     hopper={stats['hopper_frac']*100:.1f}%  anchor={stats['anchor_frac']*100:.1f}%")
    print(f"  Payout Effic:    hopper PER={stats['hopper_per']:.3f}  anchor PER={stats['anchor_per']:.3f}")
    print(f"  ▶ Hopper Advantage Ratio (HAR): {stats['har']:.3f}×")
    print(f"  Difficulty:      {stats['diff_start']:.8f} → {stats['diff_end']:.8f} ({(stats['diff_end']/stats['diff_start']-1)*100:+.1f}%)")

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 phase2a_analyze.py <csv_file>")
        sys.exit(1)
    
    rows = load_csv(sys.argv[1])
    print("=" * 70)
    print("  Phase 2a: Exponential PPLNS Decay — Payout Efficiency Analysis")
    print(f"  Data: {sys.argv[1]} ({len(rows)} rows)")
    print("=" * 70)

    phases = ['BASELINE', 'DEPARTURE', 'ARRIVAL', 'FINAL_DEPARTURE']
    results = {}
    for phase in phases:
        stats = analyze_phase(rows, phase)
        results[phase] = stats
        print_analysis(stats)

    # Summary comparison
    print(f"\n{'='*70}")
    print("  COMPARISON: Phase 2a vs Phase 1a (no defense)")
    print(f"{'='*70}")
    
    print(f"\n  Hopper Advantage Ratio (HAR) = hopper_payout_efficiency / anchor_payout_efficiency")
    print(f"  HAR=1.0 means fair. HAR>1.0 means hopper is overpaid. HAR<1.0 means hopper is underpaid.")
    print()
    print(f"  {'Phase':<20} {'Phase 1a (flat PPLNS)':>20} {'Phase 2a (decay)':>20} {'Improvement':>15}")
    print(f"  {'─'*20} {'─'*20} {'─'*20} {'─'*15}")
    
    # Phase 1a baseline values (from PHASE1A_TEST_REPORT.md)
    phase1a_har = {
        'BASELINE': 0.92,     # roughly fair
        'DEPARTURE': 0.35,    # hopper underpaid (left)
        'ARRIVAL': 5.27,      # HOPPER MASSIVELY OVERPAID (the bug)
        'FINAL_DEPARTURE': 0.35,
    }
    
    for phase in phases:
        if results[phase]:
            p2a = results[phase]['har']
            p1a = phase1a_har.get(phase, 1.0)
            improvement = (p1a - p2a) / (p1a - 1.0) * 100 if p1a != 1.0 else 0
            arrow = "✓" if abs(p2a - 1.0) < abs(p1a - 1.0) else "✗"
            print(f"  {phase:<20} {p1a:>20.3f}× {p2a:>20.3f}× {arrow:>15}")
    
    print()
    
    arrival = results.get('ARRIVAL')
    if arrival:
        print(f"  KEY METRIC: Arrival HAR")
        print(f"    Phase 1a (flat PPLNS):     5.27× (hopper gets 5.27× payout per hash)")
        print(f"    Phase 2a (decay PPLNS):    {arrival['har']:.3f}× (hopper gets {arrival['har']:.2f}× payout per hash)")
        reduction = (1 - arrival['har'] / 5.27) * 100
        print(f"    Reduction:                 {reduction:.1f}%")
        print()
        if arrival['har'] < 2.0:
            print(f"  ✓ Phase 2a SIGNIFICANTLY reduces hopper advantage at arrival")
        elif arrival['har'] < 3.0:
            print(f"  ~ Phase 2a moderately reduces hopper advantage at arrival")
        else:
            print(f"  ✗ Phase 2a provides limited improvement at arrival")

    print(f"\n{'='*70}")

if __name__ == '__main__':
    main()
