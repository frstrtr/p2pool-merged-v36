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
- [ ] **Phase 4**: Dashboard BBS UI (`web-static/`) — bulletin board message viewer
- [ ] **Phase 5**: Key management — offline derivation tool + `--signing-key` flag
- [ ] **Phase 6**: Wire obfuscation — Layer 1 encryption of all message_data
- [ ] **Phase 7**: Private messaging — ECDH encryption for P2P messages
- [ ] **Phase 8**: Ban system — node-local sender filtering

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

Add after `merged_broadcaster_status` endpoint. All messaging endpoints
use the `/msg/` prefix:

#### GET /msg/recent

Returns recent messages with optional filtering.

```python
def get_msg_recent():
    return {
        'messages': message_store.to_json(limit=50),
        'stats': message_store.stats,
    }

web_root.putChild('msg_recent', WebInterface(get_msg_recent))
```

#### GET /msg/chat

Returns miner-to-miner chat messages only.

```python
def get_msg_chat():
    return {
        'messages': message_store.to_json(msg_type=MSG_MINER_MESSAGE, limit=50),
    }

web_root.putChild('msg_chat', WebInterface(get_msg_chat))
```

#### GET /msg/alerts

Returns emergency alerts only.

```python
def get_msg_alerts():
    return {
        'alerts': message_store.to_json(msg_type=MSG_EMERGENCY, limit=10),
    }

web_root.putChild('msg_alerts', WebInterface(get_msg_alerts))
```

#### GET /msg/stats

Returns messaging system statistics and key registry.

```python
def get_msg_stats():
    return message_store.stats

web_root.putChild('msg_stats', WebInterface(get_msg_stats))
```

#### GET /msg/keys

Returns known signing key registry.

```python
def get_msg_keys():
    return message_store.key_registry.to_json()

web_root.putChild('msg_keys', WebInterface(get_msg_keys))
```

#### GET /msg/identity

Returns this node's signing identity (public info only).

```python
def get_msg_identity():
    if wb.signing_key:
        return {
            'signing_id': wb.signing_key.signing_id.encode('hex'),
            'signing_pubkey': wb.signing_key.signing_pubkey.encode('hex'),
            'key_index': wb.signing_key.key_index,
            'address': wb.address,
        }
    return {'error': 'no signing key configured'}

web_root.putChild('msg_identity', WebInterface(get_msg_identity))
```

#### POST /msg/send

Queue a message for the next share (via web form or curl).

```python
# Handled by the BBS page form submission
# or curl -X POST http://node:9327/msg/send -d '{"type":"chat","text":"hello"}'
```

#### POST /msg/ban, DELETE /msg/ban, GET /msg/bans

Ban system endpoints (Phase 8).

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

## Phase 4: Dashboard BBS UI (web-static/)

### 4.1 BBS (Bulletin Board System) page

**NEW FILE**: `web-static/bbs.html`

A retro BBS-style page at `http://node:9327/static/bbs.html`:

```
┌─────────────────────────────────────────────────────────┐
│  P2Pool BBS — Share-Authenticated Messaging             │
├─────────────────────────────────────────────────────────┤
│  [Chat] [Announcements] [Alerts] [Node Status] [Keys]  │
├─────────────────────────────────────────────────────────┤
│  #42  LVzy9...azc  ✅verified  2m ago                   │
│  > hello from miner A!                                  │
│                                                         │
│  #41  LRF2Z...DDp  ✅verified  5m ago                   │
│  > merged mining DOGE working great                     │
│                                                         │
│  #40  LiF7n...3s3  ⚠️unverified  8m ago                 │
│  > anyone seeing orphans?                               │
│                                                         │
│  #39  [SYSTEM]  NODE_STATUS  10m ago                    │
│  > v13.4 | up 2d | 1.5MH/s | 3 peers | DOGE merged    │
├─────────────────────────────────────────────────────────┤
│  🔒 Private messages (1 unread)                         │
│  From a1b2c3... → [Decrypt]                             │
├─────────────────────────────────────────────────────────┤
│  Send: [____________________________] [Chat] [Announce] │
│  Private to: [signing_id] [Send Private]                │
│  [Ban sender] [View keys] [Rotate key]                  │
└─────────────────────────────────────────────────────────┘
```

### 4.2 Message viewer widget (existing dashboard)

Add a "Messages" panel to the main dashboard showing:
- Last 5 messages (compact view)
- Alert banner for EMERGENCY messages (red, sticky)
- Link to full BBS page

### 4.3 API polling

```javascript
// Poll messages every 30 seconds
setInterval(function() {
    $.getJSON('/msg/recent', function(data) {
        updateMessageList(data.messages);
        updateMessageStats(data.stats);
    });
    // Check for alerts more frequently
    $.getJSON('/msg/alerts', function(data) {
        if (data.alerts.length > 0) showAlertBanner(data.alerts[0]);
    });
}, 30000);
```

---

## Phase 5: Key Management

### 5.1 Offline Key Derivation Tool

**NEW FILE**: `derive_signing_key.py` (standalone script, runs on any machine)

This tool runs **offline** on the miner's secure machine — NOT on the
p2pool node. The node NEVER sees the master private key.

```python
#!/usr/bin/env python
"""Offline signing key derivation tool.

Usage:
  python derive_signing_key.py <master_WIF> [key_index]

Derives a signing key from the master payout address private key.
The output signing_key_WIF is safe to copy to the p2pool node.
The master_WIF should NEVER leave this machine.
"""
```

Output:
```
Master address:  LVzy9mWFCQDBebZwvdSChevDJTJTxVbazc
Key index:       0
Signing key WIF: <derived_key_WIF>
Signing ID:      a1b2c3d4e5f6...
Signing pubkey:  02abcd...

Copy this to your p2pool node:
  --signing-key <derived_key_WIF> --signing-key-index 0

WARNING: Do NOT copy your master WIF to the node!
```

### 5.2 Command-line flags

```
--signing-key WIF          Derived signing key WIF (from offline tool)
--signing-key-index N      Key rotation index (must match offline derivation)
--message TEXT             Queue a miner message for next share
--announce TEXT            Queue a pool announcement for next share
```

### 5.3 Key rotation procedure

```
1. Miner runs offline tool with key_index=1:
     python derive_signing_key.py <master_WIF> 1

2. Miner updates node config:
     --signing-key <new_WIF> --signing-key-index 1

3. Node restarts, next share carries new signing_key_announcement

4. All nodes see key_index=1 > 0 → revoke key_index=0

5. Old messages from key_index=0 become unverifiable

6. New messages use new signing key
```

### 5.4 Signing key state file

```json
{
    "key_index": 0,
    "signing_id": "a1b2c3...",
    "created": 1707580800,
    "last_rotated": null,
    "messages_signed": 42
}
```

The master private key is NOT stored anywhere on the node.

---

## Phase 6: Wire Obfuscation

### 6.1 Obfuscation in data.py

**File**: `p2pool/data.py` or `p2pool/share_messages.py`

Add obfuscation/deobfuscation around `message_data` packing:

```python
def obfuscate_message_data(message_data, share_hash, net):
    """XOR-obfuscate message_data for wire transmission."""
    if not message_data:
        return message_data
    key = hashlib.sha256(b'p2pool-msg-obfuscate' + net.IDENTIFIER).digest()
    nonce = share_hash[:12]
    keystream = _generate_keystream(key, nonce, len(message_data))
    return bytes(a ^ b for a, b in zip(message_data, keystream))

def deobfuscate_message_data(ciphertext, share_hash, net):
    """XOR is symmetric — same function."""
    return obfuscate_message_data(ciphertext, share_hash, net)
```

### 6.2 Integration points

- **Pack (work.py)**: `obfuscate(message_data)` before setting in ref_type
- **Unpack (data.py)**: `deobfuscate(message_data)` after reading from ref_type

**Note**: Wire obfuscation does NOT affect ref_hash — the obfuscated bytes
are what gets hashed. Both sides produce the same obfuscated bytes from the
same input because the obfuscation key is deterministic.

---

## Phase 7: Private Messaging (ECDH)

### 7.1 ECDH key agreement in share_messages.py

```python
def ecdh_shared_secret(my_privkey, their_pubkey):
    """Compute ECDH shared secret for private messaging."""
    shared_point = ecdsa.ECDH(curve=SECP256k1)
    shared_point.load_private_key(my_privkey)
    shared_point.load_received_public_key(their_pubkey)
    return hashlib.sha256(shared_point.generate_sharedsecret_bytes()).digest()

def encrypt_private_message(plaintext, sender_privkey, recipient_pubkey, timestamp):
    """Encrypt a private message using ECDH + AES-256-GCM."""
    shared_secret = ecdh_shared_secret(sender_privkey, recipient_pubkey)
    encryption_key = hmac.new(shared_secret, b'p2pool-msg-encrypt', hashlib.sha256).digest()
    # Use timestamp as nonce component for uniqueness
    nonce = struct.pack('<I', timestamp) + b'\x00' * 8
    # AES-256-GCM (requires PyCryptodome or pyaes+gmac)
    ...
```

### 7.2 Crypto library consideration

PyPy 2.7 does not include AES-GCM natively. Options:
- `pyaes` (pure Python AES) + manual GMAC — slow but portable
- `PyCryptodome` — C-based, fast, but may need compilation for PyPy
- ChaCha20-Poly1305 via `nacl` — excellent performance, may need cffi

**Decision**: Defer AES-GCM library choice to implementation time. Wire
obfuscation (Phase 6) provides immediate protection; private encryption
is a Phase 7 enhancement.

---

## Phase 8: Ban System

### 8.1 Ban list management

**File**: `p2pool/share_messages.py` (extend `ShareMessageStore`)

```python
class BanList(object):
    """Node-local ban list for message filtering."""
    
    def __init__(self, data_dir):
        self.banned_signing_ids = set()   # set of hex signing_ids
        self.banned_addresses = set()     # set of payout addresses
        self.banned_words = set()         # set of keywords
        self.ban_file = os.path.join(data_dir, 'banned_senders.json')
        self._load()
    
    def is_banned(self, message):
        if message.signing_id.encode('hex') in self.banned_signing_ids:
            return True
        if hasattr(message, 'sender_address') and message.sender_address in self.banned_addresses:
            return True
        if any(word in message.payload for word in self.banned_words):
            return True
        return False
```

### 8.2 CLI flags

```
--ban-sender <signing_id_hex>     Ban a signing key
--ban-address <LTC_address>       Ban all keys from an address  
--ban-word <keyword>              Filter messages containing keyword
```

### 8.3 API endpoints

```
POST   /msg/ban    {"signing_id": "a1b2c3..."}    Ban a sender
POST   /msg/ban    {"address": "LVzy9..."}         Ban an address
POST   /msg/ban    {"word": "spam"}                Ban a keyword
DELETE /msg/ban    {"signing_id": "a1b2c3..."}     Unban a sender
GET    /msg/bans                                    List all bans
```

### 8.4 Behavior

- Banned messages are **dropped silently** on the receiving node
- Banned messages are **still relayed in shares** — the banning node
  cannot remove them (they're PoW-protected in ref_hash)
- Other nodes see the messages normally unless they also ban the sender
- Ban list is persisted to `data/<net>/banned_senders.json`

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

Step 4: Deploy offline key derivation tool (Phase 5.1)
        Miners generate derived signing keys on secure machines.
        Provide --signing-key WIF to nodes.

Step 5: Deploy wire obfuscation (Phase 6)
        All message_data is XOR-obfuscated on the wire.
        Must deploy to ALL nodes before enabling messages.

Step 6: Deploy work.py changes (Phase 2)
        Enable message embedding in shares.
        Messages start propagating via shares (obfuscated).
        *** This is the activation moment ***

Step 7: Deploy BBS dashboard UI (Phase 4)
        Bulletin board message viewer at /static/bbs.html.

Step 8: Deploy ban system (Phase 8)
        Node-local sender filtering.

Step 9: Deploy private messaging (Phase 7)
        ECDH-encrypted P2P messages.
        Requires crypto library evaluation for PyPy 2.7.
```

---

## Testing Checklist

### Phase 0: Core module
- [x] `share_messages.py` unit tests: pack/unpack round-trip
- [x] `share_messages.py` unit tests: signing key derivation deterministic
- [x] `share_messages.py` unit tests: sign/verify round-trip
- [x] `share_messages.py` unit tests: key rotation revokes old keys
- [x] `share_messages.py` unit tests: message deduplication
- [x] `share_messages.py` unit tests: message expiry/pruning
- [x] `share_messages.py` unit tests: announcement round-trip
- [x] `share_messages.py` unit tests: message store (ALL 8 TESTS PASS on PyPy 2.7)

### Phase 1: data.py integration
- [ ] Integration test: ref_hash unchanged when message_data is default (b'')
- [ ] Integration test: ref_hash changes when message_data is non-empty
- [ ] Integration test: share with messages passes verification
- [ ] Integration test: share with tampered messages fails verification

### Phase 2: work.py integration
- [ ] Integration test: messages propagate between two nodes via shares
- [ ] End-to-end test: miner sends message, other node's dashboard shows it
- [ ] Stress test: MAX_MESSAGES_PER_SHARE messages per share
- [ ] Stress test: MAX_MESSAGE_HISTORY messages in store

### Phase 3: web.py API
- [ ] Integration test: web API returns correct message data
- [ ] Integration test: /msg/identity returns signing info
- [ ] Integration test: /msg/send queues message for next share

### Phase 4: BBS UI
- [ ] BBS page loads and displays messages
- [ ] Tab filtering works (Chat, Alerts, Status)
- [ ] EMERGENCY alert banner displays prominently

### Phase 5: Key management
- [ ] Offline derivation tool produces correct keys
- [ ] Derived key WIF works when provided via --signing-key
- [ ] Key rotation increments key_index and revokes old keys

### Phase 6: Wire obfuscation
- [ ] Obfuscation/deobfuscation round-trip preserves message_data
- [ ] Obfuscated bytes differ from plaintext
- [ ] Different shares produce different obfuscated output (nonce varies)
- [ ] ref_hash with obfuscated data is consistent across nodes

### Phase 7: Private messaging
- [ ] ECDH shared secret matches for sender and recipient
- [ ] Encrypted message decrypts correctly for recipient
- [ ] Non-recipient cannot decrypt private message
- [ ] FLAG_PRIVATE messages display as "[encrypted]" for non-recipients

### Phase 8: Ban system
- [ ] Ban by signing_id blocks messages from that key
- [ ] Ban by address blocks all keys from that address
- [ ] Ban by keyword filters matching messages
- [ ] Banned messages are still relayed in shares (not stripped)
- [ ] Ban list persists across restarts

### Backward compatibility
- [ ] V35 node can still relay V36 shares with messages

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|-----------|
| ref_hash mismatch after deployment | Shares rejected | Deploy field first with default value, enable messages later |
| Message data too large | Share propagation slower | 512-byte hard limit, 3 messages max |
| Signing key compromise | Message impersonation | Key rotation via key_index increment; derived key ≠ master key |
| Master key exposure on node | Funds at risk | NEVER store master key on node; offline derivation tool |
| Message spam via rapid share generation | Memory bloat | MAX_MESSAGE_HISTORY=1000, 24h expiry |
| Crypto library unavailable on some platforms | Can't sign/verify | Graceful fallback: messages treated as unsigned |
| V35 nodes can't parse message_data | Fork? | No — PossiblyNoneType with default, V35 ignores unknown fields |
| Plaintext messages on wire | Content exposure | Wire obfuscation (Phase 6) before activation |
| ECDH no forward secrecy | Past messages decryptable | Frequent key rotation via key_index |
| Ban evasion via new signing key | Spam continues | Ban by payout address (harder to rotate) |
| AES-GCM library for PyPy 2.7 | May need compilation | Evaluate pyaes, PyCryptodome, nacl at implementation time |
