#!/usr/bin/env python3
"""
Phase 1a Asymmetric Clamp - Automated Hopper Test
==================================================
Fully automated test that:
  1. Records baseline (all miners active)
  2. Disables hopper L1s (semiwhale departure)
  3. Monitors recovery (asymmetric clamp in action)
  4. Re-enables hopper L1s (hop-in)
  5. Monitors difficulty ramp-up
  6. Disables again (second departure)
  7. Generates analysis CSV

Usage:
    python3 scripts/hopper_test.py [--baseline-min 5] [--depart-min 8] [--arrive-min 5]
"""

import argparse
import csv
import json
import os
import socket
import sys
import time
import urllib.request
from datetime import datetime

# ── Addresses ──────────────────────────────────────────────────────
ADDR_HONEST_CPU = "tltc1qktdaszj95rqhzw92jxgrpv295kzs6nct3k45x7"
ADDR_ANCHOR     = "tltc1qw3nfd6xwv8ecwz0xq4expjgdlnsv8qmwvxqyqh"
ADDR_HOPPER     = "tltc1qaj0tyr6zckzavhq984kzz6p96djsvcv9wahfc3"
ADDR_OLD        = "QZQGeMoG3MaLmWwRTcbMwuxYenkHE2zhUN"

# ── P2Pool Nodes ───────────────────────────────────────────────────
NODES = [
    ("node29", "http://192.168.86.29:19327"),
    ("node31", "http://192.168.86.31:19327"),
]

# ── L1 Miners (hopper role) ────────────────────────────────────────
HOPPER_IPS = ["192.168.86.20", "192.168.86.249"]  # alfa, charlie

# P2pool nodes where we add iptables rules
P2POOL_SSH = [
    {"name": "node29", "ip": "192.168.86.29", "user": "user0"},
    {"name": "node31", "ip": "192.168.86.31", "user": "user0"},
]

STRATUM_PORT = 19327


def ssh_cmd(node_ip, user, cmd, timeout=10):
    """Run a command on a remote node via SSH."""
    import subprocess
    full_cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                f"{user}@{node_ip}", cmd]
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), 1


def disable_hoppers():
    """Block hopper L1 IPs via iptables REJECT on both p2pool nodes (bidirectional)."""
    for node in P2POOL_SSH:
        for hip in HOPPER_IPS:
            # INPUT: block share submissions from L1
            check = f"sudo /usr/sbin/iptables -C INPUT -s {hip} -p tcp --dport {STRATUM_PORT} -j REJECT --reject-with tcp-reset 2>/dev/null"
            _, _, rc = ssh_cmd(node["ip"], node["user"], check)
            if rc != 0:
                add = f"sudo /usr/sbin/iptables -I INPUT -s {hip} -p tcp --dport {STRATUM_PORT} -j REJECT --reject-with tcp-reset"
                ssh_cmd(node["ip"], node["user"], add)
            # OUTPUT: block work notifications to L1
            check_out = f"sudo /usr/sbin/iptables -C OUTPUT -d {hip} -p tcp --sport {STRATUM_PORT} -j REJECT --reject-with tcp-reset 2>/dev/null"
            _, _, rc = ssh_cmd(node["ip"], node["user"], check_out)
            if rc != 0:
                add_out = f"sudo /usr/sbin/iptables -I OUTPUT -d {hip} -p tcp --sport {STRATUM_PORT} -j REJECT --reject-with tcp-reset"
                ssh_cmd(node["ip"], node["user"], add_out)
            print(f"    {node['name']}: BLOCKED {hip} [INPUT+OUTPUT]")


def enable_hoppers():
    """Remove iptables REJECT rules for hopper L1 IPs on both nodes (INPUT + OUTPUT)."""
    for node in P2POOL_SSH:
        for hip in HOPPER_IPS:
            removed = 0
            for chain in ["INPUT", "OUTPUT"]:
                port_flag = "--dport" if chain == "INPUT" else "--sport"
                src_flag = f"-s {hip}" if chain == "INPUT" else f"-d {hip}"
                for action in ["REJECT --reject-with tcp-reset", "DROP"]:
                    for _ in range(5):
                        rm = f"sudo /usr/sbin/iptables -D {chain} {src_flag} -p tcp {port_flag} {STRATUM_PORT} -j {action} 2>/dev/null"
                        _, _, rc = ssh_cmd(node["ip"], node["user"], rm)
                        if rc == 0:
                            removed += 1
                        else:
                            break
            if removed:
                print(f"    {node['name']}: UNBLOCKED {hip} ({removed} rule(s))")
            else:
                print(f"    {node['name']}: {hip} was not blocked")


def verify_blocks():
    """Check which iptables blocking rules are active."""
    for node in P2POOL_SSH:
        for chain in ["INPUT", "OUTPUT"]:
            cmd = f"sudo /usr/sbin/iptables -L {chain} -n 2>/dev/null | grep -E 'REJECT|DROP'"
            out, _, rc = ssh_cmd(node["ip"], node["user"], cmd)
            if out:
                print(f"    {node['name']} {chain}: {out}")
            else:
                print(f"    {node['name']} {chain}: clear")


def get_hopper_hashrates():
    """Get current GH/s from hopper L1s via cgminer API."""
    total = 0
    for hip in HOPPER_IPS:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(5)
            s.connect((hip, 4028))
            s.sendall(json.dumps({"command": "summary"}).encode())
            data = b""
            while True:
                try:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                except socket.timeout:
                    break
            s.close()
            r = json.loads(data.rstrip(b'\x00').decode())
            total += r.get("SUMMARY", [{}])[0].get("GHS 5s", 0)
        except Exception:
            pass
    return total


def fetch_json(url, timeout=5):
    """Fetch JSON from URL."""
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(resp.read().decode())
    except Exception:
        return None


def classify_address(addr):
    """Map address.worker to role."""
    base = addr.split('.')[0]
    if base == ADDR_HONEST_CPU:
        return "honest_cpu"
    elif base == ADDR_ANCHOR:
        return "anchor"
    elif base == ADDR_HOPPER:
        return "hopper"
    elif base == ADDR_OLD:
        return "old_combined"
    return "unknown"


def aggregate_by_role(hash_rates):
    """Aggregate hash rates by role."""
    roles = {"honest_cpu": 0, "anchor": 0, "hopper": 0, "old_combined": 0}
    for worker, rate in (hash_rates or {}).items():
        role = classify_address(worker)
        if role in roles:
            roles[role] += rate
    return roles


def aggregate_payouts(payouts):
    """Aggregate payouts by role."""
    roles = {"honest_cpu": 0.0, "anchor": 0.0, "hopper": 0.0, "old_combined": 0.0}
    for addr, prop in (payouts or {}).items():
        role = classify_address(addr)
        if role in roles:
            roles[role] += prop
    return roles


def sample_all_nodes(phase, elapsed, writer, start_time):
    """Take one sample from all nodes and write to CSV. Returns last stats dict."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    last_stats = None

    for node_name, base_url in NODES:
        stats = fetch_json(base_url + "/local_stats")
        payouts = fetch_json(base_url + "/current_payouts")
        gstats = fetch_json(base_url + "/global_stats")
        if not stats:
            continue
        last_stats = stats

        roles_hr = aggregate_by_role(stats.get("miner_hash_rates", {}))
        roles_pay = aggregate_payouts(payouts)

        pool_hr = gstats.get("pool_nonstale_hash_rate", 0) if gstats else 0
        diffs = list(stats.get("miner_last_difficulties", {}).values())
        avg_diff = sum(diffs) / len(diffs) if diffs else 0
        shares = stats.get("shares", {})

        writer.writerow([
            now, f"{elapsed:.0f}", phase,
            f"{pool_hr/1000:.1f}", f"{avg_diff:.8f}",
            f"{roles_hr['honest_cpu']/1000:.1f}",
            f"{roles_hr['anchor']/1000:.1f}",
            f"{roles_hr['hopper']/1000:.1f}",
            f"{roles_hr['old_combined']/1000:.1f}",
            f"{roles_pay['honest_cpu']:.6f}",
            f"{roles_pay['anchor']:.6f}",
            f"{roles_pay['hopper']:.6f}",
            f"{roles_pay['old_combined']:.6f}",
            shares.get("total", 0),
            shares.get("orphan", 0),
            shares.get("dead", 0),
            node_name
        ])
    return last_stats


def run_phase(phase_name, duration_sec, writer, start_time, poll_interval=5):
    """Run a test phase for the given duration, sampling at poll_interval."""
    phase_start = time.time()
    print(f"\n{'='*72}")
    print(f"  PHASE: {phase_name}  (duration: {duration_sec}s = {duration_sec/60:.1f}min)")
    print(f"{'='*72}")

    while True:
        elapsed_total = time.time() - start_time
        elapsed_phase = time.time() - phase_start

        if elapsed_phase >= duration_sec:
            break

        stats = sample_all_nodes(phase_name, elapsed_total, writer, start_time)

        # Print status line
        if stats:
            roles_hr = aggregate_by_role(stats.get("miner_hash_rates", {}))
            diffs = list(stats.get("miner_last_difficulties", {}).values())
            avg_diff = sum(diffs) / len(diffs) if diffs else 0
            remaining = duration_sec - elapsed_phase

            print(f"\r  [{elapsed_total:5.0f}s] diff={avg_diff:.6f} "
                  f"cpu={roles_hr['honest_cpu']/1000:.0f} "
                  f"anchor={roles_hr['anchor']/1000:.0f} "
                  f"hopper={roles_hr['hopper']/1000:.0f} "
                  f"old={roles_hr['old_combined']/1000:.0f} kH/s "
                  f"({remaining:.0f}s left)   ",
                  end='', flush=True)

        time.sleep(poll_interval)

    print(f"\n  Phase {phase_name} complete.")


def main():
    parser = argparse.ArgumentParser(description="Automated hopper test")
    parser.add_argument("--baseline-min", type=float, default=5,
                        help="Baseline phase duration in minutes (default: 5)")
    parser.add_argument("--depart-min", type=float, default=8,
                        help="Departure phase duration in minutes (default: 8)")
    parser.add_argument("--arrive-min", type=float, default=5,
                        help="Arrival phase duration in minutes (default: 5)")
    parser.add_argument("--final-min", type=float, default=8,
                        help="Final departure phase in minutes (default: 8)")
    parser.add_argument("--poll", type=int, default=5,
                        help="Poll interval seconds (default: 5)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output CSV (default: auto-timestamped)")
    args = parser.parse_args()

    if args.output is None:
        args.output = f"hopper_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    print("=" * 72)
    print("  Phase 1a Asymmetric Clamp - Automated Hopper Test")
    print("=" * 72)
    print(f"  Output:    {args.output}")
    print(f"  Baseline:  {args.baseline_min} min")
    print(f"  Departure: {args.depart_min} min")
    print(f"  Arrival:   {args.arrive_min} min")
    print(f"  Final:     {args.final_min} min")
    print(f"  Poll:      {args.poll}s")
    print()

    # Verify all L1s are up
    print("Checking L1 miners...")
    hopper_ghs = get_hopper_hashrates()
    print(f"  Hopper L1s total: {hopper_ghs:.2f} GH/s")
    if hopper_ghs < 0.5:
        print("  WARNING: Hopper L1s seem too low. Are they mining?")

    # Verify p2pool nodes respond
    print("Checking p2pool nodes...")
    for name, url in NODES:
        s = fetch_json(url + "/local_stats")
        print(f"  {name}: {'OK' if s else 'FAIL'}")

    # Open CSV
    csvfile = open(args.output, 'w', newline='')
    writer = csv.writer(csvfile)
    writer.writerow([
        "timestamp", "elapsed_s", "phase",
        "pool_hashrate_khs", "share_difficulty",
        "hr_honest_cpu_khs", "hr_anchor_khs", "hr_hopper_khs", "hr_old_khs",
        "payout_honest_cpu", "payout_anchor", "payout_hopper", "payout_old",
        "shares_total", "shares_orphan", "shares_dead",
        "node"
    ])

    start_time = time.time()

    try:
        # ── Phase 1: BASELINE ──────────────────────────────────────
        # All miners active, record normal operation
        print("\n>>> All miners should be active. Starting baseline measurement.")
        enable_hoppers()  # Ensure hoppers are on
        time.sleep(3)
        run_phase("BASELINE", args.baseline_min * 60, writer, start_time, args.poll)
        csvfile.flush()

        # ── Phase 2: DEPARTURE ─────────────────────────────────────
        # Block hopper L1 IPs = semiwhale leaves the pool
        print("\n>>> BLOCKING hopper L1s via iptables (semiwhale departs)...")
        disable_hoppers()
        print("  Verifying iptables rules:")
        verify_blocks()
        print("  (stratum connections will timeout in ~30s)")
        time.sleep(5)
        run_phase("DEPARTURE", args.depart_min * 60, writer, start_time, args.poll)
        csvfile.flush()

        # ── Phase 3: ARRIVAL ───────────────────────────────────────
        # Unblock hopper L1 IPs = hopper jumps in for cheap shares
        print("\n>>> UNBLOCKING hopper L1s via iptables (hopper arrives)...")
        enable_hoppers()
        print("  Verifying iptables rules:")
        verify_blocks()
        print("  (L1s will reconnect in ~30-60s)")
        time.sleep(5)
        run_phase("ARRIVAL", args.arrive_min * 60, writer, start_time, args.poll)
        csvfile.flush()

        # ── Phase 4: FINAL DEPARTURE ──────────────────────────────
        # Block again to measure 2nd recovery
        print("\n>>> BLOCKING hopper L1s via iptables (final departure)...")
        disable_hoppers()
        print("  Verifying iptables rules:")
        verify_blocks()
        time.sleep(5)
        run_phase("FINAL_DEPARTURE", args.final_min * 60, writer, start_time, args.poll)
        csvfile.flush()

        # ── Cleanup ────────────────────────────────────────────────
        print("\n>>> Test complete. Unblocking hopper L1s...")
        enable_hoppers()

    except KeyboardInterrupt:
        print("\n\n>>> Test interrupted. Unblocking hoppers...")
        enable_hoppers()

    finally:
        csvfile.close()
        total_time = time.time() - start_time
        print(f"\n{'='*72}")
        print(f"  Test completed in {total_time/60:.1f} minutes")
        print(f"  Data saved to: {args.output}")
        print(f"  Analyze with: python3 scripts/hopper_analyze.py {args.output}")
        print(f"{'='*72}")


if __name__ == "__main__":
    main()
