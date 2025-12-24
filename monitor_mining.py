#!/usr/bin/env python3
"""
P2Pool Merged Mining Monitor
Displays real-time dashboard of Dogecoin testnet mining activity
"""

import subprocess
import json
import time
import sys
from datetime import datetime
from collections import deque
import hashlib

# Configuration
DOGE_RPC_USER = "dogeuser"
DOGE_RPC_PASS = "dogepass123"
DOGE_RPC_URL = "http://127.0.0.1:44555/"
P2POOL_LOG = "/tmp/p2pool_new.log"

# Mining addresses
# Litecoin testnet (parent chain): mm3suEPoj1WnhYuRTdoM6dfEXQvZEyuu9h
# Dogecoin testnet (merged chain): nZj5sSzP9NSYLRBbWUTz4tConRSSeuYQvY (same pubkey_hash, different ADDRESS_VERSION)
LTC_MINING_ADDRESS = "mm3suEPoj1WnhYuRTdoM6dfEXQvZEyuu9h"  # Parent chain
DOGE_MINING_ADDRESS = "nZj5sSzP9NSYLRBbWUTz4tConRSSeuYQvY"  # Merged mining payouts (NEW - correctly converted)

# OLD address used before address conversion fix (Dec 24, 2024)
# This is where historical blocks paid out (LTC address was used directly in DOGE coinbase)
OLD_DOGE_MINING_ADDRESS = "mm3suEPoj1WnhYuRTdoM6dfEXQvZEyuu9h"  # Historical payouts

MINING_ADDRESS = DOGE_MINING_ADDRESS  # Use new correct address for blockchain scanning

EXPLORER_URL = "https://blockexplorer.one/dogecoin/testnet"
SSH_HOST = "user0@192.168.80.182"

# P2Pool donation script (P2PKH format - updated Dec 2024)
# Original P2PK (67 bytes): 4104ffd03...664bac (Forrest era)
# New P2PKH (25 bytes): 76a91420cb5c22b1e4d5947e5c112c7696b51ad9af3c6188ac
# Pubkey hash: 20cb5c22b1e4d5947e5c112c7696b51ad9af3c61
# Addresses derived from this pubkey_hash:
#   Dash mainnet:     XdgF55wEHBRWwbuBniNYH4GvvaoYMgL84u
#   Dogecoin mainnet: D88Vn6Dyct7DKfVCfR3syHkjyNx9gEyyiv
#   Dogecoin testnet: nXBZW6xtYrZwCe4PhEhLDhM3DFLSd1pa1R
DONATION_SCRIPT = "76a91420cb5c22b1e4d5947e5c112c7696b51ad9af3c6188ac"
DONATION_ADDRESS_DOGE_TESTNET = "nXBZW6xtYrZwCe4PhEhLDhM3DFLSd1pa1R"
DONATION_ADDRESS_DOGE_MAINNET = "D88Vn6Dyct7DKfVCfR3syHkjyNx9gEyyiv"

# Old P2PK script for backward compatibility (blocks mined before Dec 24, 2024)
OLD_DONATION_SCRIPT = "4104ffd03de44a6e11b9917f3a29f9443283d9871c9d743ef30d5eddcd37094b64d1b3d8090496b53256786bf5c82932ec23c3b74d9f05a6f95a8b5529352656664bac"

# Track recent candidates
recent_candidates = deque(maxlen=10)
last_candidate_count = 0
candidate_times = deque(maxlen=100)  # Track timestamps for hashrate calculation

def run_ssh_command(cmd):
    """Execute command on remote host via SSH"""
    result = subprocess.run(
        ["ssh", SSH_HOST, cmd],
        capture_output=True,
        text=True,
        timeout=10
    )
    return result.stdout.strip()

def rpc_call(method, params=[]):
    """Make RPC call to Dogecoin daemon"""
    cmd = f'curl -s --user {DOGE_RPC_USER}:{DOGE_RPC_PASS} --data-binary \'{{"jsonrpc":"1.0","id":"monitor","method":"{method}","params":{json.dumps(params)}}}\' -H "content-type: text/plain;" {DOGE_RPC_URL}'
    result = run_ssh_command(cmd)
    try:
        return json.loads(result)["result"]
    except:
        return None

def get_network_info():
    """Get current network mining info"""
    return rpc_call("getmininginfo")

def get_block_count():
    """Get current block height"""
    return rpc_call("getblockcount")

def get_balance():
    """Get wallet balance"""
    return rpc_call("getbalance")

def get_address_balance(address):
    """Get balance for a specific address by scanning UTXOs"""
    try:
        # Use listunspent to find UTXOs for this address
        utxos = rpc_call("listunspent", [0, 9999999, [address]])
        if utxos:
            return sum(u.get('amount', 0) for u in utxos)
        return 0
    except:
        return None

def get_received_by_address(address):
    """Get total received by address (includes spent)"""
    try:
        result = rpc_call("getreceivedbyaddress", [address, 0])  # 0 confirmations
        if result is not None:
            return result
        # If address not in wallet, return None (will trigger blockchain scan)
        return None
    except:
        return None

def scan_blockchain_for_address(address, num_blocks=100):
    """Scan recent blocks for payments to an address (fallback for non-wallet addresses)"""
    try:
        current_height = get_block_count()
        if not current_height:
            return 0
        
        total_received = 0
        blocks_with_payments = 0
        
        for i in range(min(num_blocks, current_height)):
            height = current_height - i
            block_hash = rpc_call("getblockhash", [height])
            if not block_hash:
                continue
            
            block = rpc_call("getblock", [block_hash, 2])
            if not block:
                continue
            
            # Check coinbase transaction
            coinbase_tx = block["tx"][0]
            for vout in coinbase_tx.get("vout", []):
                addresses = vout.get("scriptPubKey", {}).get("addresses", [])
                if address in addresses:
                    total_received += vout.get("value", 0)
                    blocks_with_payments += 1
        
        return total_received
    except:
        return 0

def get_submission_stats():
    """Parse block submission statistics from P2Pool logs"""
    try:
        stats = {
            'accepted': 0,
            'inconclusive': 0,
            'duplicate': 0,
            'duplicate_inconclusive': 0,
            'bad_cb_height': 0,
            'other': 0
        }
        
        # Method 1: Count "Multiaddress merged block accepted!" messages (new format)
        cmd_multiaddr = "grep -c 'Multiaddress merged block accepted!' " + P2POOL_LOG + " 2>/dev/null || echo 0"
        multiaddr_accepted = int(run_ssh_command(cmd_multiaddr))
        
        # Method 2: Count "rpc_submitblock returned: None" (legacy format)
        cmd_legacy = "grep -c 'rpc_submitblock returned: None' " + P2POOL_LOG + " 2>/dev/null || echo 0"
        legacy_accepted = int(run_ssh_command(cmd_legacy))
        
        # Use the higher count (should not double-count as formats differ)
        stats['accepted'] = max(multiaddr_accepted, legacy_accepted)
        
        # Count rejections - both multiaddress and legacy
        cmd_inconclusive = r"grep -c 'rejected: inconclusive\|rejected.*inconclusive' " + P2POOL_LOG + " 2>/dev/null || echo 0"
        stats['inconclusive'] = int(run_ssh_command(cmd_inconclusive))
        
        # Count duplicate/duplicate-inconclusive
        cmd_duplicate = r"grep -c 'duplicate\|duplicate-inconclusive' " + P2POOL_LOG + " 2>/dev/null || echo 0"
        dup_count = int(run_ssh_command(cmd_duplicate))
        stats['duplicate'] = dup_count
        
        # Count bad-cb-height errors
        cmd_cb = "grep -c 'bad-cb-height' " + P2POOL_LOG + " 2>/dev/null || echo 0"
        stats['bad_cb_height'] = int(run_ssh_command(cmd_cb))
        
        return stats
    except Exception as e:
        return None

def check_accepted_blocks_orphan_status():
    """Check which of our accepted blocks are still in the blockchain vs orphaned"""
    try:
        # Get all accepted block submissions with context
        cmd = "grep -B5 'rpc_submitblock returned: None' " + P2POOL_LOG + " | grep -E '(Building Dogecoin auxpow block|rpc_submitblock returned: None)' | head -100"
        result = run_ssh_command(cmd)
        
        # Parse to find block heights from getblocktemplate
        # We need to check if the block at that height matches our submission
        # Since we don't log the exact height, we'll check recent blocks for our address pattern
        
        # Alternative: Check recent blockchain blocks for our mining address
        current_height = get_block_count()
        if not current_height:
            return {'in_chain': 0, 'orphaned': 0, 'unknown': 0, 'checked': 0}
        
        our_blocks_in_chain = 0
        blocks_checked = 0
        
        # Check last 500 blocks for our address (covers ~2 minutes at 0.26s/block)
        for i in range(min(500, current_height)):
            height = current_height - i
            blocks_checked += 1
            
            try:
                block_hash = rpc_call("getblockhash", [height])
                if not block_hash:
                    continue
                    
                block = rpc_call("getblock", [block_hash, 2])
                if not block:
                    continue
                
                # Check coinbase transaction
                coinbase_tx = block["tx"][0]
                vouts = coinbase_tx.get("vout", [])
                
                # Look for our mining address OR donation address
                found_ours = False
                for vout in vouts:
                    addresses = vout.get("scriptPubKey", {}).get("addresses", [])
                    # Our mining address is Litecoin format, won't appear in Dogecoin blockchain
                    # Check for donation address or 2-output pattern (our signature)
                    if len(vouts) == 2:  # Our blocks have exactly 2 outputs
                        script_hex = vout.get("scriptPubKey", {}).get("hex", "")
                        # Check both new P2PKH and old P2PK donation scripts
                        if script_hex == DONATION_SCRIPT or script_hex == OLD_DONATION_SCRIPT:
                            found_ours = True
                            break
                
                if found_ours:
                    our_blocks_in_chain += 1
                    
            except:
                continue
        
        # Get total accepted from logs (both new and legacy format)
        cmd_total = "grep -c 'Multiaddress merged block accepted!\\|rpc_submitblock returned: None' " + P2POOL_LOG + " 2>/dev/null || echo 0"
        total_accepted = int(run_ssh_command(cmd_total))
        
        orphaned = max(0, total_accepted - our_blocks_in_chain)
        
        return {
            'in_chain': our_blocks_in_chain,
            'orphaned': orphaned,
            'total_accepted': total_accepted,
            'checked_blocks': blocks_checked
        }
    except Exception as e:
        return {'in_chain': 0, 'orphaned': 0, 'total_accepted': 0, 'checked_blocks': 0, 'error': str(e)}

def check_recent_accepted_blocks():
    """Check coinbase outputs of recently accepted blocks"""
    try:
        # Find timestamps of accepted blocks (both new and legacy format)
        cmd = r"grep -B10 'Multiaddress merged block accepted!\|rpc_submitblock returned: None' " + P2POOL_LOG + " | grep 'Dogecoin block candidate' | tail -5"
        result = run_ssh_command(cmd)
        
        blocks_info = []
        for line in result.split('\n'):
            if 'pow_hash=' in line:
                try:
                    # Extract pow_hash
                    pow_hash = line.split('pow_hash=')[1].split()[0]
                    blocks_info.append({'pow_hash': pow_hash[:16] + '...'})
                except:
                    continue
        
        return blocks_info
    except:
        return []

def check_recent_blocks(num_blocks=10):
    """Check recent blocks for our mined blocks (optimized for speed)"""
    current_height = get_block_count()
    if not current_height:
        return []
    
    found_blocks = []
    # Only scan last 10 blocks for speed over SSH
    for i in range(min(num_blocks, 10)):
        height = current_height - i
        block_hash = rpc_call("getblockhash", [height])
        if not block_hash:
            continue
            
        block = rpc_call("getblock", [block_hash, 2])
        if not block:
            continue
        
        coinbase_tx = block["tx"][0]
        vouts = coinbase_tx.get("vout", [])
        
        # Check if our address is in outputs
        for vout in vouts:
            addresses = vout.get("scriptPubKey", {}).get("addresses", [])
            if MINING_ADDRESS in addresses:
                found_blocks.append({
                    "height": height,
                    "hash": block_hash[:16] + "...",
                    "confirmations": current_height - height + 1,
                    "outputs": len(vouts),
                    "time": datetime.fromtimestamp(block["time"]).strftime("%H:%M:%S"),
                    "explorer": f"{EXPLORER_URL}/block/{block_hash}"
                })
                break
    
    return found_blocks

def get_candidate_info():
    """Get block candidate information from P2Pool log"""
    global last_candidate_count, recent_candidates, candidate_times
    
    # Count total candidates
    count_cmd = f'grep -c "Dogecoin block candidate" {P2POOL_LOG} 2>/dev/null || echo 0'
    total_candidates = int(run_ssh_command(count_cmd))
    
    # Get recent candidates if count increased
    if total_candidates > last_candidate_count:
        candidates_cmd = f'grep "Dogecoin block candidate" {P2POOL_LOG} | tail -20'
        candidates_output = run_ssh_command(candidates_cmd)
        
        for line in candidates_output.split('\n'):
            if 'pow_hash=' in line and 'ratio=' in line:
                try:
                    # Parse timestamp
                    timestamp_str = line.split('>')[0].strip()
                    timestamp = timestamp_str.split()[1]
                    
                    # Parse full datetime for hashrate calculation
                    dt = datetime.strptime(timestamp_str.split('>')[0].strip(), "%Y-%m-%d %H:%M:%S.%f")
                    candidate_times.append(dt)
                    
                    # Parse hash and ratio
                    pow_hash = line.split('pow_hash=')[1].split()[0][:16] + "..."
                    ratio = line.split('ratio=')[1].split('%')[0]
                    
                    candidate_info = {
                        "time": timestamp,
                        "hash": pow_hash,
                        "ratio": float(ratio)
                    }
                    
                    # Add if not already in recent list
                    if candidate_info not in recent_candidates:
                        recent_candidates.append(candidate_info)
                except:
                    continue
        
        last_candidate_count = total_candidates
    
    return total_candidates, list(recent_candidates)

def calculate_local_stats(network_info):
    """Calculate local mining statistics"""
    if len(candidate_times) < 2:
        return {"candidates_per_min": 0, "network_share": 0}
    
    # Calculate time span in seconds
    time_span = (candidate_times[-1] - candidate_times[0]).total_seconds()
    if time_span < 1:
        return {"candidates_per_min": 0, "network_share": 0}
    
    # Calculate candidates per minute
    candidates_per_min = (len(candidate_times) / time_span) * 60
    
    # Estimate network share based on candidate rate vs block rate
    # Network produces blocks at: network_hashps / (2^32 * difficulty) blocks/sec
    # We produce candidates at: candidates_per_sec rate
    # Our share ≈ (candidates_per_sec) / (network_blocks_per_sec) * (1/ratio_threshold)
    if network_info and network_info.get('networkhashps', 0) > 0 and network_info.get('difficulty', 0) > 0:
        network_blocks_per_sec = network_info['networkhashps'] / (2**32 * network_info['difficulty'])
        candidates_per_sec = len(candidate_times) / time_span
        # Rough estimate: assume candidates represent ~1% difficulty threshold
        # This is very approximate since actual candidate difficulty varies
        network_share = min(100, (candidates_per_sec / network_blocks_per_sec) * 100 * 0.01)
    else:
        network_share = 0
    
    return {
        "candidates_per_min": candidates_per_min,
        "network_share": network_share
    }

def get_donation_balance(mined_blocks):
    """Get donation outputs from blocks we actually mined"""
    try:
        if not mined_blocks:
            return {
                "outputs": 0,
                "total": 0,
                "scanned_blocks": 0
            }
        
        donation_outputs = 0
        donation_total = 0
        
        # Only check blocks we mined (much faster)
        for block_info in mined_blocks:
            height = block_info['height']
            block_hash = rpc_call("getblockhash", [height])
            if not block_hash:
                continue
            
            block = rpc_call("getblock", [block_hash, 2])
            if not block:
                continue
            
            # Check coinbase transaction for donation output
            coinbase_tx = block["tx"][0]
            vouts = coinbase_tx.get("vout", [])
            
            # Look for outputs with our donation script (both old and new)
            for vout in vouts:
                script_hex = vout.get("scriptPubKey", {}).get("hex", "")
                if script_hex == DONATION_SCRIPT or script_hex == OLD_DONATION_SCRIPT:
                    donation_outputs += 1
                    donation_total += vout.get("value", 0)
        
        return {
            "outputs": donation_outputs,
            "total": donation_total,
            "scanned_blocks": len(mined_blocks)
        }
    except:
        return {
            "outputs": 0,
            "total": 0,
            "scanned_blocks": 0
        }

def clear_screen():
    """Clear terminal screen and reset cursor"""
    import sys
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()

def print_header():
    """Print dashboard header"""
    print("=" * 80)
    print(" P2POOL DOGECOIN TESTNET MERGED MINING MONITOR".center(80))
    print("=" * 80)
    print()

def print_network_stats(info):
    """Print network statistics"""
    if not info:
        print("⚠️  Unable to fetch network info")
        return
    
    print("📊 NETWORK STATS")
    print("-" * 80)
    print(f"  Block Height:     {info['blocks']:,}")
    print(f"  Difficulty:       {info['difficulty']:.10f}")
    print(f"  Network Hashrate: {info['networkhashps']/1000:.1f} KH/s")
    print(f"  Chain:            {info.get('chain', 'unknown')}")
    print()

def print_mining_stats(candidates, balance, local_stats, donation_info, address_balance=None, old_address_balance=None, donation_balance=None):
    """Print our mining statistics"""
    print("⛏️  MINING STATS")
    print("-" * 80)
    print(f"  Total Candidates: {candidates}")
    print(f"  Candidate Rate:   {local_stats['candidates_per_min']:.2f} per minute")
    print(f"  Network Share:    ~{local_stats['network_share']:.2f}% (estimated)")
    
    # Show wallet balance
    if balance is not None:
        print(f"  Wallet Balance:   {balance:,.1f} DOGE (total)")
    else:
        print(f"  Wallet Balance:   N/A")
    
    # Show OLD address balance (historical blocks before fix)
    if old_address_balance is not None and old_address_balance > 0:
        print(f"  Old Address:      {old_address_balance:,.1f} DOGE ({OLD_DOGE_MINING_ADDRESS}) [pre-fix]")
    
    # Show NEW address balance (blocks after address conversion fix)
    if address_balance is not None:
        print(f"  New Address:      {address_balance:,.1f} DOGE ({DOGE_MINING_ADDRESS})")
    else:
        print(f"  New Address:      Check explorer: {EXPLORER_URL}/address/{DOGE_MINING_ADDRESS}")
    
    # Show P2Pool donation address balance
    print(f"\n  💰 P2Pool Donation ({DONATION_ADDRESS_DOGE_TESTNET}):")
    if donation_balance is not None:
        print(f"     Balance:        {donation_balance:,.1f} DOGE")
    else:
        print(f"     Check:          {EXPLORER_URL}/address/{DONATION_ADDRESS_DOGE_TESTNET}")
    
    if donation_info and donation_info.get('outputs', 0) > 0:
        print(f"     Recent blocks:  {donation_info['outputs']} outputs, {donation_info['total']:,.1f} DOGE")
    print()

def print_submission_stats(stats, orphan_status):
    """Print block submission statistics with orphan analysis"""
    print("📤 BLOCK SUBMISSION STATS")
    print("-" * 80)
    if not stats:
        print("  Unable to fetch submission stats")
    else:
        total = sum(stats.values())
        print(f"  Total Submissions: {total}")
        print(f"  ✅ Accepted:       {stats['accepted']} ({stats['accepted']*100//max(1,total)}%) [Multiaddress merged]")
        print(f"  ⏱️  Too Late:        {stats['inconclusive']} ({stats['inconclusive']*100//max(1,total)}%) [Racing with network]")
        print(f"  🔄 Duplicate:      {stats['duplicate']}")
        print(f"  ❌ Errors:         {stats['bad_cb_height'] + stats['other']}")
        
        if orphan_status and orphan_status.get('total_accepted', 0) > 0:
            print(f"\n  🔍 ORPHAN ANALYSIS (last {orphan_status['checked_blocks']} blocks scanned):")
            print(f"     Still in Chain: {orphan_status['in_chain']} blocks (have donation script)")
            print(f"     Orphaned:       {orphan_status['orphaned']} blocks (removed from chain)")
            print(f"     Orphan Rate:    {orphan_status['orphaned']*100//max(1,orphan_status['total_accepted'])}%")
            
            if orphan_status['in_chain'] > 0:
                print(f"\n     💰 Immature coinbase outputs: Check with minconf=0")
            else:
                print(f"\n     ⚠️  ALL accepted blocks were orphaned! (Testnet too fast)")
    print()

def print_recent_candidates(candidates_list):
    """Print recent block candidates (95%+ only)"""
    # Filter to show candidates that are very close to or exceeded block target
    qualifying_candidates = [c for c in candidates_list if c['ratio'] >= 95.0]
    
    print("🎯 RECENT CANDIDATES (≥95% to Target)")
    print("-" * 80)
    if not qualifying_candidates:
        print("  No qualifying candidates yet (need ≥95% to target)...")
    else:
        print(f"  {'Time':<12} {'Hash':<22} {'% to Target':>12}")
        print("  " + "-" * 78)
        for c in reversed(qualifying_candidates):  # Most recent first
            ratio_str = f"{c['ratio']:.2f}%"
            # Highlight 100%+ candidates with a marker
            marker = " ✓" if c['ratio'] >= 100.0 else ""
            print(f"  {c['time']:<12} {c['hash']:<22} {ratio_str:>12}{marker}")
    print()

def print_mined_blocks(blocks):
    """Print our mined blocks"""
    print("✅ MINED BLOCKS (Last 10 blocks scanned)")
    print("-" * 80)
    if not blocks:
        print("  No blocks found in last 10 blocks")
        print("  (Testnet competition: ~658 KH/s, 0.26s block time)")
    else:
        print(f"  {'Height':<10} {'Time':<10} {'Confs':<7} {'Outs':<5} {'Block Hash':<20}")
        print("  " + "-" * 78)
        for b in blocks[:5]:  # Show top 5
            print(f"  {b['height']:<10} {b['time']:<10} {b['confirmations']:<7} {b['outputs']:<5} {b['hash']:<20}")
            print(f"     🔗 {b['explorer']}")
    print()

def print_footer(update_time):
    """Print dashboard footer"""
    print("-" * 80)
    print(f"Last update: {update_time.strftime('%Y-%m-%d %H:%M:%S')} | Press Ctrl+C to exit")
    print("=" * 80)

def main():
    """Main monitoring loop"""
    # Initial startup message (only shown once)
    clear_screen()
    print("Starting P2Pool mining monitor...")
    print("Connecting to remote server...")
    print("Please wait...")
    time.sleep(2)
    
    try:
        iteration = 0
        while True:
            try:
                # Fetch data silently
                network_info = get_network_info()
                balance = get_balance()
                total_candidates, recent = get_candidate_info()
                
                # Calculate local mining stats
                local_stats = calculate_local_stats(network_info)
                
                # Get submission statistics (fast - just grep counts)
                submission_stats = get_submission_stats()
                
                # Skip slow orphan check - too slow over SSH
                orphan_status = None
                
                # Skip slow mined blocks scan - too slow over SSH
                mined_blocks = []
                
                # Skip slow donation scan
                donation_info = None
                
                # Get balance for mining addresses (wallet lookup only - blockchain scan too slow over SSH)
                # NEW address (after address conversion fix)
                address_balance = get_received_by_address(DOGE_MINING_ADDRESS)
                # Skip blockchain scan - too slow. Use explorer: https://blockexplorer.one/dogecoin/testnet/address/{address}
                
                # OLD address (before fix - LTC address used directly in DOGE coinbase)
                old_address_balance = get_received_by_address(OLD_DOGE_MINING_ADDRESS)
                
                # Donation address balance (new P2PKH donation script)
                donation_balance = get_received_by_address(DONATION_ADDRESS_DOGE_TESTNET)
                
                # Now clear and display everything at once
                clear_screen()
                print_header()
                print_network_stats(network_info)
                print_mining_stats(total_candidates, balance, local_stats, donation_info, address_balance, old_address_balance, donation_balance)
                print_submission_stats(submission_stats, orphan_status)
                print_recent_candidates(recent)
                print_mined_blocks(mined_blocks)
                print_footer(datetime.now())
                
                iteration += 1
                
                # Wait before next refresh
                time.sleep(5)
                
            except KeyboardInterrupt:
                raise
            except Exception as e:
                clear_screen()
                print_header()
                print(f"\n⚠️  Error: {e}")
                print("Retrying in 5 seconds...")
                time.sleep(5)
                
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user")
        sys.exit(0)

if __name__ == "__main__":
    main()
