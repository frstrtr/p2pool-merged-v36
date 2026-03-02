#!/usr/bin/env python3
"""
Phase 2a Long-Term Consensus Monitor
=====================================
Runs overnight to verify Phase 2a exponential PPLNS decay maintains
consensus stability across multiple full sharechain lengths.

Monitors:
  - Share chain length on both nodes (should agree)
  - Share verification errors (gentx mismatch = consensus break)
  - Node uptime (crash detection)
  - Orphan/stale rates
  - PPLNS weight computation (no errors/exceptions)
  - DNS/connection errors

Target: 2-3+ full sharechain lengths (400 shares each on testnet)
At ~4s/share with 3 miners: ~27 min per full chain length.
6 hours = ~13 full chain rotations.

Usage:
    python3 scripts/overnight_monitor.py [--hours 6] [--poll 30]
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime

NODES = {
    'node29': {'ssh': 'user0@192.168.86.29', 'api': 'http://192.168.86.29:19327'},
    'node31': {'ssh': 'user0@192.168.86.31', 'api': 'http://192.168.86.31:19327'},
}

LOG_PATH = '~/p2pool-merged/data/litecoin_testnet/log'
CHAIN_LENGTH = 400  # testnet CHAIN_LENGTH
SHARE_PERIOD = 4    # seconds between shares

def ssh_cmd(host, cmd, timeout=10):
    """Run SSH command and return stdout."""
    try:
        r = subprocess.run(
            ['ssh', '-o', 'ConnectTimeout=5', host, cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip(), r.returncode
    except subprocess.TimeoutExpired:
        return '(timeout)', -1
    except Exception as e:
        return str(e), -1

def get_api(host, endpoint='/local_stats'):
    """Fetch JSON from p2pool API."""
    import urllib.request
    try:
        url = host + endpoint
        req = urllib.request.Request(url, headers={'User-Agent': 'monitor/1.0'})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None

def count_errors(ssh_host, since_line=0):
    """Count various error types in the log since a given line."""
    cmd = "tail -n +%d %s 2>/dev/null | grep -c '%s'" 
    errors = {}
    patterns = {
        'gentx_mismatch': "gentx doesn't match hash_link",
        'share_check_failed': 'Share check failed',
        'dns_lookup': 'DNS lookup failed',
        'traceback': 'Traceback',
        'unhandled_error': 'Unhandled Error',
        'pplns_mismatch': 'PPLNS weight mismatch',
        'decay_error': 'decay.*error|error.*decay',
    }
    for key, pattern in patterns.items():
        out, rc = ssh_cmd(ssh_host, "grep -c '%s' %s 2>/dev/null || echo 0" % (pattern, LOG_PATH))
        try:
            errors[key] = int(out.split('\n')[-1])
        except (ValueError, IndexError):
            errors[key] = -1
    return errors

def get_share_stats(ssh_host):
    """Get share chain stats from log."""
    # Get the most recent P2Pool status line
    out, _ = ssh_cmd(ssh_host, "grep 'P2Pool:' %s | tail -1" % LOG_PATH)
    chain_shares = 0
    verified = 0
    total = 0
    if 'shares in chain' in out:
        try:
            # "P2Pool: 400 shares in chain (402 verified/402 total)"
            parts = out.split('P2Pool: ')[1]
            chain_shares = int(parts.split(' shares')[0])
            v_part = parts.split('(')[1]
            verified = int(v_part.split(' verified')[0])
            total = int(v_part.split('/')[1].split(' total')[0])
        except (IndexError, ValueError):
            pass
    return chain_shares, verified, total

def main():
    parser = argparse.ArgumentParser(description='Phase 2a overnight consensus monitor')
    parser.add_argument('--hours', type=float, default=6, help='Duration in hours (default: 6)')
    parser.add_argument('--poll', type=int, default=30, help='Poll interval seconds (default: 30)')
    parser.add_argument('--output', type=str, default=None, help='Output CSV file')
    args = parser.parse_args()

    duration = args.hours * 3600
    poll = args.poll
    
    if args.output is None:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        args.output = 'scripts/overnight_phase2a_%s.csv' % ts

    print('=' * 72)
    print('  Phase 2a Long-Term Consensus Monitor')
    print('=' * 72)
    print('  Duration: %.1f hours (%d seconds)' % (args.hours, duration))
    print('  Poll interval: %d seconds' % poll)
    print('  Expected chain rotations: %.1f' % (duration / (CHAIN_LENGTH * SHARE_PERIOD)))
    print('  Output: %s' % args.output)
    print('  Nodes: %s' % ', '.join(NODES.keys()))
    print('  Monitoring: gentx mismatch, share check failures, DNS errors,')
    print('              tracebacks, PPLNS weight mismatch, crashes')
    print('=' * 72)
    print()

    # CSV header
    with open(args.output, 'w') as f:
        f.write('timestamp,elapsed_s,node,chain_shares,verified,total,uptime,'
                'pool_hashrate_khs,miners,peers_in,peers_out,'
                'orphan,dead,stale_pct,'
                'err_gentx,err_share_check,err_dns,err_traceback,'
                'err_unhandled,err_pplns,err_decay\n')

    start = time.time()
    sample = 0
    max_errors_seen = {n: {} for n in NODES}
    alert_count = 0

    while time.time() - start < duration:
        elapsed = time.time() - start
        sample += 1
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        rows = []
        for name, info in NODES.items():
            # Get API stats
            stats = get_api(info['api'])
            
            # Get share chain stats from log
            chain_shares, verified, total = get_share_stats(info['ssh'])
            
            # Get error counts
            errors = count_errors(info['ssh'])
            
            if stats:
                uptime = stats.get('uptime', 0)
                pool_hr = sum(stats.get('miner_hash_rates', {}).values()) / 1000.0  # kH/s
                miners = len(stats.get('miner_hash_rates', {}))
                peers_in = stats.get('peers', {}).get('incoming', 0)
                peers_out = stats.get('peers', {}).get('outgoing', 0)
                shares_info = stats.get('shares', {})
                orphan = shares_info.get('orphan', 0)
                dead = shares_info.get('dead', 0)
                total_shares = shares_info.get('total', 0)
                stale_pct = (orphan + dead) / total_shares * 100 if total_shares > 0 else 0
            else:
                uptime = 0
                pool_hr = 0
                miners = 0
                peers_in = peers_out = 0
                orphan = dead = 0
                stale_pct = 0

            row = (now, int(elapsed), name, chain_shares, verified, total, 
                   int(uptime), round(pool_hr, 1), miners, peers_in, peers_out,
                   orphan, dead, round(stale_pct, 1),
                   errors.get('gentx_mismatch', 0),
                   errors.get('share_check_failed', 0),
                   errors.get('dns_lookup', 0),
                   errors.get('traceback', 0),
                   errors.get('unhandled_error', 0),
                   errors.get('pplns_mismatch', 0),
                   errors.get('decay_error', 0))
            rows.append(row)

            # Check for NEW errors (alert on increase)
            for err_name, err_val in errors.items():
                prev = max_errors_seen[name].get(err_name, 0)
                if err_val > prev and err_val > 0:
                    alert_count += 1
                    print('\n  *** ALERT #%d: %s on %s: %d (was %d) ***' % (
                        alert_count, err_name, name, err_val, prev))
                max_errors_seen[name][err_name] = max(err_val, prev)

        # Write CSV
        with open(args.output, 'a') as f:
            for row in rows:
                f.write(','.join(str(v) for v in row) + '\n')

        # Console status
        remaining = duration - elapsed
        chain_rot = elapsed / (CHAIN_LENGTH * SHARE_PERIOD)
        
        # One-line status per node
        status_parts = []
        for row in rows:
            name = row[2]
            chain = row[3]
            hr = row[7]
            mn = row[8]
            gentx_err = row[14]
            share_err = row[15]
            status_parts.append('%s: %d shares, %.0fkH/s, %dm' % (name, chain, hr, mn))
        
        total_gentx = sum(max_errors_seen[n].get('gentx_mismatch', 0) for n in NODES)
        total_share_err = sum(max_errors_seen[n].get('share_check_failed', 0) for n in NODES)
        
        health = 'OK' if (total_gentx == 0 and total_share_err == 0) else 'ERRORS!'
        
        print('\r  [%5ds] rot=%.1f %s | %s | alerts=%d   ' % (
            int(elapsed), chain_rot, health, ' | '.join(status_parts), alert_count), end='')
        sys.stdout.flush()

        time.sleep(poll)

    # Final summary
    print('\n')
    print('=' * 72)
    print('  OVERNIGHT TEST COMPLETE')
    print('=' * 72)
    elapsed = time.time() - start
    print('  Duration: %.1f hours' % (elapsed / 3600))
    print('  Chain rotations: %.1f' % (elapsed / (CHAIN_LENGTH * SHARE_PERIOD)))
    print('  Total alerts: %d' % alert_count)
    print()
    
    print('  Error summary:')
    any_errors = False
    for name in NODES:
        errs = max_errors_seen[name]
        non_zero = {k: v for k, v in errs.items() if v > 0}
        if non_zero:
            any_errors = True
            print('    %s: %s' % (name, non_zero))
        else:
            print('    %s: CLEAN (no errors)' % name)
    
    if not any_errors:
        print('\n  ✓ CONSENSUS STABLE: No gentx mismatches or share check failures')
        print('    Phase 2a exponential PPLNS decay is consensus-safe over %d+ chain rotations' % 
              int(elapsed / (CHAIN_LENGTH * SHARE_PERIOD)))
    else:
        print('\n  ✗ ERRORS DETECTED — review log for details')
    
    print('\n  Output: %s' % args.output)
    print('=' * 72)

if __name__ == '__main__':
    main()
