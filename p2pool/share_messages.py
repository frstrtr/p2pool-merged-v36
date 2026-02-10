"""
P2Pool Share-Based Messaging System (V36 Integrated)

Messages are embedded directly in V36 shares via ref_hash extension.
Protected by share PoW -- only miners who produce valid shares can send messages.
This prevents spam without any additional infrastructure.

Architecture:
- Messages stored in V36 ref_type as 'message_data' field
- Included in ref_hash computation (PoW-protected)
- NOT included in coinbase/gentx (never written to parent blockchain)
- Propagated via existing p2pool P2P share protocol (no new message types)
- Signed with derived signing keys (master private key stays secret)

Derived Signing Key Scheme:
  signing_privkey = HMAC-SHA256(master_privkey, "p2pool-msg-v1" || key_index_le32)
  signing_pubkey  = secp256k1_point(signing_privkey)
  signing_id      = HASH160(signing_pubkey_compressed)

  - master_privkey: miner's payout address private key (NEVER exposed)
  - key_index: uint32, incremented for key rotation
  - signing_id: 20-byte identifier announced in shares, used for verification
  - Key rotation: new key_index -> new signing_privkey -> old signatures unverifiable
  - Trust anchor: must mine a valid share to announce a signing_id (PoW anti-spam)

Message Types:
  0x01 NODE_STATUS    - Node health/capability announcements
  0x02 MINER_MESSAGE  - Miner-to-miner text messages (signed)
  0x03 POOL_ANNOUNCE  - Node operator announcements
  0x04 VERSION_SIGNAL - Extended version signaling with metadata
  0x05 MERGED_STATUS  - Merged mining chain status
  0x10 EMERGENCY      - Security/upgrade alerts

Wire Format (per message in share ref_data):
  [type:1] [flags:1] [timestamp:4] [payload_len:2] [payload:N]
  [signing_id:20] [sig_len:1] [signature:M]

Envelope Format (in share ref_type.message_data):
  [version:1] [flags:1] [msg_count:1] [announcement_len:1]
  [signing_key_announcement:57?]  (signing_id:20 + key_index:4 + pubkey:33)
  [message1] [message2] ...
"""

from __future__ import division

import struct
import time
import hashlib
import hmac
import json


# ============================================================================
# Constants
# ============================================================================

# Message types
MSG_NODE_STATUS = 0x01
MSG_MINER_MESSAGE = 0x02
MSG_POOL_ANNOUNCE = 0x03
MSG_VERSION_SIGNAL = 0x04
MSG_MERGED_STATUS = 0x05
MSG_EMERGENCY = 0x10

# Message flags
FLAG_HAS_SIGNATURE = 0x01
FLAG_BROADCAST = 0x02
FLAG_PERSISTENT = 0x04

# Limits
MAX_MESSAGE_PAYLOAD = 220       # bytes per message payload
MAX_MESSAGES_PER_SHARE = 3      # max messages embedded in one share
MAX_TOTAL_MESSAGE_BYTES = 512   # total bytes for all messages in one share
MAX_MESSAGE_AGE = 86400         # 24 hours -- messages older than this are pruned
MAX_MESSAGE_HISTORY = 1000      # max messages to keep in memory

# Signing key derivation
SIGNING_KEY_DOMAIN = b'p2pool-msg-v1'  # Domain separator for HMAC derivation
SIGNING_KEY_ANNOUNCEMENT_SIZE = 57     # signing_id(20) + key_index(4) + pubkey(33)

# Message type names for display
MESSAGE_TYPE_NAMES = {
    MSG_NODE_STATUS: 'NODE_STATUS',
    MSG_MINER_MESSAGE: 'MINER_MESSAGE',
    MSG_POOL_ANNOUNCE: 'POOL_ANNOUNCE',
    MSG_VERSION_SIGNAL: 'VERSION_SIGNAL',
    MSG_MERGED_STATUS: 'MERGED_STATUS',
    MSG_EMERGENCY: 'EMERGENCY',
}


# ============================================================================
# Crypto helpers -- isolated for easy replacement/testing
# ============================================================================

def _derive_pubkey(privkey_bytes):
    """
    Derive secp256k1 public key from private key bytes.
    Returns (uncompressed_pubkey, compressed_pubkey).
    """
    try:
        import coincurve
        pk = coincurve.PrivateKey(privkey_bytes)
        compressed = pk.public_key.format(compressed=True)
        uncompressed = pk.public_key.format(compressed=False)
        return uncompressed, compressed
    except ImportError:
        pass

    try:
        import ecdsa
        sk = ecdsa.SigningKey.from_string(privkey_bytes, curve=ecdsa.SECP256k1)
        vk = sk.get_verifying_key()
        uncompressed = b'\x04' + vk.to_string()
        # Compress: prefix 02 if y is even, 03 if odd
        x = vk.to_string()[:32]
        y = vk.to_string()[32:]
        prefix = b'\x02' if (ord(y[-1:]) % 2 == 0) else b'\x03'
        compressed = prefix + x
        return uncompressed, compressed
    except ImportError:
        pass

    raise ImportError('No ECDSA library available (need coincurve or ecdsa)')


def _ecdsa_sign(privkey_bytes, message_hash):
    """Sign a 32-byte hash with secp256k1 ECDSA. Returns DER-encoded signature."""
    try:
        import coincurve
        pk = coincurve.PrivateKey(privkey_bytes)
        return pk.sign(message_hash, hasher=None)
    except ImportError:
        pass

    try:
        import ecdsa
        sk = ecdsa.SigningKey.from_string(privkey_bytes, curve=ecdsa.SECP256k1)
        return sk.sign_digest(message_hash, sigencode=ecdsa.util.sigencode_der)
    except ImportError:
        pass

    return b''


def _ecdsa_verify(pubkey_compressed, message_hash, signature):
    """Verify ECDSA signature against compressed public key."""
    try:
        import coincurve
        pk = coincurve.PublicKey(pubkey_compressed)
        return pk.verify(signature, message_hash, hasher=None)
    except ImportError:
        pass

    try:
        import ecdsa
        if pubkey_compressed[0:1] in (b'\x02', b'\x03'):
            vk = ecdsa.VerifyingKey.from_string(
                pubkey_compressed, curve=ecdsa.SECP256k1)
        else:
            vk = ecdsa.VerifyingKey.from_string(
                pubkey_compressed[1:], curve=ecdsa.SECP256k1)
        return vk.verify_digest(signature, message_hash,
                                sigdecode=ecdsa.util.sigdecode_der)
    except (ImportError, ecdsa.BadSignatureError):
        pass
    except Exception:
        pass

    return False


def _hash160(data):
    """RIPEMD160(SHA256(data)) -- standard Bitcoin address hash."""
    sha = hashlib.sha256(data).digest()
    try:
        ripemd = hashlib.new('ripemd160')
        ripemd.update(sha)
        return ripemd.digest()
    except (ValueError, AttributeError):
        # Fallback for platforms without ripemd160
        try:
            from p2pool.bitcoin.data import hash160
            return hash160(data)
        except ImportError:
            raise NotImplementedError('No RIPEMD-160 available')


# ============================================================================
# Derived Signing Key
# ============================================================================

class DerivedSigningKey(object):
    """
    Signing key derived from miner's master private key via HMAC.

    The master private key (payout address key) is NEVER exposed.
    Instead, a one-way derived signing key is used for message signing.

    Derivation:
      signing_privkey = HMAC-SHA256(master_privkey, "p2pool-msg-v1" || key_index_le32)
      signing_pubkey  = secp256k1_point(signing_privkey)
      signing_id      = HASH160(signing_pubkey_compressed)

    Key rotation:
      Increment key_index to generate a new signing key.
      Old messages signed with the previous key become unverifiable.
      The new signing_id is announced in the miner's next share.
    """

    __slots__ = ['key_index', '_signing_privkey', '_signing_pubkey',
                 '_signing_pubkey_compressed', 'signing_id']

    def __init__(self, master_privkey_bytes, key_index=0):
        """
        Derive a signing key from the master private key.

        Args:
            master_privkey_bytes: 32-byte master private key (payout address key)
            key_index: uint32 rotation index (0 = first key, 1 = rotated, etc.)
        """
        if len(master_privkey_bytes) != 32:
            raise ValueError('Master private key must be 32 bytes, got %d' %
                             len(master_privkey_bytes))

        self.key_index = key_index

        # Derive signing private key via HMAC-SHA256
        # domain = "p2pool-msg-v1" || key_index as little-endian uint32
        domain = SIGNING_KEY_DOMAIN + struct.pack('<I', key_index)
        self._signing_privkey = hmac.new(
            master_privkey_bytes, domain, hashlib.sha256
        ).digest()

        # Derive public key and signing_id
        self._signing_pubkey, self._signing_pubkey_compressed = \
            _derive_pubkey(self._signing_privkey)

        # signing_id = HASH160(compressed_pubkey)
        self.signing_id = _hash160(self._signing_pubkey_compressed)

    def sign(self, message_hash):
        """
        Sign a 32-byte message hash with the derived signing key.
        Returns DER-encoded ECDSA signature bytes.
        """
        return _ecdsa_sign(self._signing_privkey, message_hash)

    def get_announcement(self):
        """
        Get the signing key announcement dict to embed in a share.

        This is what other nodes use to verify our signatures:
        - signing_id (20 bytes): HASH160 identifier
        - key_index (4 bytes): rotation counter
        - signing_pubkey (33 bytes): compressed public key for verification

        Total: 57 bytes in share ref_data
        """
        return {
            'signing_id': self.signing_id,
            'key_index': self.key_index,
            'signing_pubkey': self._signing_pubkey_compressed,
        }

    def pack_announcement(self):
        """
        Pack signing key announcement for embedding in share ref_data.
        Format: [signing_id:20] [key_index:4] [signing_pubkey:33] = 57 bytes
        """
        return (
            self.signing_id +
            struct.pack('<I', self.key_index) +
            self._signing_pubkey_compressed
        )

    @staticmethod
    def unpack_announcement(data, offset=0):
        """
        Unpack signing key announcement from share ref_data.
        Returns (signing_id, key_index, signing_pubkey, bytes_consumed).
        Returns (None, 0, None, 0) on failure.
        """
        if len(data) - offset < SIGNING_KEY_ANNOUNCEMENT_SIZE:
            return None, 0, None, 0

        signing_id = data[offset:offset + 20]
        key_index = struct.unpack_from('<I', data, offset + 20)[0]
        signing_pubkey = data[offset + 24:offset + 57]

        # Verify signing_id matches the pubkey
        expected_id = _hash160(signing_pubkey)
        if expected_id != signing_id:
            return None, 0, None, SIGNING_KEY_ANNOUNCEMENT_SIZE

        return signing_id, key_index, signing_pubkey, SIGNING_KEY_ANNOUNCEMENT_SIZE


# ============================================================================
# Signing Key Registry
# ============================================================================

class SigningKeyRegistry(object):
    """
    Registry of known signing keys, learned from verified shares.

    Each miner's share carries their (signing_id, key_index, signing_pubkey).
    When a miner rotates their key (increments key_index), the old signing_id
    is marked as revoked. Messages signed with revoked keys are rejected.

    Trust model:
    - A signing key is trusted if it was announced in a verified share
    - If a miner announces a higher key_index, all lower key_indexes are revoked
    - Share PoW prevents announcement spam (must mine a valid share to announce)
    """

    def __init__(self):
        # {miner_address: {signing_id_hex: {key_index, signing_pubkey, first_seen,
        #                                    share_hash, revoked}}}
        self.keys = {}
        # {signing_id_hex: miner_address} -- reverse lookup
        self.id_to_address = {}
        # {miner_address: current_key_index} -- highest known key_index per miner
        self.current_key_index = {}

    def register_key(self, miner_address, signing_id, key_index, signing_pubkey,
                     share_hash=None, timestamp=None):
        """
        Register a signing key announcement from a verified share.

        If key_index is higher than the previously known index for this miner,
        all older keys are revoked (key rotation).

        Returns True if this is a new or updated key.
        """
        signing_id_hex = signing_id.encode('hex') if isinstance(
            signing_id, bytes) else signing_id

        if miner_address not in self.keys:
            self.keys[miner_address] = {}
            self.current_key_index[miner_address] = -1

        # Check if this is a key rotation (higher key_index)
        if key_index > self.current_key_index.get(miner_address, -1):
            # Revoke all older keys for this miner
            for old_id, old_info in self.keys[miner_address].items():
                if old_info['key_index'] < key_index:
                    old_info['revoked'] = True

            self.current_key_index[miner_address] = key_index

        # Register the key (or update if already known)
        is_new = signing_id_hex not in self.keys[miner_address]
        self.keys[miner_address][signing_id_hex] = {
            'key_index': key_index,
            'signing_pubkey': signing_pubkey,
            'first_seen': timestamp or time.time(),
            'share_hash': share_hash,
            'revoked': key_index < self.current_key_index.get(miner_address, 0),
        }

        self.id_to_address[signing_id_hex] = miner_address
        return is_new

    def is_key_valid(self, signing_id):
        """Check if a signing_id is known and not revoked."""
        signing_id_hex = signing_id.encode('hex') if isinstance(
            signing_id, bytes) else signing_id

        miner_address = self.id_to_address.get(signing_id_hex)
        if miner_address is None:
            return False

        key_info = self.keys.get(miner_address, {}).get(signing_id_hex)
        if key_info is None:
            return False

        return not key_info.get('revoked', False)

    def get_pubkey_for_id(self, signing_id):
        """Get the compressed public key for a signing_id, or None if unknown/revoked."""
        signing_id_hex = signing_id.encode('hex') if isinstance(
            signing_id, bytes) else signing_id

        miner_address = self.id_to_address.get(signing_id_hex)
        if miner_address is None:
            return None

        key_info = self.keys.get(miner_address, {}).get(signing_id_hex)
        if key_info is None or key_info.get('revoked', False):
            return None

        return key_info['signing_pubkey']

    def get_miner_for_id(self, signing_id):
        """Get the miner address associated with a signing_id."""
        signing_id_hex = signing_id.encode('hex') if isinstance(
            signing_id, bytes) else signing_id
        return self.id_to_address.get(signing_id_hex)

    def get_miner_current_key(self, miner_address):
        """Get the current (non-revoked) signing key info for a miner."""
        if miner_address not in self.keys:
            return None

        current_idx = self.current_key_index.get(miner_address, -1)
        for signing_id_hex, key_info in self.keys[miner_address].items():
            if key_info['key_index'] == current_idx and \
                    not key_info.get('revoked', False):
                return {
                    'signing_id': signing_id_hex,
                    'key_index': key_info['key_index'],
                    'signing_pubkey': key_info['signing_pubkey'],
                }
        return None

    def to_json(self):
        """Serialize registry for API/debugging."""
        result = {}
        for miner_addr, keys in self.keys.items():
            result[miner_addr] = {
                'current_key_index': self.current_key_index.get(miner_addr, -1),
                'keys': {}
            }
            for signing_id_hex, key_info in keys.items():
                result[miner_addr]['keys'][signing_id_hex] = {
                    'key_index': key_info['key_index'],
                    'revoked': key_info.get('revoked', False),
                    'first_seen': key_info.get('first_seen', 0),
                }
        return result


# ============================================================================
# Share Message
# ============================================================================

class ShareMessage(object):
    """A single message embedded in a p2pool share's ref_data."""

    __slots__ = ['msg_type', 'flags', 'timestamp', 'payload', 'signature',
                 'signing_id', 'sender_address', 'share_hash', 'verified']

    def __init__(self, msg_type, payload, flags=FLAG_BROADCAST | FLAG_PERSISTENT,
                 timestamp=None, signature=b'', signing_id=None,
                 sender_address=None, share_hash=None):
        self.msg_type = msg_type
        self.flags = flags
        self.timestamp = timestamp or int(time.time())
        self.payload = payload if isinstance(payload, bytes) else payload.encode('utf-8')
        self.signature = signature
        self.signing_id = signing_id    # 20-byte HASH160 of signing pubkey
        self.sender_address = sender_address
        self.share_hash = share_hash
        self.verified = False

    @property
    def type_name(self):
        return MESSAGE_TYPE_NAMES.get(self.msg_type, 'UNKNOWN_0x%02x' % self.msg_type)

    @property
    def has_signature(self):
        return bool(self.flags & FLAG_HAS_SIGNATURE)

    @property
    def is_broadcast(self):
        return bool(self.flags & FLAG_BROADCAST)

    @property
    def is_persistent(self):
        return bool(self.flags & FLAG_PERSISTENT)

    @property
    def age(self):
        return time.time() - self.timestamp

    def message_hash(self):
        """
        Double-SHA256 of message content.
        Used for signing, verification, and deduplication.
        """
        data = struct.pack('<BBI', self.msg_type, self.flags, self.timestamp) + \
            self.payload
        return hashlib.sha256(hashlib.sha256(data).digest()).digest()

    def sign(self, derived_key):
        """
        Sign this message with a DerivedSigningKey.
        Sets the signature, signing_id, and FLAG_HAS_SIGNATURE flag.
        """
        self.flags |= FLAG_HAS_SIGNATURE
        self.signing_id = derived_key.signing_id
        self.signature = derived_key.sign(self.message_hash())

    def verify(self, key_registry):
        """
        Verify this message's signature against the signing key registry.

        Returns True if:
        1. Message has FLAG_HAS_SIGNATURE and non-empty signature
        2. signing_id is known in the registry (announced in a verified share)
        3. signing_id is not revoked (key hasn't been rotated to a higher index)
        4. ECDSA signature is valid against the registered public key
        """
        if not self.has_signature or not self.signing_id or not self.signature:
            self.verified = False
            return False

        # Look up the public key for this signing_id
        pubkey = key_registry.get_pubkey_for_id(self.signing_id)
        if pubkey is None:
            self.verified = False
            return False

        # Verify ECDSA signature
        msg_hash = self.message_hash()
        self.verified = _ecdsa_verify(pubkey, msg_hash, self.signature)

        # Also populate sender_address from registry
        if self.verified:
            self.sender_address = key_registry.get_miner_for_id(self.signing_id)

        return self.verified

    def pack(self):
        """
        Serialize message to bytes for embedding in share ref_data.

        Wire format:
          [type:1] [flags:1] [timestamp:4] [payload_len:2] [payload:N]
          [signing_id:20] [sig_len:1] [signature:M]

        Min size: 8 + 0 + 20 + 1 + 0 = 29 bytes (empty payload, no signature)
        Max size: 8 + 220 + 20 + 1 + 73 = 322 bytes
        """
        if len(self.payload) > MAX_MESSAGE_PAYLOAD:
            raise ValueError('Message payload too large: %d > %d' % (
                len(self.payload), MAX_MESSAGE_PAYLOAD))

        sig = self.signature or b''
        if len(sig) > 73:  # Max DER-encoded ECDSA signature
            raise ValueError('Signature too large: %d' % len(sig))

        sid = self.signing_id or (b'\x00' * 20)

        return (
            struct.pack('<BBIH', self.msg_type, self.flags, self.timestamp,
                        len(self.payload)) +
            self.payload +
            sid +                           # 20 bytes signing_id
            struct.pack('<B', len(sig)) +
            sig
        )

    @classmethod
    def unpack(cls, data, offset=0):
        """
        Deserialize message from bytes.
        Returns (message, new_offset).
        """
        if len(data) - offset < 8:
            raise ValueError('Message data too short: %d bytes at offset %d' % (
                len(data) - offset, offset))

        msg_type, flags, timestamp, payload_len = struct.unpack_from(
            '<BBIH', data, offset)
        offset += 8

        if payload_len > MAX_MESSAGE_PAYLOAD:
            raise ValueError('Payload length %d exceeds maximum %d' % (
                payload_len, MAX_MESSAGE_PAYLOAD))

        if len(data) - offset < payload_len:
            raise ValueError('Not enough data for payload: need %d, have %d' % (
                payload_len, len(data) - offset))

        payload = data[offset:offset + payload_len]
        offset += payload_len

        # Read 20-byte signing_id
        if len(data) - offset < 20:
            raise ValueError('Not enough data for signing_id')
        signing_id = data[offset:offset + 20]
        offset += 20

        # Check if signing_id is all zeros (unsigned message)
        if signing_id == b'\x00' * 20:
            signing_id = None

        # Read signature
        if len(data) - offset < 1:
            raise ValueError('Not enough data for signature length')
        sig_len = struct.unpack_from('<B', data, offset)[0]
        offset += 1

        if len(data) - offset < sig_len:
            raise ValueError('Not enough data for signature: need %d, have %d' % (
                sig_len, len(data) - offset))
        signature = data[offset:offset + sig_len] if sig_len > 0 else b''
        offset += sig_len

        msg = cls(
            msg_type=msg_type,
            payload=payload,
            flags=flags,
            timestamp=timestamp,
            signature=signature,
            signing_id=signing_id,
        )

        return msg, offset

    def to_dict(self):
        """Convert to JSON-serializable dict for API/display."""
        result = {
            'type': self.type_name,
            'type_id': self.msg_type,
            'timestamp': self.timestamp,
            'age': int(self.age),
            'flags': {
                'signed': self.has_signature,
                'broadcast': self.is_broadcast,
                'persistent': self.is_persistent,
            },
            'verified': self.verified,
        }

        if self.signing_id:
            result['signing_id'] = self.signing_id.encode('hex')

        # Decode payload based on message type
        if self.msg_type in (MSG_MINER_MESSAGE, MSG_POOL_ANNOUNCE, MSG_EMERGENCY):
            try:
                result['text'] = self.payload.decode('utf-8')
            except UnicodeDecodeError:
                result['text'] = self.payload.encode('hex')
        elif self.msg_type in (MSG_NODE_STATUS, MSG_MERGED_STATUS, MSG_VERSION_SIGNAL):
            try:
                result['data'] = json.loads(self.payload)
            except (ValueError, TypeError):
                result['raw'] = self.payload.encode('hex')
        else:
            result['raw'] = self.payload.encode('hex')

        if self.sender_address:
            result['sender'] = self.sender_address
        if self.share_hash is not None:
            result['share_hash'] = '%064x' % self.share_hash if isinstance(
                self.share_hash, (int, long)) else str(self.share_hash)

        return result

    def __repr__(self):
        return '<ShareMessage %s sender=%s verified=%s age=%ds %d bytes>' % (
            self.type_name, self.sender_address or '?',
            self.verified, int(self.age), len(self.payload))


# ============================================================================
# Pack/unpack message lists for share embedding
# ============================================================================

def pack_share_messages(messages, signing_key_announcement=None):
    """
    Pack messages + optional signing key announcement for share ref_data.

    Envelope format:
      [version:1] [flags:1] [msg_count:1] [announcement_len:1]
      [announcement:N] [messages...]

    Returns bytes to embed in share ref_type.message_data,
    or empty bytes if nothing to pack.
    """
    if not messages and not signing_key_announcement:
        return b''

    if messages and len(messages) > MAX_MESSAGES_PER_SHARE:
        messages = messages[:MAX_MESSAGES_PER_SHARE]

    version = 1   # Message protocol version
    flags = 0
    if signing_key_announcement:
        flags |= 0x01  # Has signing key announcement

    announcement_data = b''
    if signing_key_announcement:
        announcement_data = signing_key_announcement

    msg_count = len(messages) if messages else 0

    packed = struct.pack('<BBBB', version, flags, msg_count,
                         len(announcement_data))
    packed += announcement_data

    if messages:
        for msg in messages:
            packed += msg.pack()

    if len(packed) > MAX_TOTAL_MESSAGE_BYTES:
        raise ValueError('Total message data %d exceeds limit %d' % (
            len(packed), MAX_TOTAL_MESSAGE_BYTES))

    return packed


def unpack_share_messages(data):
    """
    Unpack messages + signing key announcement from share ref_data.

    Returns (messages_list, signing_key_info_dict_or_None).
    """
    if not data or len(data) < 4:
        return [], None

    version, flags, msg_count, announcement_len = struct.unpack_from(
        '<BBBB', data, 0)
    offset = 4

    if version != 1:
        return [], None  # Unknown version -- skip gracefully

    # Parse signing key announcement if present
    signing_key_info = None
    if flags & 0x01 and announcement_len > 0:
        if len(data) - offset >= announcement_len:
            ann_data = data[offset:offset + announcement_len]
            signing_id, key_index, signing_pubkey, consumed = \
                DerivedSigningKey.unpack_announcement(ann_data)
            if signing_id is not None:
                signing_key_info = {
                    'signing_id': signing_id,
                    'key_index': key_index,
                    'signing_pubkey': signing_pubkey,
                }
            offset += announcement_len
        else:
            offset += announcement_len  # Skip malformed announcement

    # Parse messages
    messages = []
    if msg_count > MAX_MESSAGES_PER_SHARE:
        msg_count = MAX_MESSAGES_PER_SHARE

    for i in range(msg_count):
        try:
            msg, offset = ShareMessage.unpack(data, offset)
            messages.append(msg)
        except (ValueError, struct.error):
            break  # Malformed message -- keep what we have

    return messages, signing_key_info


# ============================================================================
# Message hash for ref_hash integration
# ============================================================================

def compute_message_data_hash(packed_message_data):
    """
    Compute hash of packed message data for inclusion in ref_hash.

    This is what makes messages PoW-protected:
    ref_hash = merkle(pack(identifier, share_info, message_data_hash))

    If no messages, returns zero hash (32 zero bytes).
    """
    if not packed_message_data:
        return b'\x00' * 32

    return hashlib.sha256(hashlib.sha256(packed_message_data).digest()).digest()


# ============================================================================
# Message Store
# ============================================================================

class ShareMessageStore(object):
    """
    In-memory store for share messages with deduplication, pruning,
    and integrated signing key registry.

    Messages are indexed by share_hash, sender, type, and timestamp.
    The SigningKeyRegistry is automatically maintained from share announcements.
    """

    def __init__(self, max_messages=MAX_MESSAGE_HISTORY, max_age=MAX_MESSAGE_AGE):
        self.messages = []          # ordered newest first
        self.message_hashes = set() # for deduplication
        self.max_messages = max_messages
        self.max_age = max_age
        self.key_registry = SigningKeyRegistry()

    def process_share(self, share_hash, sender_address, packed_message_data):
        """
        Process messages from a share. This is the main entry point called
        during share verification in data.py.

        1. Unpacks messages and signing key announcement from packed data
        2. Registers any signing key announcement in the key registry
        3. Verifies message signatures against the registry
        4. Stores valid messages with deduplication

        Args:
            share_hash: int -- hash of the share carrying these messages
            sender_address: str -- payout address of the miner who produced the share
            packed_message_data: bytes -- raw message_data from share ref_type

        Returns number of messages added.
        """
        messages, signing_key_info = unpack_share_messages(packed_message_data)

        # Register signing key if present
        if signing_key_info:
            self.key_registry.register_key(
                miner_address=sender_address,
                signing_id=signing_key_info['signing_id'],
                key_index=signing_key_info['key_index'],
                signing_pubkey=signing_key_info['signing_pubkey'],
                share_hash=share_hash,
            )

        # Process each message
        added = 0
        for msg in messages:
            msg.share_hash = share_hash
            msg.sender_address = sender_address

            # Verify signature if present
            if msg.has_signature:
                msg.verify(self.key_registry)

            if self._add_message(msg):
                added += 1

        return added

    def _add_message(self, msg):
        """Add a message if not duplicate and not too old."""
        msg_hash = msg.message_hash()
        msg_hash_hex = msg_hash.encode('hex')

        if msg_hash_hex in self.message_hashes:
            return False

        if msg.age > self.max_age:
            return False

        self.messages.append(msg)
        self.message_hashes.add(msg_hash_hex)
        self.messages.sort(key=lambda m: m.timestamp, reverse=True)
        self._prune()
        return True

    def add_local_message(self, msg, sender_address=None):
        """
        Add a locally-generated message (not from a share).
        Used for node status messages that haven't been embedded in a share yet.
        """
        if sender_address:
            msg.sender_address = sender_address
        return self._add_message(msg)

    def _prune(self):
        """Remove old and excess messages."""
        cutoff = time.time() - self.max_age

        expired = [m for m in self.messages if m.timestamp < cutoff]
        for m in expired:
            self.messages.remove(m)
            self.message_hashes.discard(m.message_hash().encode('hex'))

        while len(self.messages) > self.max_messages:
            oldest = self.messages.pop()
            self.message_hashes.discard(oldest.message_hash().encode('hex'))

    def get_messages(self, msg_type=None, sender=None, since=None,
                     verified_only=False, limit=50):
        """Query messages with optional filters."""
        results = self.messages

        if msg_type is not None:
            results = [m for m in results if m.msg_type == msg_type]
        if sender is not None:
            results = [m for m in results if m.sender_address == sender]
        if since is not None:
            results = [m for m in results if m.timestamp >= since]
        if verified_only:
            results = [m for m in results if m.verified]

        return results[:limit]

    def get_recent(self, limit=20):
        """Get most recent messages of all types."""
        return self.messages[:limit]

    def get_chat(self, limit=50):
        """Get miner-to-miner chat messages (signed and verified only)."""
        return self.get_messages(
            msg_type=MSG_MINER_MESSAGE, verified_only=True, limit=limit)

    def get_all_chat(self, limit=50):
        """Get all miner-to-miner chat messages (including unverified)."""
        return self.get_messages(msg_type=MSG_MINER_MESSAGE, limit=limit)

    def get_announcements(self, limit=10):
        """Get pool operator announcements."""
        return self.get_messages(msg_type=MSG_POOL_ANNOUNCE, limit=limit)

    def get_alerts(self, limit=5):
        """Get emergency alerts."""
        return self.get_messages(msg_type=MSG_EMERGENCY, limit=limit)

    def get_node_statuses(self, limit=20):
        """Get node status reports."""
        return self.get_messages(msg_type=MSG_NODE_STATUS, limit=limit)

    def to_json(self, **kwargs):
        """Get messages as JSON-serializable list."""
        messages = self.get_messages(**kwargs)
        return [m.to_dict() for m in messages]

    @property
    def stats(self):
        """Get store statistics."""
        type_counts = {}
        for m in self.messages:
            name = m.type_name
            type_counts[name] = type_counts.get(name, 0) + 1

        unique_senders = set(
            m.sender_address for m in self.messages if m.sender_address)

        return {
            'total_messages': len(self.messages),
            'unique_senders': len(unique_senders),
            'senders': list(unique_senders),
            'by_type': type_counts,
            'oldest_timestamp': self.messages[-1].timestamp if self.messages else None,
            'newest_timestamp': self.messages[0].timestamp if self.messages else None,
            'signed_count': sum(1 for m in self.messages if m.has_signature),
            'verified_count': sum(1 for m in self.messages if m.verified),
            'known_signing_keys': len(self.key_registry.id_to_address),
            'key_registry': self.key_registry.to_json(),
        }


# ============================================================================
# Message Builders -- convenience functions
# ============================================================================

def build_node_status(version, uptime, hashrate, share_count, peers,
                      merged_chains=None, capabilities=None):
    """
    Build a NODE_STATUS message with node health information.

    Payload is compact JSON:
      {"v":"13.4","up":3600,"hr":1500000,"sc":8640,"p":3,"mc":["DOGE"],"cap":["v36","mm"]}
    """
    status = {
        'v': version,
        'up': int(uptime),
        'hr': int(hashrate),
        'sc': share_count,
        'p': peers,
    }
    if merged_chains:
        status['mc'] = merged_chains   # e.g., ['DOGE', 'BEL']
    if capabilities:
        status['cap'] = capabilities   # e.g., ['v36', 'mm', 'segwit']

    payload = json.dumps(status, separators=(',', ':'))
    return ShareMessage(
        msg_type=MSG_NODE_STATUS,
        payload=payload,
        flags=FLAG_BROADCAST,  # ephemeral, broadcast but not persistent
    )


def build_miner_message(text):
    """
    Build a MINER_MESSAGE for miner-to-miner chat.
    Must be signed with DerivedSigningKey before embedding in share.
    """
    if isinstance(text, unicode):
        text = text.encode('utf-8')
    if len(text) > MAX_MESSAGE_PAYLOAD:
        text = text[:MAX_MESSAGE_PAYLOAD]
    return ShareMessage(
        msg_type=MSG_MINER_MESSAGE,
        payload=text,
        flags=FLAG_HAS_SIGNATURE | FLAG_BROADCAST | FLAG_PERSISTENT,
    )


def build_pool_announcement(text):
    """Build a POOL_ANNOUNCE message from node operator."""
    if isinstance(text, unicode):
        text = text.encode('utf-8')
    if len(text) > MAX_MESSAGE_PAYLOAD:
        text = text[:MAX_MESSAGE_PAYLOAD]
    return ShareMessage(
        msg_type=MSG_POOL_ANNOUNCE,
        payload=text,
        flags=FLAG_HAS_SIGNATURE | FLAG_BROADCAST | FLAG_PERSISTENT,
    )


def build_merged_status(chain_name, symbol, height, block_value, blocks_found=0):
    """
    Build a MERGED_STATUS message with merged mining chain info.

    Payload is compact JSON:
      {"chain":"Dogecoin","sym":"DOGE","h":5000000,"bv":10000.0,"bf":3}
    """
    status = {
        'chain': chain_name,
        'sym': symbol,
        'h': height,
        'bv': block_value,
        'bf': blocks_found,
    }
    payload = json.dumps(status, separators=(',', ':'))
    return ShareMessage(
        msg_type=MSG_MERGED_STATUS,
        payload=payload,
        flags=FLAG_BROADCAST,
    )


def build_version_signal(version, features, extra=None):
    """
    Build a VERSION_SIGNAL message with extended version info.

    Payload is compact JSON:
      {"ver":36,"feat":["mm","segwit","mweb"],"proto":3600}
    """
    status = {
        'ver': version,
        'feat': features,
    }
    if extra:
        status.update(extra)
    payload = json.dumps(status, separators=(',', ':'))
    return ShareMessage(
        msg_type=MSG_VERSION_SIGNAL,
        payload=payload,
        flags=FLAG_BROADCAST,
    )


def build_emergency_alert(text):
    """
    Build an EMERGENCY alert message.
    Must be signed to be taken seriously by recipients.
    """
    if isinstance(text, unicode):
        text = text.encode('utf-8')
    if len(text) > MAX_MESSAGE_PAYLOAD:
        text = text[:MAX_MESSAGE_PAYLOAD]
    return ShareMessage(
        msg_type=MSG_EMERGENCY,
        payload=text,
        flags=FLAG_HAS_SIGNATURE | FLAG_BROADCAST | FLAG_PERSISTENT,
    )
