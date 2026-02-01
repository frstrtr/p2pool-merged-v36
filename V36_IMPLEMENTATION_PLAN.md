# V36 Merged Mining Share Implementation Plan

## Executive Summary

This document outlines the comprehensive implementation plan for P2Pool V36 shares with merged mining support. V36 introduces **per-miner merged chain addresses** directly in the share structure, enabling fair, decentralized merged mining rewards with proper incentive-driven migration.

**Key Goals:**
1. Allow miners to specify explicit merged chain addresses (DOGE, etc.)
2. Support automatic P2PKH conversion for compatible addresses
3. Implement fair reward redistribution for pre-V36 nodes with unconvertible addresses
4. Create economic incentives for network migration to V36
5. **Migrate donation from lost-key address to controlled address** (Part 9)

**Critical Design Note - Different Migration Mechanics:**

| Feature | Migration Type | Reason |
|---------|---------------|--------|
| **Merged Mining Rewards** | Gradual proportional | NEW feature - V35 doesn't verify |
| **Donation** | Flag day (95%+ V36) | EXISTING feature - ALL nodes must agree on coinbase |

- **Merged rewards**: CAN be split proportionally because V35 doesn't verify merged outputs
- **Donation**: CANNOT change until V35 deprecated - `get_expected_payouts()` must produce IDENTICAL coinbase on ALL nodes
- **Result**: Continue SECONDARY_DONATION_ENABLED hack (50/50 split) until flag day

**Critical Donation Constraint:**
The original P2Pool donation address has a **LOST PRIVATE KEY** - all funds sent there are permanently burned. However, donation CANNOT be migrated gradually because **ALL P2Pool nodes (V35 and V36) must compute identical coinbase structure**. V35 nodes use `DONATION_SCRIPT` in `get_expected_payouts()` - if V36 used different script, V35 nodes would reject V36 blocks! Migration requires V35 deprecation first.

---

## Part 1: Current State Analysis

### 1.1 Existing Share Structure (V35 - PaddingBugfixShare)

```python
# Current share_data fields (V35):
share_data = {
    'previous_share_hash': IntType(256),
    'coinbase': VarStrType(),
    'nonce': IntType(32),
    'address': VarStrType(),         # Parent chain address (string)
    'subsidy': IntType(64),
    'donation': IntType(16),
    'stale_info': EnumType(),
    'desired_version': VarIntType(), # Currently signals up to 35
}
```

### 1.2 Current Address Handling Problem

| Address Type | Example | Convertible to DOGE? | Current Behavior |
|--------------|---------|---------------------|------------------|
| P2PKH | `Lxyz...` | ✅ Yes (pubkey_hash) | Works |
| P2WPKH | `ltc1q...` (43 chars) | ✅ Yes (pubkey_hash) | Works |
| P2SH | `Mxyz...` | ❌ No (script hash) | **Lost rewards** |
| P2WSH | `ltc1q...` (62 chars) | ❌ No (script hash) | **Lost rewards** |
| P2TR | `ltc1p...` | ❌ No (tweaked key) | **Lost rewards** |

### 1.3 Current Redistribution (Temporary Solution)

We currently redistribute unconvertible address rewards among convertible addresses on the merged mining node. This is:
- ✅ Fair for miners on OUR node
- ❌ Only benefits miners connected to the merged-mining-capable node
- ❌ Pre-V36 nodes can't participate at all

---

## Part 2: V36 Share Format Specification

### 2.1 New Share Class: MergedMiningShare

```python
class MergedMiningShare(BaseShare):
    VERSION = 36
    VOTING_VERSION = 36
    SUCCESSOR = None  # Current head (until V37)
    MINIMUM_PROTOCOL_VERSION = 3600  # +100 from V35's 3500
```

### 2.2 Extended share_info_type

```python
# V36 adds merged_addresses after existing fields
share_info_type = pack.ComposedType([
    ('share_data', pack.ComposedType([
        ('previous_share_hash', pack.PossiblyNoneType(0, pack.IntType(256))),
        ('coinbase', pack.VarStrType()),
        ('nonce', pack.IntType(32)),
        ('address', pack.VarStrType()),    # Parent chain address (unchanged)
        ('subsidy', pack.IntType(64)),
        ('donation', pack.IntType(16)),
        ('stale_info', pack.EnumType(...)),
        ('desired_version', pack.VarIntType()),  # Signals 36 for merged support
    ])),
    ('segwit_data', ...),  # Existing field
    # NEW in V36:
    ('merged_addresses', pack.PossiblyNoneType([], pack.ListType(
        pack.ComposedType([
            ('chain_id', pack.IntType(32)),      # AuxPoW chain ID (DOGE=0x62)
            ('script', pack.VarStrType()),       # Payment script for that chain
        ]), max_count=8))),  # Max 8 merged chains
    # Existing fields continue...
    ('far_share_hash', ...),
    ('max_bits', ...),
    ('bits', ...),
    ('timestamp', ...),
    ('absheight', ...),
    ('abswork', ...),
])
```

### 2.3 Type Definitions

```python
# New pack type for merged addresses
merged_address_entry_type = pack.ComposedType([
    ('chain_id', pack.IntType(32)),    # AuxPoW chain ID
    ('script', pack.VarStrType()),     # Payment script (P2PKH format)
])

# List of merged addresses (max 8 chains)
merged_addresses_type = pack.PossiblyNoneType(
    [],  # Default: empty list (auto-convert or no merged support)
    pack.ListType(merged_address_entry_type, max_count=8)
)
```

### 2.4 Chain IDs

| Chain | Chain ID | Notes |
|-------|----------|-------|
| Dogecoin | 0x00000062 (98) | From auxpow commitment |
| Bellscoin | TBD | Future support |
| Reserved | 0-7 | Reserved for special purposes |

---

## Part 3: Merged Reward Redistribution Principle

### 3.1 The Redistribution Problem

When a merged block is found, we must distribute rewards among ALL miners in the share chain (PPLNS). However:

1. **V36 Miners** - Have explicit merged addresses OR convertible P2PKH → Full eligibility
2. **Pre-V36 Miners with P2PKH** - Can auto-convert → Partial eligibility (incentive)
3. **Pre-V36 Miners with P2SH/Bech32** - Cannot convert → No eligibility (redistribute)

### 3.2 Three-Pool Distribution Model

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  MERGED BLOCK REWARD DISTRIBUTION                                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Total Merged Block Reward (e.g., 10,000 DOGE)                              │
│                     │                                                       │
│          ┌─────────┴─────────┐                                              │
│          ▼                   ▼                                              │
│    ┌───────────┐      ┌───────────────────┐                                 │
│    │ PRIMARY   │      │ INCENTIVE POOL    │                                 │
│    │ POOL (90%)│      │ (10% max)         │                                 │
│    └─────┬─────┘      └─────────┬─────────┘                                 │
│          │                      │                                           │
│          ▼                      ▼                                           │
│   V36 Miners Only       Pre-V36 Convertible                                 │
│   (full rewards)        (upgrade incentive)                                 │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐   │
│   │ PRE-V36 UNCONVERTIBLE SHARES → Redistributed to PRIMARY POOL        │   │
│   │ (P2SH, P2WSH, P2TR addresses get 0 merged rewards)                  │   │
│   └─────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Mathematical Model

#### Variables:

```
R_total     = Total merged block reward (satoshis)
W_v36       = Sum of share weights from V36 miners
W_pre_conv  = Sum of share weights from pre-V36 miners with convertible addresses
W_pre_unconv= Sum of share weights from pre-V36 miners with unconvertible addresses
W_total     = W_v36 + W_pre_conv + W_pre_unconv

INCENTIVE_RATE = 0.10  (10% - adjustable based on migration progress)
```

#### Pool Allocation:

```python
# Dynamic incentive rate based on V36 adoption
def calculate_incentive_rate(v36_adoption_percent):
    """
    Incentive rate decreases as V36 adoption increases.
    - 0% V36 adoption: 10% incentive (max encouragement)
    - 50% V36 adoption: 5% incentive
    - 90% V36 adoption: 1% incentive (minimal)
    - 100% V36 adoption: 0% incentive (everyone upgraded)
    """
    return max(0.01, 0.10 * (1.0 - v36_adoption_percent))

# Pool sizes
incentive_pool = R_total * INCENTIVE_RATE
primary_pool = R_total - incentive_pool

# If no V36 miners, primary pool goes to incentive pool
if W_v36 == 0:
    incentive_pool = R_total
    primary_pool = 0
```

#### Per-Miner Rewards:

```python
def calculate_miner_reward(miner_weight, miner_version, has_merged_address, R_total, pools, weights):
    """
    Calculate merged mining reward for a single miner.
    
    Args:
        miner_weight: This miner's share weight (target_to_average_attempts)
        miner_version: Share's desired_version field
        has_merged_address: True if explicit merged_addresses OR convertible P2PKH
        R_total: Total merged block reward
        pools: (primary_pool, incentive_pool)
        weights: (W_v36, W_pre_conv, W_pre_unconv)
    
    Returns:
        Reward in satoshis, or 0 if not eligible
    """
    primary_pool, incentive_pool = pools
    W_v36, W_pre_conv, W_pre_unconv = weights
    
    if not has_merged_address:
        # Unconvertible pre-V36: No reward (their share redistributed)
        return 0
    
    if miner_version >= 36:
        # V36 miner: Full primary pool share + redistribution bonus
        if W_v36 > 0:
            base_share = primary_pool * miner_weight / W_v36
            # Redistribution: unconvertible weight's portion goes to V36 miners
            redistribution_share = (R_total * W_pre_unconv / (W_total)) * (miner_weight / W_v36)
            return base_share + redistribution_share
        else:
            return 0
    else:
        # Pre-V36 with convertible address: Incentive pool only
        if W_pre_conv > 0:
            return incentive_pool * miner_weight / W_pre_conv
        else:
            return 0
```

### 3.4 Reward Flow Diagram

```
                    ┌──────────────────────────────────────┐
                    │   MERGED BLOCK FOUND (10,000 DOGE)   │
                    └──────────────────┬───────────────────┘
                                       │
                    ┌──────────────────┴───────────────────┐
                    │      Walk PPLNS Share Chain          │
                    │      (e.g., 8640 shares = 24 hours)  │
                    └──────────────────┬───────────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
        ▼                              ▼                              ▼
┌───────────────┐           ┌───────────────────┐          ┌──────────────────┐
│  V36 MINERS   │           │ PRE-V36 CONVERT.  │          │ PRE-V36 UNCONV.  │
│  version >= 36│           │ version < 36      │          │ version < 36     │
│  + any address│           │ P2PKH address     │          │ P2SH/Bech32      │
├───────────────┤           ├───────────────────┤          ├──────────────────┤
│  W_v36 = 4000 │           │ W_pre_conv = 3500 │          │ W_pre_unconv=500 │
│  (50% weight) │           │ (43.75% weight)   │          │ (6.25% weight)   │
└───────┬───────┘           └─────────┬─────────┘          └────────┬─────────┘
        │                             │                             │
        │                             │                             │
        ▼                             ▼                             ▼
┌───────────────┐           ┌───────────────────┐          ┌──────────────────┐
│PRIMARY POOL   │           │ INCENTIVE POOL    │          │ REDISTRIBUTED    │
│= 9000 DOGE    │           │ = 1000 DOGE       │          │ to Primary Pool  │
│(90% of total) │           │ (10% of total)    │          │ = 625 DOGE       │
└───────┬───────┘           └─────────┬─────────┘          └────────┬─────────┘
        │                             │                             │
        │         ┌───────────────────┘                             │
        │         │                                                 │
        ▼         ▼                                                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      FINAL DISTRIBUTION                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  V36 Miners (4000 weight):                                                  │
│    Base: 9000 * (4000/4000) = 9000 DOGE                                     │
│    Redistribution: 625 DOGE (from unconvertible)                            │
│    TOTAL: 9625 DOGE (96.25% of block!)                                      │
│                                                                             │
│  Pre-V36 Convertible (3500 weight):                                         │
│    Incentive: 1000 * (3500/3500) = 1000 DOGE                                │
│    TOTAL: 1000 DOGE (10% of block)                                          │
│    → Message: "Upgrade to V36 to earn 3.5x more!"                           │
│                                                                             │
│  Pre-V36 Unconvertible (500 weight):                                        │
│    TOTAL: 0 DOGE (cannot receive - no valid address)                        │
│    → Message: "Your address is not compatible with DOGE. Use P2PKH          │
│               or upgrade to V36 and specify a DOGE address."                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.5 Special Cases

#### Case 1: No V36 Miners Yet (Early Deployment)

```python
if W_v36 == 0:
    # All rewards go to incentive pool for convertible pre-V36 miners
    # This bootstraps the system - early upgraders see immediate benefit
    for miner in convertible_pre_v36_miners:
        payout[miner] = R_total * miner.weight / W_pre_conv
```

#### Case 2: All Miners are V36 (Full Migration)

```python
if W_pre_conv == 0 and W_pre_unconv == 0:
    # Standard proportional distribution, no incentive pool needed
    for miner in v36_miners:
        payout[miner] = R_total * miner.weight / W_v36
```

#### Case 3: Only Unconvertible Pre-V36 Miners

```python
if W_v36 == 0 and W_pre_conv == 0:
    # CRITICAL: No one can receive rewards!
    # This scenario shouldn't happen if merged mining nodes exist
    # The merged mining node operator should always be V36
    # Their reward acts as a "floor" for the system
    log.warning("No eligible merged mining recipients!")
```

---

## Part 4: Incentivization Strategy

### 4.1 Economic Incentives

| Miner Situation | Reward Multiplier | Message |
|-----------------|-------------------|---------|
| V36 + explicit DOGE addr | 100% of fair share | "Full merged mining rewards!" |
| V36 + P2PKH (auto-convert) | 100% of fair share | "Full merged mining rewards!" |
| Pre-V36 + P2PKH | ~10% of fair share | "Upgrade to earn 10x more!" |
| Pre-V36 + P2SH/Bech32 | 0% | "Address not compatible" |

### 4.2 Dashboard Notifications

```javascript
// Display in web dashboard for pre-V36 miners
function getMergedMiningStatus(minerShare) {
    if (minerShare.desired_version >= 36) {
        return {
            status: 'FULL_REWARDS',
            message: 'Full merged mining rewards active',
            color: 'green'
        };
    }
    
    if (isConvertibleAddress(minerShare.address)) {
        const potential = calculatePotentialReward(minerShare);
        const current = calculateCurrentIncentive(minerShare);
        return {
            status: 'INCENTIVE_ONLY',
            message: `Earning ${current} DOGE. Upgrade to V36 for ${potential} DOGE!`,
            color: 'yellow',
            upgradeUrl: '/upgrade-guide'
        };
    }
    
    return {
        status: 'NOT_ELIGIBLE',
        message: 'Your address cannot receive DOGE. Use P2PKH or specify DOGE address.',
        color: 'red',
        helpUrl: '/merged-mining-addresses'
    };
}
```

### 4.3 Migration Tracking

```python
def get_migration_stats(tracker, best_share_hash, chain_length):
    """
    Calculate V36 adoption statistics for the share chain.
    """
    stats = {
        'v36_shares': 0,
        'v36_weight': 0,
        'pre_v36_convertible_shares': 0,
        'pre_v36_convertible_weight': 0,
        'pre_v36_unconvertible_shares': 0,
        'pre_v36_unconvertible_weight': 0,
        'total_weight': 0,
    }
    
    for share in tracker.get_chain(best_share_hash, chain_length):
        weight = bitcoin_data.target_to_average_attempts(share.target)
        stats['total_weight'] += weight
        
        if share.desired_version >= 36:
            stats['v36_shares'] += 1
            stats['v36_weight'] += weight
        else:
            has_merged, _, _ = is_pubkey_hash_address(share.address, net.PARENT)
            if has_merged:
                stats['pre_v36_convertible_shares'] += 1
                stats['pre_v36_convertible_weight'] += weight
            else:
                stats['pre_v36_unconvertible_shares'] += 1
                stats['pre_v36_unconvertible_weight'] += weight
    
    # Calculate percentages
    if stats['total_weight'] > 0:
        stats['v36_percent'] = stats['v36_weight'] / stats['total_weight']
        stats['convertible_percent'] = stats['pre_v36_convertible_weight'] / stats['total_weight']
        stats['unconvertible_percent'] = stats['pre_v36_unconvertible_weight'] / stats['total_weight']
    
    return stats
```

---

## Part 5: Implementation Plan

### Phase 1: Share Format + Donation Migration (Week 1-2)

#### 1.1 Define MergedMiningShare Class

```python
# In p2pool/data.py

class MergedMiningShare(BaseShare):
    """
    V36 share with merged mining address support.
    Allows miners to specify explicit addresses for merged chains.
    ALSO: Migrates donation to controlled address (lost key fix!)
    """
    VERSION = 36
    VOTING_VERSION = 36
    SUCCESSOR = None
    MINIMUM_PROTOCOL_VERSION = 3600
    
    # CRITICAL: New gentx_before_refhash using SECONDARY_DONATION_SCRIPT
    # This is the donation migration point - no more burning to lost key!
    gentx_before_refhash = (
        pack.VarStrType().pack(SECONDARY_DONATION_SCRIPT) +  # NEW controlled address!
        pack.IntType(64).pack(0) + 
        pack.VarStrType().pack('\x6a\x28' + pack.IntType(256).pack(0) + pack.IntType(64).pack(0))[:3]
    )
    
    @classmethod
    def get_dynamic_types(cls, net):
        # Get V35 types first
        t = super(MergedMiningShare, cls).get_dynamic_types(net)
        
        # Add merged_addresses field to share_info_type
        # Insert after segwit_data but before far_share_hash
        merged_addresses_type = pack.PossiblyNoneType(
            [],
            pack.ListType(pack.ComposedType([
                ('chain_id', pack.IntType(32)),
                ('script', pack.VarStrType()),
            ]), max_count=8)
        )
        
        # Rebuild share_info_type with new field
        # ... implementation details ...
        
        return t
```

#### 1.2 Update Successor Chain

```python
# Update existing class
PaddingBugfixShare.SUCCESSOR = MergedMiningShare

# Update share_versions dict
share_versions = {
    s.VERSION: s for s in [
        MergedMiningShare,      # v36 - NEW
        PaddingBugfixShare,     # v35
        SegwitMiningShare,      # v34
        NewShare,               # v33
        PreSegwitShare,         # v32
        Share,                  # v17
    ]
}
```

### Phase 2: Address Resolution (Week 2-3)

#### 2.1 Implement Address Resolution

```python
# In p2pool/work.py or new p2pool/merged_addresses.py

def get_merged_address_for_share(share, chain_id, parent_net, merged_net):
    """
    Resolve merged chain address for a share.
    
    Priority:
    1. Explicit merged_addresses field (V36+)
    2. Auto-convert P2PKH/P2WPKH address
    3. Return None if not convertible
    
    Returns:
        Payment script for chain_id or None
    """
    # Priority 1: Check V36 explicit merged_addresses
    if share.VERSION >= 36:
        merged_addresses = share.share_info.get('merged_addresses', [])
        for entry in merged_addresses:
            if entry['chain_id'] == chain_id:
                return entry['script']
    
    # Priority 2: Try auto-conversion
    is_convertible, pubkey_hash, error = is_pubkey_hash_address(
        share.share_info['share_data']['address'], parent_net
    )
    
    if is_convertible and pubkey_hash is not None:
        # Convert pubkey_hash to merged chain P2PKH script
        return bitcoin_data.pubkey_hash_to_script2(
            pubkey_hash, merged_net.ADDRESS_VERSION, -1, merged_net
        )
    
    # Priority 3: Not convertible
    return None
```

### Phase 3: Reward Calculation (Week 3-4)

#### 3.1 Implement Distribution Algorithm

```python
# In p2pool/merged_mining.py or p2pool/work.py

def calculate_merged_payouts(tracker, best_share_hash, chain_id, total_reward, 
                              parent_net, merged_net, chain_length):
    """
    Calculate fair merged mining payouts using share chain weights.
    
    Implements three-pool distribution model:
    - Primary Pool (90%): V36 miners with full proportional rewards
    - Incentive Pool (10%): Pre-V36 miners with convertible addresses
    - Redistribution: Unconvertible shares' portion goes to Primary Pool
    
    Returns:
        dict: {merged_script: satoshi_amount}
    """
    MERGED_VERSION = 36
    BASE_INCENTIVE_RATE = 0.10  # 10% base
    
    # Accumulators
    v36_weights = {}           # {script: total_weight}
    pre_v36_conv_weights = {}  # {script: total_weight}
    total_v36_weight = 0
    total_pre_v36_conv_weight = 0
    total_pre_v36_unconv_weight = 0
    
    # Walk the share chain
    for share in tracker.get_chain(best_share_hash, chain_length):
        weight = bitcoin_data.target_to_average_attempts(share.target)
        merged_script = get_merged_address_for_share(
            share, chain_id, parent_net, merged_net
        )
        
        if merged_script is None:
            # Unconvertible - weight will be redistributed
            total_pre_v36_unconv_weight += weight
            continue
        
        if share.share_info['share_data']['desired_version'] >= MERGED_VERSION:
            # V36+ miner: Full primary pool
            v36_weights[merged_script] = v36_weights.get(merged_script, 0) + weight
            total_v36_weight += weight
        else:
            # Pre-V36 with convertible address: Incentive pool
            pre_v36_conv_weights[merged_script] = pre_v36_conv_weights.get(merged_script, 0) + weight
            total_pre_v36_conv_weight += weight
    
    # Calculate dynamic incentive rate based on V36 adoption
    total_weight = total_v36_weight + total_pre_v36_conv_weight + total_pre_v36_unconv_weight
    v36_adoption = total_v36_weight / total_weight if total_weight > 0 else 0
    incentive_rate = max(0.01, BASE_INCENTIVE_RATE * (1.0 - v36_adoption))
    
    # Special case: No V36 miners yet
    if total_v36_weight == 0:
        incentive_pool = total_reward
        primary_pool = 0
    else:
        incentive_pool = int(total_reward * incentive_rate)
        primary_pool = total_reward - incentive_pool
        
        # Add unconvertible redistribution to primary pool
        # (they can't receive, so their share goes to V36 miners)
        if total_weight > 0:
            unconv_portion = total_reward * total_pre_v36_unconv_weight // total_weight
            primary_pool += unconv_portion
    
    # Calculate payouts
    payouts = {}
    
    # Primary pool for V36 miners
    if total_v36_weight > 0 and primary_pool > 0:
        for script, weight in v36_weights.items():
            amount = primary_pool * weight // total_v36_weight
            payouts[script] = payouts.get(script, 0) + amount
    
    # Incentive pool for pre-V36 convertible miners
    if total_pre_v36_conv_weight > 0 and incentive_pool > 0:
        for script, weight in pre_v36_conv_weights.items():
            amount = incentive_pool * weight // total_pre_v36_conv_weight
            payouts[script] = payouts.get(script, 0) + amount
    
    return payouts
```

### Phase 4: CLI and Configuration (Week 4-5)

#### 4.1 New Command Line Options

```python
# In p2pool/main.py

parser.add_argument('--merged-address-doge',
    help='Explicit Dogecoin address for merged mining rewards',
    type=str, action='store', default=None, dest='merged_address_doge')

parser.add_argument('--merged-address-bells',
    help='Explicit Bellscoin address for merged mining rewards',
    type=str, action='store', default=None, dest='merged_address_bells')

# Generic format for future chains
parser.add_argument('--merged-address',
    help='Merged chain address in format CHAIN:ADDRESS (e.g., doge:D9xyz...)',
    type=str, action='append', default=[], dest='merged_addresses')
```

#### 4.2 Share Generation with Merged Addresses

```python
# In p2pool/work.py WorkerBridge.get_work()

def build_merged_addresses(self):
    """
    Build merged_addresses list from CLI options and miner requests.
    """
    merged_addresses = []
    
    # From CLI options
    if self.args.merged_address_doge:
        doge_script = bitcoin_data.address_to_script2(
            self.args.merged_address_doge, dogecoin_net)
        merged_addresses.append({
            'chain_id': 0x62,  # Dogecoin
            'script': doge_script
        })
    
    if self.args.merged_address_bells:
        bells_script = bitcoin_data.address_to_script2(
            self.args.merged_address_bells, bellscoin_net)
        merged_addresses.append({
            'chain_id': 0x...,  # Bellscoin chain ID
            'script': bells_script
        })
    
    # From --merged-address format
    for entry in self.args.merged_addresses:
        chain, address = entry.split(':', 1)
        chain_id, net = get_chain_info(chain)
        script = bitcoin_data.address_to_script2(address, net)
        merged_addresses.append({
            'chain_id': chain_id,
            'script': script
        })
    
    return merged_addresses if merged_addresses else None
```

### Phase 5: Network Protocol (Week 5-6)

#### 5.1 Protocol Version Bump

```python
# In p2pool/p2p.py
class Protocol(p2protocol.Protocol):
    VERSION = 3600  # Bump from 3502 to support V36 shares
```

#### 5.2 Share Serialization

The V36 shares need to serialize/deserialize the new `merged_addresses` field correctly. This is handled by the pack types, but we need to ensure backward compatibility:

```python
# V36 shares received by V35 nodes will be rejected as "unknown type"
# V35 shares received by V36 nodes will be accepted (SUCCESSOR chain)
```

### Phase 6: Testing (Week 6-7)

#### 6.1 Unit Tests

```python
# test_v36_shares.py

def test_v36_share_serialization():
    """Test V36 share round-trip serialization."""
    share = create_test_v36_share(merged_addresses=[
        {'chain_id': 0x62, 'script': b'\x76\xa9\x14' + b'\x00'*20 + b'\x88\xac'}
    ])
    packed = pack_share(share)
    unpacked = unpack_share(packed)
    assert unpacked.merged_addresses == share.merged_addresses

def test_merged_address_resolution():
    """Test address resolution priority."""
    # V36 with explicit address
    share_v36 = create_share(version=36, merged_addresses=[
        {'chain_id': 0x62, 'script': DOGE_SCRIPT_A}
    ])
    assert get_merged_address_for_share(share_v36, 0x62) == DOGE_SCRIPT_A
    
    # Pre-V36 with P2PKH (should auto-convert)
    share_v35_p2pkh = create_share(version=35, address='Lxyz...')
    assert get_merged_address_for_share(share_v35_p2pkh, 0x62) is not None
    
    # Pre-V36 with P2SH (should return None)
    share_v35_p2sh = create_share(version=35, address='Mxyz...')
    assert get_merged_address_for_share(share_v35_p2sh, 0x62) is None

def test_reward_distribution():
    """Test three-pool reward distribution."""
    # Create share chain with mixed versions
    shares = [
        create_share(version=36, weight=1000),  # V36
        create_share(version=35, address='Lxyz...', weight=500),  # Pre-V36 convertible
        create_share(version=35, address='Mxyz...', weight=300),  # Pre-V36 unconvertible
    ]
    
    payouts = calculate_merged_payouts(shares, total_reward=10000)
    
    # V36 should get primary pool + redistribution
    # Pre-V36 convertible should get incentive pool
    # Pre-V36 unconvertible should get nothing
    assert payouts[v36_script] > payouts[pre_v36_conv_script]
    assert pre_v36_unconv_script not in payouts
```

### Phase 7: Deployment (Week 7-8)

#### 7.1 Testnet Deployment

1. Deploy V36-capable nodes on Litecoin testnet
2. Connect to existing tLTC P2Pool network
3. Verify V35 shares still accepted
4. Verify V36 shares propagate correctly
5. Test merged mining with tDOGE

#### 7.2 Mainnet Rollout

1. Deploy V36 nodes alongside existing V35 nodes
2. Monitor `desired_version` signaling
3. Wait for 50% adoption before enabling full merged mining
4. Gradually reduce incentive pool as adoption increases

---

## Part 6: Files to Modify

| File | Changes |
|------|---------|
| `p2pool/data.py` | MergedMiningShare class, merged_addresses type, share_versions, **NEW gentx_before_refhash** |
| `p2pool/work.py` | get_merged_address_for_share(), build_merged_addresses(), share generation |
| `p2pool/web.py` | calculate_merged_payouts(), migration stats endpoint, **donation_stats endpoint** |
| `p2pool/main.py` | --merged-address-* CLI options |
| `p2pool/p2p.py` | Protocol VERSION bump to 3600 |
| `p2pool/bitcoin/data.py` | merged_address_entry_type pack definition |
| `p2pool/networks/*.py` | MINIMUM_PROTOCOL_VERSION update |
| `web-static/dashboard.html` | Migration status display, upgrade prompts, **donation transparency** |

---

## Part 7: Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Chain split between V35/V36 | High | SUCCESSOR chain ensures V36 nodes accept V35 shares |
| Address validation bugs | Medium | Extensive testing, fail-safe to auto-convert |
| Incentive gaming | Low | POW-protected, each share costs real work |
| Migration stalls | Medium | Adjust incentive rates dynamically |
| Merged chain issues | Low | Graceful degradation if merged chain unavailable |
| **Donation migration confusion** | Medium | **Clear documentation, transparency dashboard** |

---

## Part 8: Success Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| V36 adoption rate | >50% in 4 weeks | `get_desired_version_counts()` |
| Unconvertible share rate | <10% | Migration stats endpoint |
| Merged block success rate | >95% | Block submission logs |
| Miner upgrade complaints | Minimal | Community feedback |

---

## Appendix A: Chain ID Reference

| Chain | Chain ID (hex) | Chain ID (dec) | Notes |
|-------|----------------|----------------|-------|
| Dogecoin | 0x00000062 | 98 | Reversed from auxpow magic |
| Litecoin | 0x00000000 | 0 | Parent chain (not needed) |
| Bellscoin | TBD | TBD | Future support |
| Reserved | 0x00000001-0x00000007 | 1-7 | Reserved |

---

## Appendix B: Example Merged Block Distribution

**Scenario:** Merged block found with 10,000 DOGE reward

**Share Chain Composition:**
- 100 V36 shares (5,000 weight total)
- 150 pre-V36 P2PKH shares (4,000 weight total)
- 25 pre-V36 P2SH shares (1,000 weight total)
- Total: 275 shares, 10,000 weight

**V36 Adoption:** 50% (5,000 / 10,000)

**Incentive Rate:** 5% (0.10 * (1 - 0.50))

**Distribution:**
```
Total Reward:       10,000 DOGE

Incentive Pool:        500 DOGE (5%)
Primary Pool:        9,500 DOGE (95%)

Unconvertible Portion: 1,000 DOGE (10% of total)
Redistributed to Primary: +1,000 DOGE

Final Primary Pool:  10,500 DOGE (9,500 + 1,000 redistribution)

V36 Miners (5,000 weight):
  10,500 * (5,000/5,000) = 10,500 DOGE total
  Average per V36 share: 105 DOGE

Pre-V36 P2PKH (4,000 weight):
  500 * (4,000/4,000) = 500 DOGE total
  Average per pre-V36 share: 3.33 DOGE

Pre-V36 P2SH (1,000 weight):
  0 DOGE (address not compatible)

Upgrade Incentive:
  V36 miner earns 105 DOGE/share
  Pre-V36 P2PKH earns 3.33 DOGE/share
  Ratio: 31.5x more rewards for upgrading!
```

---

## Part 9: Donation Script Migration Plan

### 9.1 The Problem: Lost Private Key

The original P2Pool donation script is a **P2PK (Pay-to-Public-Key)** format with a **LOST PRIVATE KEY**:

```python
# Original DONATION_SCRIPT (P2PK format) - PRIVATE KEY LOST!
# P2PK script: <65-byte uncompressed pubkey> OP_CHECKSIG
DONATION_SCRIPT = '4104ffd03de44a6e11b9917f3a29f9443283d9871c9d743ef30d5eddcd37094b64d1b3d8090496b53256786bf5c82932ec23c3b74d9f05a6f95a8b5529352656664bac'.decode('hex')

# This pubkey hash160 is: d03da6fca390166d020be0e7c28ac8cc70f58403
# Which corresponds to addresses (P2PKH equivalent):
# - Bitcoin:  1Kz5QaUPDtKrj5SqW5tFkn7WZh8LmQaQi4
# - Litecoin: LeD2fnnDJYZuyt8zgDsZ2oBGmuVcxGKCLd
# - Dogecoin: DQ8AwqR2XJE9G5dSEfspJYH7Spre85dj6L
#
# NOTE: P2PK outputs can be spent without revealing the address,
# but the private key is LOST so these funds are UNSPENDABLE!
```

**Impact:**
- Every P2Pool block sends 0.5-1% to this address
- Estimated lost funds on Litecoin: ~10,000+ LTC over the years
- Funds accumulate but can NEVER be spent
- This is wasteful and reduces miner rewards

### 9.2 CRITICAL CONSTRAINT: Coinbase Must Be Identical Across All Nodes!

**⚠️ THIS IS THE KEY CONSTRAINT THAT LIMITS OUR OPTIONS ⚠️**

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  WHY DONATION CANNOT CHANGE UNTIL V35 IS DEPRECATED                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  BLOCK VALIDATION REQUIREMENT:                                              │
│  ├─ When ANY share finds a block, ALL P2Pool nodes must agree on coinbase   │
│  ├─ get_expected_payouts() calculates payouts for ALL miners in PPLNS       │
│  ├─ This function uses donation_script_to_address() → DONATION_SCRIPT       │
│  └─ ALL nodes must compute IDENTICAL coinbase outputs!                      │
│                                                                             │
│  IF V36 USED DIFFERENT DONATION SCRIPT:                                     │
│  ├─ V36 node: donation → SECONDARY_DONATION_SCRIPT                          │
│  ├─ V35 node: donation → DONATION_SCRIPT                                    │
│  ├─ RESULT: Different coinbase structure!                                   │
│  └─ V35 nodes would REJECT blocks from V36 nodes!                           │
│                                                                             │
│  UNLIKE MERGED MINING (which is NEW and V35 doesn't verify):                │
│  ├─ Donation is part of EXISTING coinbase verification                      │
│  ├─ V35 nodes verify ALL blocks against their expected payout structure     │
│  └─ Cannot use different donation until V35 is GONE!                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.3 Why gentx_before_refhash Doesn't Help Here

You might think: "V36 can have different gentx_before_refhash, so it can use different donation!"

**WRONG.** Here's why:

```python
# gentx_before_refhash is for SHARE VERIFICATION, not BLOCK PAYOUT CALCULATION

# Share verification:
# - Each share version has its own gentx_before_refhash
# - V35 share verified with V35's gentx_before_refhash
# - V36 share verified with V36's gentx_before_refhash
# - This works fine for SHARES

# Block payout calculation:
# - When block found, get_expected_payouts() is called
# - This calculates payouts for ALL miners in PPLNS chain
# - Uses SINGLE donation_script_to_address() for ALL shares
# - ALL nodes must compute SAME result!

# The coinbase that actually goes on the blockchain must:
# 1. Match the winning share's hash_link (which share found the block)
# 2. Be accepted by ALL P2Pool nodes (for share chain validation)
# 3. Be consistent with get_expected_payouts() on ALL nodes

# PROBLEM: If V36 coinbase has SECONDARY_DONATION_SCRIPT,
#          but V35 nodes expect DONATION_SCRIPT,
#          V35 nodes reject the block as invalid!
```

### 9.4 The ONLY Valid Migration Path

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  DONATION MIGRATION: MUST WAIT FOR V35 DEPRECATION                          │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  PHASE 0: CURRENT STATE (V35 Network)                                       │
│  ├─ All blocks: 100% donation to DONATION_SCRIPT (burned)                   │
│  ├─ Our nodes: Use SECONDARY_DONATION_ENABLED hack                          │
│  │   └─ Adds SECOND output (doesn't change donation amount calculation!)    │
│  └─ This works because we ADD an output, not CHANGE the donation            │
│                                                                             │
│  PHASE 1: V36 DEPLOYMENT (0-95% V36 adoption)                               │
│  ├─ V36 nodes deployed, coexisting with V35 nodes                           │
│  ├─ Coinbase STILL uses DONATION_SCRIPT for donation calculation            │
│  ├─ V36 gentx_before_refhash: Keep same as V35 for now!                     │
│  │   └─ OR: V36 shares only verified by V36 nodes (V35 rejects anyway)      │
│  └─ SECONDARY_DONATION_ENABLED hack continues (adds 2nd output)             │
│                                                                             │
│  PHASE 2: V36 SUPERMAJORITY (95%+ V36 adoption)                             │
│  ├─ Version signaling shows >95% V36                                        │
│  ├─ Prepare for flag day                                                    │
│  └─ Announce deprecation timeline                                           │
│                                                                             │
│  PHASE 3: V35 DEPRECATION (Flag Day)                                        │
│  ├─ Bump MINIMUM_PROTOCOL_VERSION to reject V35 shares                      │
│  ├─ V35 nodes can no longer participate in share chain                      │
│  └─ NOW we can change donation calculation!                                 │
│                                                                             │
│  PHASE 4: POST-DEPRECATION (100% V36)                                       │
│  ├─ Update get_expected_payouts() to use SECONDARY_DONATION_SCRIPT          │
│  ├─ Update gentx_before_refhash to use SECONDARY_DONATION_SCRIPT            │
│  ├─ Remove SECONDARY_DONATION_ENABLED hack                                  │
│  └─ 100% of donations go to controlled address!                             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.5 Why We CAN'T Do Per-Block Migration (Like Merged Mining)

**Merged Mining Rewards** - CAN be proportionally split:
- V35 doesn't have merged mining, so it doesn't verify merged outputs
- V36 can distribute merged rewards however it wants
- V35 nodes simply ignore merged outputs they don't understand

**Donation** - CANNOT be changed until V35 is gone:
- V35 DOES verify donation outputs
- V35 nodes use `get_expected_payouts()` which hard-codes DONATION_SCRIPT
- If coinbase doesn't match V35's expectation, V35 rejects the block
- Even if V36 share finds block, V35 nodes must accept it!

### 9.6 Current Transitional Solution: SECONDARY_DONATION_ENABLED

Our current hack works because it ADDS a second output without changing the primary:

```python
# Current implementation (p2pool/data.py):

if SECONDARY_DONATION_ENABLED and secondary_donation_address and not verifying:
    secondary_donation_amount = total_donation // 2  # 50% to secondary
    primary_donation_amount = total_donation - secondary_donation_amount
    amounts[secondary_donation_address] = amounts.get(secondary_donation_address, 0) + secondary_donation_amount
    amounts[donation_address] = amounts.get(donation_address, 0) + primary_donation_amount
else:
    amounts[donation_address] = amounts.get(donation_address, 0) + total_donation

# KEY: We only use this when `not verifying`!
# When verifying shares from others, we use their format (single donation)
# This means our shares have 2 donation outputs, but we still verify theirs correctly
```

**Why this works:**
- We add SECONDARY output to OUR shares only
- When verifying THEIR shares, we use their expected format
- The total donation AMOUNT stays the same
- We just split it between two addresses

**Limitation:**
- 50% still goes to burned address
- Can't improve this until V35 is gone

### 9.7 V36 Donation Strategy: Continue the Hack

For V36, we continue the same strategy:

```python
# V36 donation handling (BEFORE V35 deprecation):

class MergedMiningShare(BaseShare):
    VERSION = 36
    
    # IMPORTANT: Keep same gentx_before_refhash structure as V35!
    # This ensures coinbase format is compatible
    # (OR: Accept that V35 rejects V36 shares - that's expected anyway)
    gentx_before_refhash = pack.VarStrType().pack(DONATION_SCRIPT) + ...

# In generate_transaction:
# - Still calculate donation using DONATION_SCRIPT for compatibility
# - Still add SECONDARY_DONATION_SCRIPT as bonus output (not verifying)
# - V35 nodes can still verify our coinbase structure
```

### 9.8 Post-V35-Deprecation: Full Migration

Only AFTER V35 is deprecated (MINIMUM_PROTOCOL_VERSION bump):

```python
# V36 donation handling (AFTER V35 deprecation):

# Update donation calculation:
def donation_script_to_address(net):
    return bitcoin_data.script2_to_address(SECONDARY_DONATION_SCRIPT, net)

# Update gentx_before_refhash:
class MergedMiningShare(BaseShare):
    gentx_before_refhash = pack.VarStrType().pack(SECONDARY_DONATION_SCRIPT) + ...

# Remove dual-donation hack:
# - Single donation output to SECONDARY_DONATION_SCRIPT
# - 100% of donation saved!
```

### 9.9 Timeline Comparison: Merged Mining vs Donation

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  FEATURE MIGRATION TIMELINES                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  MERGED MINING REWARDS (Gradual from Day 1):                                │
│  ├─ Day 1: V36 deployed → V36 miners get full merged rewards                │
│  ├─ Week 2: 30% V36 → 30% of merged rewards to V36 miners                   │
│  ├─ Month 1: 70% V36 → 70% of merged rewards to V36 miners                  │
│  └─ Migration complete when V36 dominant in PPLNS                           │
│                                                                             │
│  DONATION (Flag Day after 95% V36):                                         │
│  ├─ Day 1 to Flag Day: BOTH donations (50/50 split continues)               │
│  ├─ Flag Day: MINIMUM_PROTOCOL_VERSION bump, V35 rejected                   │
│  └─ Post Flag Day: 100% donation to controlled address                      │
│                                                                             │
│  WHY THE DIFFERENCE:                                                        │
│  ├─ Merged rewards: NEW feature, V35 doesn't verify                         │
│  └─ Donation: EXISTING feature, V35 DOES verify coinbase structure          │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.10 Dashboard: Show Donation Migration Status

```javascript
// In web-static/dashboard.html

function loadDonationStatus() {
    d3.json('../donation_stats', function(stats) {
        if (!stats) return;
        
        var v36Pct = stats.v36_adoption * 100;
        var threshold = 95;
        
        d3.select('#donation_status').html(`
            <div class="donation-status">
                <h4>Donation Migration Status</h4>
                <p>V36 Adoption: ${v36Pct.toFixed(1)}% (need ${threshold}% for migration)</p>
                <div class="progress-bar">
                    <div class="progress" style="width: ${Math.min(v36Pct, 100)}%"></div>
                    <div class="threshold" style="left: ${threshold}%"></div>
                </div>
                <p class="donation-split">
                    Current: 50% saved / 50% burned<br>
                    After migration: 100% saved
                </p>
                ${v36Pct >= threshold 
                    ? '<p class="ready">✓ Ready for donation migration! Awaiting flag day.</p>'
                    : '<p class="waiting">Upgrade to V36 to help reach migration threshold!</p>'
                }
            </div>
        `);
    });
}
```

### 9.11 Summary: Donation Migration is a Flag Day Event

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  KEY TAKEAWAYS                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  1. Donation migration CANNOT be gradual like merged mining                 │
│     └─ All nodes must agree on coinbase structure                           │
│                                                                             │
│  2. Must wait for V35 deprecation (flag day)                                │
│     └─ 95%+ V36 adoption triggers deprecation timeline                      │
│     └─ MINIMUM_PROTOCOL_VERSION bump removes V35 nodes                      │
│                                                                             │
│  3. Until flag day, use SECONDARY_DONATION_ENABLED hack                     │
│     └─ 50/50 split between old (burned) and new (saved)                     │
│     └─ Better than 100% burned, but not ideal                               │
│                                                                             │
│  4. After flag day, full migration                                          │
│     └─ 100% donation to controlled address                                  │
│     └─ No more burned funds!                                                │
│                                                                             │
│  5. This is fundamentally different from merged mining                      │
│     └─ Merged mining: V35 doesn't verify → gradual migration OK             │
│     └─ Donation: V35 DOES verify → flag day required                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Appendix C: Donation Address Reference

**Script Formats:**
- **DONATION_SCRIPT** (P2PK): `4104ffd03de...ac` - pubkey_hash: `0384f570ccc88ac2e7e00b026d1690a3fca63dd0`
- **SECONDARY_DONATION_SCRIPT** (P2PKH): `76a91420cb5c22...88ac` - pubkey_hash: `20cb5c22b1e4d5947e5c112c7696b51ad9af3c61`

| Chain | Old Donation (P2PK - BURNED) | New Donation (P2PKH - CONTROLLED) |
|-------|------------------------------|-----------------------------------|
| Bitcoin | `1Kz5QaUPDtKrj5SqW5tFkn7WZh8LmQaQi4` | `13zQEqHLKUCvnfJbvq4KRXb96FDrMa72CB` |
| Litecoin | `LeD2fnnDJYZuyt8zgDsZ2oBGmuVcxGKCLd` | `LNDMW3bAQ8Sz3Tzm6y3chYeuJTb8VHSHGM` |
| Dogecoin | `DQ8AwqR2XJE9G5dSEfspJYH7Spre85dj6L` | `D88Vn6Dyct7DKfVCfR3syHkjyNx9gEyyiv` |

**CRITICAL:** 
- Old donation is P2PK format (65-byte pubkey, no address in coinbase) - **PRIVATE KEY LOST!**
- New donation is P2PKH format - **WE CONTROL THIS!**

---

*Document Version: 1.6*
*Last Updated: February 2026*
*Author: P2Pool Merged Mining Team*
*Changelog:*
- *v1.0: Initial V36 merged mining plan*
- *v1.1: Added donation migration plan (Part 9)*
- *v1.2: Updated donation migration to use same gradual principle as merged rewards*
- *v1.3: CORRECTED: Donation migration is per-block (winner takes all), NOT proportional split*
- *v1.4: FINAL CORRECTION: Donation CANNOT change until V35 deprecated - all nodes must compute identical coinbase. Continue SECONDARY_DONATION_ENABLED hack (50/50 split) until flag day.*
- *v1.5: FIXED: Corrected all donation addresses - they were completely wrong. Verified against actual script hash160 values.*
- *v1.6: VERIFIED: BTC donation address confirmed as 1Kz5QaUPDtKrj5SqW5tFkn7WZh8LmQaQi4 (user-provided), all addresses now derived correctly from hash160 d03da6fca390166d020be0e7c28ac8cc70f58403*
