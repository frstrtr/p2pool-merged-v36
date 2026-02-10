# P2Pool Share-Based Messaging System — Design Document

## Overview

A decentralized messaging system embedded in P2Pool share data, using share
Proof-of-Work as anti-spam and derived signing keys for authentication.

Messages are part of the share hash (PoW-protected) but NOT part of the
coinbase transaction — they never pollute the parent blockchain.

## Design Goals

1. **PoW-gated**: Only miners who produce valid shares can send messages
2. **Blockchain-clean**: Messages never appear in Litecoin/Dogecoin blocks
3. **Authenticated**: Messages signed with derived keys, verifiable by all nodes
4. **Key-safe**: Master private key (payout address) is NEVER exposed to the node
5. **Rotatable**: Signing keys can be rotated, revoking all previous signatures
6. **Encrypted**: Messages are not plaintext on the wire — even public BBS messages
7. **Private messaging**: P2P messages use ECDH encryption, only sender/recipient can read
8. **Bannable**: Nodes can locally ban senders by signing_id or miner address
9. **Backward-compatible**: Embeds in V36 without breaking existing share format
10. **Zero infrastructure**: No external servers, databases, or PKI — trust is anchored to share PoW

## Why NOT Coinbase OP_RETURN

| Approach | PoW-protected? | Blockchain-clean? | Propagated? |
|----------|:-:|:-:|:-:|
| Coinbase OP_RETURN | ✅ | ❌ pollutes blockchain if block found | ✅ |
| `share_data['coinbase']` suffix | ✅ | ❌ same problem — in coinbase | ✅ |
| Separate P2P message type | ❌ needs own anti-spam | ✅ | ✅ needs new protocol |
| **ref_type extension (chosen)** | **✅** | **✅** | **✅** via existing P2P |

The chosen approach adds a `message_data` field to V36's `ref_type`. This field:
- Is included in `ref_hash` → part of the share merkle tree → protected by PoW
- Is NOT included in the coinbase/gentx → never written to any blockchain
- Propagates with shares via existing p2pool P2P protocol — no new message types

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     P2Pool Share (V36)                        │
├──────────────────────────────────────────────────────────────┤
│ share_info (existing — in coinbase/gentx/blockchain)         │
│   ├── share_data (previous_hash, coinbase, nonce, address…)  │
│   ├── segwit_data                                            │
│   ├── merged_addresses (NEW in V36)                          │
│   └── far_share_hash, bits, timestamp, absheight, abswork    │
├──────────────────────────────────────────────────────────────┤
│ ref_type (PoW-protected via ref_hash, NOT in blockchain)     │
│   ├── identifier (8 bytes)                                   │
│   ├── share_info (same as above)                             │
│   └── message_data (NEW — VarStrType)  ◄── MESSAGES HERE     │
│         ├── signing_key_announcement (57 bytes, optional)     │
│         └── messages[] (up to 3 per share)                   │
├──────────────────────────────────────────────────────────────┤
│ ref_hash = merkle(hash256(ref_type.pack(...)))               │
│   └── Includes message_data → PoW protects messages          │
├──────────────────────────────────────────────────────────────┤
│ gentx/coinbase (existing — written to blockchain)            │
│   └── Does NOT include message_data → blockchain stays clean │
└──────────────────────────────────────────────────────────────┘
```

## Derived Signing Key Scheme

### The Problem

Miners need to sign messages, but exposing the payout address private key
would be catastrophic — an attacker could steal all mining rewards.

### The Solution: Offline Key Derivation

The miner derives a **one-way signing key** from their master private key
**OFFLINE** (on their own machine, never on the node). The derived signing
key is then provided to the node via config file or CLI flag.

**CRITICAL: The node never sees the master private key.**

The signing key can sign messages but cannot be reversed to obtain the
master key. If the signing key is compromised, the miner rotates to a
new key by incrementing `key_index` and re-running the offline derivation.

### Offline Derivation Tool

```bash
# Run on miner's secure machine — NOT on the p2pool node
$ python derive_signing_key.py <master_WIF> [key_index]

Master address: LVzy9mWFCQDBebZwvdSChevDJTJTxVbazc
Key index: 0
Signing key WIF: KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn
Signing ID: a1b2c3d4e5f6...

Put this in your p2pool config:
  --signing-key KwDiBf89QgGbjEhKnhXJuH7LrciVrZi3qYjgd9M7rFU73sVHnoWn
  --signing-key-index 0

To rotate (if compromised):
  python derive_signing_key.py <master_WIF> 1
```

The node receives only the **derived signing key** (safe to expose) and the
**key_index** (public). The master WIF stays on the miner's air-gapped machine.

### Derivation

```
Master Private Key (payout address — NEVER EXPOSED)
    │
    └─── HMAC-SHA256(master_pk, "p2pool-msg-v1" || key_index_le32)
              │
              ├── signing_privkey (32 bytes)
              │     └── Used to ECDSA-sign messages
              │
              ├── signing_pubkey = secp256k1(signing_privkey) (33 bytes compressed)
              │     └── Announced in shares for verification
              │
              └── signing_id = HASH160(signing_pubkey) (20 bytes)
                    └── Short identifier for message attribution
```

### Key Rotation

```
key_index=0 ──► signing_key_A ──► signing_id_A  (announced in share)
                  │
                  ├── Signs messages M1, M2, M3
                  │
                  └── COMPROMISED! Attacker has signing_key_A
                        │
key_index=1 ──► signing_key_B ──► signing_id_B  (announced in NEW share)
                  │
                  ├── SigningKeyRegistry sees key_index=1 > 0
                  ├── REVOKES signing_id_A ──► M1, M2, M3 now UNVERIFIABLE
                  └── Signs new messages with signing_key_B
```

**Properties:**
- Master key stays secret — signing key is one-way derived
- Compromise of signing key only affects message auth, not mining rewards
- Key rotation is immediate — just mine one share with new key_index
- Old messages become unverifiable (revoked), preventing impersonation
- No PKI, no key exchange — trust anchored entirely to share PoW

### Security Analysis

| Attack | Mitigation |
|--------|-----------|
| Steal master private key | Signing key is derived via HMAC — can't reverse |
| Steal signing key | Rotate key_index — old key revoked, new key in next share |
| Forge messages from another miner | Need their signing_privkey — can't derive without master |
| Spam messages | Must mine valid shares (PoW cost) |
| Announce fake signing key | Must mine valid share — PoW prevents sybil |
| Replay old messages | Deduplicated by message_hash in ShareMessageStore |
| Impersonate after key rotation | Old signing_id is revoked — verification fails |

## Encryption Layers

Messages are NOT plaintext on the wire. Four layers of protection:

### Layer 1: Wire Obfuscation (all messages)

All message_data in shares is obfuscated with a symmetric key derived from
the p2pool network identifier. This prevents passive TCP traffic analysis
by anyone not running a p2pool node.

```
obfuscation_key = SHA256("p2pool-msg-obfuscate" || net.IDENTIFIER)
ciphertext = XOR(plaintext, keystream(obfuscation_key, nonce=share_hash))
```

This is NOT security — any p2pool node can deobfuscate. It's protection
against casual wire sniffing.

### Layer 2: Authentication (signed messages)

ECDSA signatures via DerivedSigningKey, as described above. Proves sender
identity and message integrity.

### Layer 3: Private Encryption (P2P messages)

For private miner-to-miner messages, ECDH key agreement provides
end-to-end encryption:

```
shared_secret = ECDH(sender_signing_privkey, recipient_signing_pubkey)
encryption_key = HMAC-SHA256(shared_secret, "p2pool-msg-encrypt")
ciphertext = AES-256-GCM(encryption_key, nonce=timestamp||counter, plaintext)
```

Only sender and recipient can decrypt. All other nodes relay the opaque
ciphertext without being able to read it. The `to` field (recipient
signing_id) is visible so nodes can check if a message is addressed to them.

### Layer 4: (Future) ZK Anonymous Reputation — Phase 3+

Zero-Knowledge proofs could enable anonymous messaging with reputation:
- Prove "I control addresses with combined hashrate weight > X%" without
  revealing which addresses
- Enables anonymous governance proposals and emergency alerts from major miners
- Requires zkSNARK/Bulletproof library — too complex for Phase 1

#### ZK Analysis

| What to prove | Without ZK | With ZK | Worth it? |
|---|---|---|---|
| "I mined this share" | Share PoW proves it | Same | ❌ No benefit |
| "I own this address" | Derived signing key | ZK proof of key knowledge | ⚠️ Marginal |
| "I have >X% hashrate" | Check share count | ZK proof without revealing address | ✅ Compelling |
| "I'm a node operator" | No way currently | ZK proof of uptime | ⚠️ Hard to verify |

**Decision**: ZK is deferred to Phase 3+. The one compelling use case
(anonymous weighted voting) doesn't justify the implementation complexity
in PyPy 2.7 for initial release.

## Banning System

Each node maintains a **local** ban list. Bans are not propagated — each
node operator decides independently what content to filter.

### Ban Types

```
Ban by signing_id:     Blocks a specific signing key
Ban by miner_address:  Blocks all keys from a payout address
Ban by content:        Regex/keyword filter on message payload
```

### Behavior

- Banned messages are dropped silently on the receiving node
- Banned messages are still relayed in shares (the banning node can't
  remove them from shares — they're PoW-protected)
- Other nodes see the messages normally unless they also ban the sender
- Ban list persisted to `data/litecoin/banned_senders.json`

### Configuration

```
--ban-sender <signing_id_hex>     Ban a signing key
--ban-address <LTC_address>       Ban all keys from an address
--ban-word <keyword>              Filter messages containing keyword
```

Or via API:
```
POST /msg/ban {"signing_id": "a1b2c3..."}
POST /msg/ban {"address": "LVzy9..."}
DELETE /msg/ban {"signing_id": "a1b2c3..."}
```

## UI/UX Design

### Key Setup (One-Time, Offline)

```
Miner's secure machine:
  $ python derive_signing_key.py <master_WIF> 0
  → signing_key_WIF (safe to give to node)

P2Pool node config:
  --signing-key <signing_key_WIF>
  --signing-key-index 0
```

The node NEVER sees the master private key. The derived signing key
is safe to store in config files and expose to the node process.

### Sending Messages

```
# Via web API
curl -X POST http://localhost:9327/msg/send \
  -d '{"type":"chat","text":"hello miners!"}'

# Private message to a specific miner
curl -X POST http://localhost:9327/msg/send \
  -d '{"type":"chat","text":"secret msg","to":"<signing_id_hex>"}'

# Pool announcement
curl -X POST http://localhost:9327/msg/send \
  -d '{"type":"announce","text":"upgrading in 1hr"}'

# Emergency alert
curl -X POST http://localhost:9327/msg/send \
  -d '{"type":"alert","text":"critical bug, upgrade now"}'
```

Messages are queued and embedded in the next mined share.

### Viewing Messages — BBS (Bulletin Board System)

The node serves a BBS-style web page at `http://node:9327/static/bbs.html`:

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
│  Send: [____________________________] [Chat] [Announce] │
└─────────────────────────────────────────────────────────┘
```

### API Endpoints

```
GET  /msg/recent           Recent messages (all types)
GET  /msg/chat             Miner chat messages only
GET  /msg/announcements    Pool announcements only
GET  /msg/alerts           Emergency alerts only
GET  /msg/status           Node status messages
GET  /msg/keys             Signing key registry
GET  /msg/identity         This node's signing identity
GET  /msg/stats            Messaging system statistics
POST /msg/send             Queue a message for next share
POST /msg/ban              Ban a sender
POST /msg/rotate_key       Rotate signing key (increment key_index)
DELETE /msg/ban            Unban a sender
```

## Message Types

| Type | ID | Signed? | Persistent? | Purpose |
|------|----|---------|-------------|---------|
| NODE_STATUS | 0x01 | Optional | No | Node health: version, uptime, hashrate, peers |
| MINER_MESSAGE | 0x02 | **Required** | Yes | Miner-to-miner text chat |
| POOL_ANNOUNCE | 0x03 | **Required** | Yes | Node operator announcements |
| VERSION_SIGNAL | 0x04 | Optional | No | Extended version signaling with metadata |
| MERGED_STATUS | 0x05 | Optional | No | Merged mining chain status reports |
| EMERGENCY | 0x10 | **Required** | Yes | Security alerts, critical upgrade notices |

## Message Flags

```
Bit 0 (0x01): FLAG_HAS_SIGNATURE  — Message carries an ECDSA signature
Bit 1 (0x02): FLAG_BROADCAST      — Relay to peers (vs. local-only)
Bit 2 (0x04): FLAG_PERSISTENT     — Store in message history (vs. ephemeral)
Bits 3-7:     Reserved for future use
```

## Limits

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max payload per message | 220 bytes | Fits a tweet-length message + JSON metadata |
| Max messages per share | 3 | Keeps share overhead small |
| Max total message bytes per share | 512 bytes | ~0.5 KB overhead on ~2 KB share |
| Signing key announcement | 57 bytes | signing_id(20) + key_index(4) + pubkey(33) |
| Message expiry | 24 hours | Prevents unbounded memory growth |
| Message store capacity | 1000 messages | ~24 hours at typical share rates |

## Use Cases

### 1. Miner Chat
Miners communicate in real-time via signed text messages. Only verified shares
can carry messages — this is the most expensive chat system in the world
(you literally need to perform PoW to send a message).

### 2. Pool Status Dashboard
Node operators broadcast health status: hashrate, peer count, merged chain
availability. Other nodes display this on their dashboards.

### 3. Emergency Alerts
Critical security notices propagate through the share chain. Since they're
signed and PoW-protected, they can't be spoofed.

### 4. Upgrade Coordination
Extended version signaling carries feature flags and capability lists,
enabling richer negotiation than the existing `desired_version` integer.

### 5. Merged Mining Discovery
Nodes announce which merged chains they support and their block heights,
enabling automatic peer discovery for new merged mining chains.

## Implementation Files

| File | Purpose |
|------|---------|
| `p2pool/share_messages.py` | Core module: messages, signing, registry, store |
| `SHARE_MESSAGING_DESIGN.md` | This document — architecture and rationale |
| `SHARE_MESSAGING_PROTOCOL.md` | Wire format specification |
| `SHARE_MESSAGING_INTEGRATION.md` | Integration plan for data.py, work.py, web.py |

## Integration Points (Summary)

1. **data.py**: Add `message_data` to V36 `ref_type`, process in share verification
2. **work.py**: Embed queued messages when generating new shares
3. **web.py**: API endpoints for viewing messages and queuing outbound messages
4. **p2p.py**: No changes needed — messages travel with shares automatically

See `SHARE_MESSAGING_INTEGRATION.md` for detailed integration plan.
