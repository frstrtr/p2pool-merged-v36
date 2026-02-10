# P2Pool Share Messaging — Integration Plan

## Overview

This document details how to integrate the share messaging system
(`share_messages.py`) into the existing P2Pool codebase. The integration
touches four files and requires no changes to the P2P protocol.

## Integration Status

- [x] **Phase 0**: Core module (`share_messages.py`) — DONE
- [ ] **Phase 1**: V36 ref_type extension (`data.py`) — adds `message_data` field
- [ ] **Phase 2**: Work generation (`work.py`) — embeds queued messages in new shares
- [ ] **Phase 3**: Web API (`web.py`) — endpoints for viewing/sending messages
- [ ] **Phase 4**: Dashboard UI (`web-static/`) — message viewer and chat interface
- [ ] **Phase 5**: Key management CLI — `--signing-key-index` flag, key rotation

---

## Phase 1: V36 ref_type Extension (data.py)

### 1.1 Add `message_data` to ref_type

**File**: `p2pool/data.py`  
**Location**: `MergedMiningShare.get_dynamic_types()`, around line 996

**Current code:**
```python
t['ref_type'] = pack.ComposedType([
    ('identifier', pack.FixedStrType(64//8)),
    ('share_info', t['share_info_type']),
])
```

**Changed to:**
```python
t['ref_type'] = pack.ComposedType([
    ('identifier', pack.FixedStrType(64//8)),
    ('share_info', t['share_info_type']),
    ('message_data', pack.PossiblyNoneType(b'', pack.VarStrType())),
])
```

**IMPORTANT**: This changes `ref_hash` computation. This is a V36-only change.
Nodes running V35 do not compute ref_hash for V36 shares — they just relay them.
V36 nodes will produce different ref_hashes after this change, so this MUST
be deployed before any node embeds messages (otherwise ref_hash mismatch).

**Deployment strategy**: Deploy in two phases:
1. First deploy: Add `message_data` field but always set it to `b''` (default)
   - ref_hash is unchanged because PossiblyNoneType with default `b''` packs identically
   - This validates the field is parseable
2. Second deploy: Enable message embedding in work.py
   - Now messages appear in ref_hash, but all V36 nodes understand the field

### 1.2 Include message_data in get_ref_hash()

**File**: `p2pool/data.py`  
**Location**: `BaseShare.get_ref_hash()`, around line 635

**Current code:**
```python
@classmethod
def get_ref_hash(cls, net, share_info, ref_merkle_link):
    return pack.IntType(256).pack(bitcoin_data.check_merkle_link(
        bitcoin_data.hash256(cls.get_dynamic_types(net)['ref_type'].pack(dict(
            identifier=net.IDENTIFIER,
            share_info=share_info,
        ))), ref_merkle_link))
```

**Changed to (for V36 only):**
```python
@classmethod
def get_ref_hash(cls, net, share_info, ref_merkle_link, message_data=None):
    ref_dict = dict(
        identifier=net.IDENTIFIER,
        share_info=share_info,
    )
    if cls.VERSION >= 36:
        ref_dict['message_data'] = message_data  # None = default (empty)
    return pack.IntType(256).pack(bitcoin_data.check_merkle_link(
        bitcoin_data.hash256(cls.get_dynamic_types(net)['ref_type'].pack(ref_dict)),
        ref_merkle_link))
```

### 1.3 Process messages during share verification

**File**: `p2pool/data.py`  
**Location**: `BaseShare.check()`, around line 790

After the existing `generate_transaction` call in `check()`, add message
processing:

```python
# After gentx verification succeeds:
# Process any messages embedded in this share
if self.VERSION >= 36 and hasattr(self, '_message_data') and self._message_data:
    from p2pool.share_messages import unpack_share_messages
    try:
        messages, signing_key_info = unpack_share_messages(self._message_data)
        # Messages will be processed by the message store in the node
        self._parsed_messages = messages
        self._signing_key_info = signing_key_info
    except Exception:
        self._parsed_messages = []
        self._signing_key_info = None
```

### 1.4 Store message_data during share __init__

**File**: `p2pool/data.py`  
**Location**: `BaseShare.__init__()`, around line 654

Add to the `__init__` method to preserve message_data from the share contents:

```python
# After existing field assignments:
if self.VERSION >= 36:
    self._message_data = contents.get('message_data', b'')
else:
    self._message_data = b''
```

This also requires adding `_message_data` to the `__slots__` declaration.

### 1.5 Clear cached_types

**CRITICAL**: `MergedMiningShare.cached_types` must be `None` for the new
`ref_type` to take effect. Since this is a code change (not runtime), this
happens automatically on restart. But if hot-reloading is ever used, the
cache must be explicitly cleared.

---

## Phase 2: Work Generation (work.py)

### 2.1 Access pending messages

**File**: `p2pool/work.py`  
**Location**: `WorkerBridge.__init__()` or attribute initialization

```python
# In WorkerBridge initialization:
self.message_store = None       # Set by web.py
self.pending_messages = []      # Messages queued for next share
self.signing_key = None         # DerivedSigningKey, set during startup
```

### 2.2 Embed messages in share generation

**File**: `p2pool/work.py`  
**Location**: Around line 1237, after `generate_transaction()` call

The messages need to be packed and passed to `generate_transaction()` so they
can be included in `ref_hash`. However, since messages are in `ref_type` (not
`share_info_type`), they're added at the `get_ref_hash()` level, not inside
`generate_transaction()`.

**Approach**: After `generate_transaction()` returns `share_info`, compute
the message_data and include it in `get_share()`:

```python
# After generate_transaction returns:
share_info, gentx, other_transaction_hashes, get_share = share_type.generate_transaction(...)

# Prepare message data for ref_hash
message_data = b''
if share_type.VERSION >= 36 and self.pending_messages:
    from p2pool.share_messages import pack_share_messages
    
    # Sign messages if we have a signing key
    for msg in self.pending_messages:
        if msg.has_signature and self.signing_key:
            msg.sign(self.signing_key)
    
    # Pack announcement + messages
    announcement = self.signing_key.pack_announcement() if self.signing_key else None
    message_data = pack_share_messages(self.pending_messages, announcement)
    self.pending_messages = []  # Clear queue

# Pass message_data through to get_share for ref_hash computation
```

### 2.3 Auto-generate NODE_STATUS messages

**File**: `p2pool/work.py`  
**Location**: In the periodic work update loop

Every N shares (e.g., every 100 shares or every 5 minutes), automatically
queue a NODE_STATUS message:

```python
from p2pool.share_messages import build_node_status

# Check if it's time for a status update
if time.time() - self.last_status_message > 300:  # Every 5 minutes
    status_msg = build_node_status(
        version=p2pool.__version__,
        uptime=time.time() - start_time,
        hashrate=local_hash_rate,
        share_count=tracker.get_height(best_share),
        peers=len(node.p2p_node.peers),
        merged_chains=['DOGE'] if wb.merged_work.value else None,
        capabilities=['v36', 'mm', 'msg'],
    )
    self.pending_messages.append(status_msg)
    self.last_status_message = time.time()
```

---

## Phase 3: Web API (web.py)

### 3.1 Initialize message store

**File**: `p2pool/web.py`  
**Location**: After `get_web_root()` initialization, around line 1649

```python
from p2pool.share_messages import (
    ShareMessageStore, build_miner_message, build_pool_announcement,
    MSG_NODE_STATUS, MSG_MINER_MESSAGE, MSG_POOL_ANNOUNCE,
    MSG_MERGED_STATUS, MSG_EMERGENCY,
)

# Initialize message store
message_store = ShareMessageStore()

# Make accessible from WorkerBridge for share integration
wb.message_store = message_store
wb.pending_messages = []
```

### 3.2 API endpoints

Add after `merged_broadcaster_status` endpoint:

#### GET /share_messages

Returns recent messages with optional filtering.

```python
def get_share_messages():
    return {
        'messages': message_store.to_json(limit=50),
        'stats': message_store.stats,
    }

web_root.putChild('share_messages', WebInterface(get_share_messages))
```

#### GET /share_messages/chat

Returns miner-to-miner chat messages only.

```python
def get_share_chat():
    return {
        'messages': message_store.to_json(msg_type=MSG_MINER_MESSAGE, limit=50),
    }

web_root.putChild('share_messages_chat', WebInterface(get_share_chat))
```

#### GET /share_messages/alerts

Returns emergency alerts only.

```python
def get_share_alerts():
    return {
        'alerts': message_store.to_json(msg_type=MSG_EMERGENCY, limit=10),
    }

web_root.putChild('share_messages_alerts', WebInterface(get_share_alerts))
```

#### GET /share_message_stats

Returns messaging system statistics and key registry.

```python
def get_share_message_stats():
    return message_store.stats

web_root.putChild('share_message_stats', WebInterface(get_share_message_stats))
```

#### GET /signing_keys

Returns known signing key registry.

```python
def get_signing_keys():
    return message_store.key_registry.to_json()

web_root.putChild('signing_keys', WebInterface(get_signing_keys))
```

### 3.3 Periodic node status generation

Add a periodic task to generate NODE_STATUS messages locally:

```python
def generate_node_status():
    try:
        if node.tracker.get_height(node.best_share_var.value) < 10:
            return

        lookbehind = min(node.tracker.get_height(node.best_share_var.value), 720)
        pool_hr = p2pool_data.get_pool_attempts_per_second(
            node.tracker, node.best_share_var.value, lookbehind)

        miner_hash_rates, _ = wb.get_local_rates()
        local_hr = sum(miner_hash_rates.values())

        merged_chains = []
        if hasattr(wb, 'merged_work') and wb.merged_work and \
                hasattr(wb.merged_work, 'value') and wb.merged_work.value:
            for chain_id, chain in wb.merged_work.value.iteritems():
                sym = chain.get('merged_net_symbol', 'AUX')
                if chain_id == 98:
                    sym = 'DOGE'
                merged_chains.append(sym)

        msg = build_node_status(
            version=p2pool.__version__,
            uptime=time.time() - start_time,
            hashrate=local_hr,
            share_count=node.tracker.get_height(node.best_share_var.value),
            peers=len(node.p2p_node.peers),
            merged_chains=merged_chains if merged_chains else None,
            capabilities=['v36', 'mm', 'msg'] if merged_chains else ['v36', 'msg'],
        )
        msg.sender_address = wb.address

        message_store.add_local_message(msg)
    except Exception:
        pass

x_node_status = deferral.RobustLoopingCall(generate_node_status)
x_node_status.start(300)  # Every 5 minutes
stop_event.watch(x_node_status.stop)
```

---

## Phase 4: Dashboard UI (web-static/)

### 4.1 Message viewer widget

Add a "Messages" tab or panel to the existing web dashboard that shows:

- Recent messages (all types)
- Miner chat tab
- Node status overview
- Alert banner for EMERGENCY messages
- Signing key registry viewer

### 4.2 API polling

```javascript
// Poll messages every 30 seconds
setInterval(function() {
    $.getJSON('/share_messages', function(data) {
        updateMessageList(data.messages);
        updateMessageStats(data.stats);
    });
}, 30000);
```

---

## Phase 5: Key Management CLI

### 5.1 Command-line flags

```
--signing-key-file PATH    Path to signing key state file (default: data/signing_key.json)
--signing-key-index N      Force key_index for key rotation (default: auto from file)
--message TEXT             Queue a miner message for next share
--announce TEXT            Queue a pool announcement for next share
```

### 5.2 Signing key state file

```json
{
    "key_index": 0,
    "signing_id": "a1b2c3...",
    "created": 1707580800,
    "last_rotated": null,
    "messages_signed": 42
}
```

The master private key is NOT stored — it's provided at runtime (from wallet
or config). Only the key_index and metadata are persisted.

### 5.3 Key rotation procedure

```
1. Miner runs: pypy run_p2pool.py ... --signing-key-index 1
2. Node derives new signing key from master_pk + key_index=1
3. Next mined share carries new signing_key_announcement
4. All nodes see key_index=1 > 0 → revoke key_index=0
5. Old messages become unverifiable
6. New messages use new signing key
```

---

## Deployment Order

```
Step 1: Deploy share_messages.py to all nodes (no behavioral change)
        Just the module — nothing imports it yet.

Step 2: Deploy data.py changes (Phase 1.1, 1.2, 1.4)
        Add message_data to ref_type with PossiblyNoneType default.
        ref_hash unchanged because default is b'' → None → packs same.
        *** Requires restart of all nodes ***

Step 3: Deploy web.py changes (Phase 3)
        Add API endpoints. Messages are local-only at this point.
        Periodic NODE_STATUS generation starts.

Step 4: Deploy work.py changes (Phase 2)
        Enable message embedding in shares.
        Messages start propagating via shares.
        *** This is the activation moment ***

Step 5: Deploy dashboard UI (Phase 4)
        Visual message viewer.

Step 6: Deploy CLI key management (Phase 5)
        Signing key rotation, message queueing from command line.
```

---

## Testing Checklist

- [ ] `share_messages.py` unit tests: pack/unpack round-trip
- [ ] `share_messages.py` unit tests: signing key derivation deterministic
- [ ] `share_messages.py` unit tests: sign/verify round-trip
- [ ] `share_messages.py` unit tests: key rotation revokes old keys
- [ ] `share_messages.py` unit tests: message deduplication
- [ ] `share_messages.py` unit tests: message expiry/pruning
- [ ] Integration test: ref_hash unchanged when message_data is default (b'')
- [ ] Integration test: ref_hash changes when message_data is non-empty
- [ ] Integration test: share with messages passes verification
- [ ] Integration test: share with tampered messages fails verification
- [ ] Integration test: web API returns correct message data
- [ ] Integration test: messages propagate between two nodes via shares
- [ ] End-to-end test: miner sends message, other node's dashboard shows it
- [ ] Stress test: MAX_MESSAGES_PER_SHARE messages per share
- [ ] Stress test: MAX_MESSAGE_HISTORY messages in store
- [ ] Backward compat: V35 node can still relay V36 shares with messages

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| ref_hash mismatch after deployment | Shares rejected | Deploy field first with default value, enable messages later |
| Message data too large | Share propagation slower | 512-byte hard limit, 3 messages max |
| Signing key compromise | Message impersonation | Key rotation via key_index increment |
| Message spam via rapid share generation | Memory bloat | MAX_MESSAGE_HISTORY=1000, 24h expiry |
| Crypto library unavailable on some platforms | Can't sign/verify | Graceful fallback: messages treated as unsigned |
| V35 nodes can't parse message_data | Fork? | No — PossiblyNoneType with default, V35 ignores unknown fields |
