#!/usr/bin/env python3
"""
L1 Hopper Controller - Enable/disable L1 miners via iptables IP blocking.

Uses iptables rules on the p2pool nodes to DROP packets from hopper L1 IPs,
effectively disconnecting them from the pool.  Requires passwordless sudo
for /usr/sbin/iptables on nodes 29 and 31.

Usage:
    python3 scripts/l1_control.py status           # Show all L1 status + iptables rules
    python3 scripts/l1_control.py disable-hoppers   # Block alfa+charlie on both nodes
    python3 scripts/l1_control.py enable-hoppers    # Unblock alfa+charlie on both nodes
    python3 scripts/l1_control.py verify-block      # Check if iptables rules are active
"""

import json
import os
import socket
import subprocess
import sys
import time


L1_MINERS = {
    "alfa":    {"ip": "192.168.86.20",  "port": 4028, "role": "hopper",  "node": "31"},
    "bravo":   {"ip": "192.168.86.22",  "port": 4028, "role": "anchor",  "node": "29"},
    "charlie": {"ip": "192.168.86.249", "port": 4028, "role": "hopper",  "node": "29"},
}

# Hopper IPs to block/unblock
HOPPER_IPS = ["192.168.86.20", "192.168.86.249"]  # alfa, charlie

# P2pool nodes where we add iptables rules (block on BOTH to prevent reconnect)
P2POOL_NODES = [
    {"name": "node29", "ip": "192.168.86.29", "user": "user0"},
    {"name": "node31", "ip": "192.168.86.31", "user": "user0"},
]

# P2pool stratum port (testnet)
STRATUM_PORT = 19327


def ssh_cmd(node_ip, user, cmd, timeout=10):
    """Run a command on a remote node via SSH."""
    full_cmd = ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no",
                f"{user}@{node_ip}", cmd]
    try:
        result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout", 1
    except Exception as e:
        return "", str(e), 1


def block_ip_on_node(node, miner_ip):
    """Add iptables REJECT rules (INPUT+OUTPUT) for miner_ip on a p2pool node."""
    results = []
    for chain, src_flag, port_flag in [("INPUT", f"-s {miner_ip}", "--dport"),
                                        ("OUTPUT", f"-d {miner_ip}", "--sport")]:
        check_cmd = f"sudo /usr/sbin/iptables -C {chain} {src_flag} -p tcp {port_flag} {STRATUM_PORT} -j REJECT --reject-with tcp-reset 2>/dev/null"
        out, err, rc = ssh_cmd(node["ip"], node["user"], check_cmd)
        if rc == 0:
            results.append(f"{chain}:exists")
            continue
        add_cmd = f"sudo /usr/sbin/iptables -I {chain} {src_flag} -p tcp {port_flag} {STRATUM_PORT} -j REJECT --reject-with tcp-reset"
        out, err, rc = ssh_cmd(node["ip"], node["user"], add_cmd)
        if rc == 0:
            results.append(f"{chain}:added")
        else:
            results.append(f"{chain}:FAILED({err})")
    return f"  {node['name']}: BLOCKED {miner_ip} [{', '.join(results)}]"


def unblock_ip_on_node(node, miner_ip):
    """Remove iptables REJECT rules (INPUT+OUTPUT) for miner_ip on a p2pool node."""
    removed = 0
    for chain, src_flag, port_flag in [("INPUT", f"-s {miner_ip}", "--dport"),
                                        ("OUTPUT", f"-d {miner_ip}", "--sport")]:
        for action in ["REJECT --reject-with tcp-reset", "DROP"]:
            for _ in range(5):
                rm_cmd = f"sudo /usr/sbin/iptables -D {chain} {src_flag} -p tcp {port_flag} {STRATUM_PORT} -j {action} 2>/dev/null"
                out, err, rc = ssh_cmd(node["ip"], node["user"], rm_cmd)
                if rc == 0:
                    removed += 1
                else:
                    break
    if removed > 0:
        return f"  {node['name']}: UNBLOCKED {miner_ip} ({removed} rule(s) removed)"
    else:
        return f"  {node['name']}: {miner_ip} was not blocked"


def list_blocks_on_node(node):
    """List current iptables REJECT/DROP rules for hopper IPs on a node."""
    lines = []
    for chain in ["INPUT", "OUTPUT"]:
        cmd = f"sudo /usr/sbin/iptables -L {chain} -n --line-numbers 2>/dev/null | grep -E 'REJECT|DROP'"
        out, err, rc = ssh_cmd(node["ip"], node["user"], cmd)
        if out:
            for line in out.split('\n'):
                lines.append(f"{chain}: {line}")
    return '\n'.join(lines) if lines else ''


def cgminer_api(ip, port, command, parameter=None, timeout=5):
    """Send a JSON command to cgminer API and return parsed response."""
    msg = {"command": command}
    if parameter is not None:
        msg["parameter"] = parameter
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((ip, port))
        s.sendall(json.dumps(msg).encode())
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
        return json.loads(data.rstrip(b'\x00').decode())
    except Exception as e:
        return {"error": str(e)}


def get_status(name, info):
    """Get hash rate and pool info from an L1."""
    summary = cgminer_api(info["ip"], info["port"], "summary")
    pools = cgminer_api(info["ip"], info["port"], "pools")

    if "error" in summary:
        return f"  {name} ({info['ip']}): ERROR - {summary['error']}"

    s = summary.get("SUMMARY", [{}])[0]
    ghs = s.get("GHS 5s", 0)

    active_pool = "none"
    if "POOLS" in pools:
        for p in pools["POOLS"]:
            if p.get("Stratum Active"):
                active_pool = f"{p['User']} (Pool {p['POOL']})"
                break

    return f"  {name:8s} ({info['ip']:15s}): {ghs:.2f} GH/s  pool={active_pool}  role={info['role']}"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1].lower()

    if action == "status":
        print("L1 Miner Status (cgminer API):")
        print("-" * 80)
        for name, info in L1_MINERS.items():
            print(get_status(name, info))
        print()
        print("iptables DROP rules (p2pool nodes):")
        print("-" * 80)
        for node in P2POOL_NODES:
            blocks = list_blocks_on_node(node)
            if blocks:
                print(f"  {node['name']} ({node['ip']}):")
                for line in blocks.split('\n'):
                    print(f"    {line}")
            else:
                print(f"  {node['name']} ({node['ip']}): no blocks")

    elif action == "disable-hoppers":
        print(f">>> BLOCKING hopper L1s via iptables (port {STRATUM_PORT})...")
        for node in P2POOL_NODES:
            for hip in HOPPER_IPS:
                print(block_ip_on_node(node, hip))
        print(f">>> Hopper L1s blocked at {time.strftime('%H:%M:%S')}")
        print("  (stratum connections will timeout in ~30s)")

    elif action == "enable-hoppers":
        print(f">>> UNBLOCKING hopper L1s via iptables...")
        for node in P2POOL_NODES:
            for hip in HOPPER_IPS:
                print(unblock_ip_on_node(node, hip))
        print(f">>> Hopper L1s unblocked at {time.strftime('%H:%M:%S')}")
        print("  (L1s will reconnect within ~30-60s)")

    elif action == "verify-block":
        print("Verifying iptables rules on p2pool nodes:")
        for node in P2POOL_NODES:
            blocks = list_blocks_on_node(node)
            if blocks:
                print(f"  {node['name']}: BLOCKING active")
                for line in blocks.split('\n'):
                    print(f"    {line}")
            else:
                print(f"  {node['name']}: no blocks (hoppers can connect)")

    elif action == "disable-all":
        print(f">>> BLOCKING ALL L1s via iptables (port {STRATUM_PORT})...")
        all_ips = [info["ip"] for info in L1_MINERS.values()]
        for node in P2POOL_NODES:
            for ip in all_ips:
                print(block_ip_on_node(node, ip))
            
    elif action == "enable-all":
        print(f">>> UNBLOCKING ALL L1s via iptables...")
        all_ips = [info["ip"] for info in L1_MINERS.values()]
        for node in P2POOL_NODES:
            for ip in all_ips:
                print(unblock_ip_on_node(node, ip))

    else:
        print(f"Unknown action: {action}")
        print("Actions: status, disable-hoppers, enable-hoppers, verify-block, disable-all, enable-all")
        sys.exit(1)


if __name__ == "__main__":
    main()
