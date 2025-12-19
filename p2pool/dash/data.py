from __future__ import division

import hashlib
import random
import warnings
from Crypto.Hash import RIPEMD

import p2pool
from p2pool.util import math, pack, segwit_addr

def hash256(data):
    return pack.IntType(256).unpack(hashlib.sha256(hashlib.sha256(data).digest()).digest())

def hash160(data):
    #if data == '04ffd03de44a6e11b9917f3a29f9443283d9871c9d743ef30d5eddcd37094b64d1b3d8090496b53256786bf5c82932ec23c3b74d9f05a6f95a8b5529352656664b'.decode('hex'):
        #return 0x384f570ccc88ac2e7e00b026d1690a3fca63dd0 # hack for people who don't have openssl - this is the only value that p2pool ever hashes
    return pack.IntType(160).unpack(RIPEMD.new(hashlib.sha256(data).digest()).digest())

class ChecksummedType(pack.Type):
    def __init__(self, inner, checksum_func=lambda data: hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4]):
        self.inner = inner
        self.checksum_func = checksum_func
    
    def read(self, file):
        obj = self.inner.read(file)
        data = self.inner.pack(obj)
        
        calculated_checksum = self.checksum_func(data)
        checksum = file.read(len(calculated_checksum))
        if checksum != calculated_checksum:
            raise ValueError('invalid checksum')
        
        return obj
    
    def write(self, file, item):
        data = self.inner.pack(item)
        file.write(data)
        file.write(self.checksum_func(data))

class FloatingInteger(object):
    __slots__ = ['bits', '_target']
    
    @classmethod
    def from_target_upper_bound(cls, target):
        n = math.natural_to_string(target)
        if n and ord(n[0]) >= 128:
            n = '\x00' + n
        bits2 = (chr(len(n)) + (n + 3*chr(0))[:3])[::-1]
        bits = pack.IntType(32).unpack(bits2)
        return cls(bits)
    
    def __init__(self, bits, target=None):
        self.bits = bits
        self._target = None
        if target is not None and self.target != target:
            raise ValueError('target does not match')
    
    @property
    def target(self):
        res = self._target
        if res is None:
            res = self._target = math.shift_left(self.bits & 0x00ffffff, 8 * ((self.bits >> 24) - 3))
        return res
    
    def __hash__(self):
        return hash(self.bits)
    
    def __eq__(self, other):
        return self.bits == other.bits
    
    def __ne__(self, other):
        return not (self == other)
    
    def __cmp__(self, other):
        assert False
    
    def __repr__(self):
        return 'FloatingInteger(bits=%s, target=%s)' % (hex(self.bits), hex(self.target))

class FloatingIntegerType(pack.Type):
    _inner = pack.IntType(32)
    
    def read(self, file):
        bits = self._inner.read(file)
        return FloatingInteger(bits)
    
    def write(self, file, item):
        self._inner.write(file, item.bits)

address_type = pack.ComposedType([
    ('services', pack.IntType(64)),
    ('address', pack.IPV6AddressType()),
    ('port', pack.IntType(16, 'big')),
])

tx_type = pack.ComposedWithContextualOptionalsType([
    ('version', pack.IntType(16)),
    ('type', pack.IntType(16)),
    ('tx_ins', pack.ListType(pack.ComposedType([
        ('previous_output', pack.PossiblyNoneType(dict(hash=0, index=2**32 - 1), pack.ComposedType([
            ('hash', pack.IntType(256)),
            ('index', pack.IntType(32)),
        ]))),
        ('script', pack.VarStrType()),
        ('sequence', pack.PossiblyNoneType(2**32 - 1, pack.IntType(32))),
    ]))),
    ('tx_outs', pack.ListType(pack.ComposedType([
        ('value', pack.IntType(64)),
        ('script', pack.VarStrType()),
    ]))),
    ('lock_time', pack.IntType(32)),
    ('extra_payload', pack.ContextualOptionalType(pack.VarStrType(), lambda item: item['version'] >= 3 and item['type'] != 0)),
])

merkle_link_type = pack.ComposedType([
    ('branch', pack.ListType(pack.IntType(256))),
    ('index', pack.IntType(32)),
])

merkle_tx_type = pack.ComposedType([
    ('tx', tx_type),
    ('block_hash', pack.IntType(256)),
    ('merkle_link', merkle_link_type),
])

block_header_type = pack.ComposedType([
    ('version', pack.IntType(32)),
    ('previous_block', pack.PossiblyNoneType(0, pack.IntType(256))),
    ('merkle_root', pack.IntType(256)),
    ('timestamp', pack.IntType(32)),
    ('bits', FloatingIntegerType()),
    ('nonce', pack.IntType(32)),
])

block_type = pack.ComposedType([
    ('header', block_header_type),
    ('txs', pack.ListType(tx_type)),
])

block_type_old = pack.ComposedType([
    ('header', block_header_type),
    ('txs', pack.ListType(tx_type)),
])

# merged mining

aux_pow_type = pack.ComposedType([
    ('merkle_tx', merkle_tx_type),
    ('merkle_link', merkle_link_type),
    ('parent_block_header', block_header_type),
])

aux_pow_coinbase_type = pack.ComposedType([
    ('merkle_root', pack.IntType(256, 'big')),
    ('size', pack.IntType(32)),
    ('nonce', pack.IntType(32)),
])

def make_auxpow_tree(chain_ids):
    for size in (2**i for i in xrange(31)):
        if size < len(chain_ids):
            continue
        res = {}
        for chain_id in chain_ids:
            pos = (1103515245 * chain_id + 1103515245 * 12345 + 12345) % size
            if pos in res:
                break
            res[pos] = chain_id
        else:
            return res, size
    raise AssertionError()

# merkle trees

merkle_record_type = pack.ComposedType([
    ('left', pack.IntType(256)),
    ('right', pack.IntType(256)),
])

def merkle_hash(hashes):
    if not hashes:
        return 0
    hash_list = list(hashes)
    while len(hash_list) > 1:
        hash_list = [hash256(merkle_record_type.pack(dict(left=left, right=right)))
            for left, right in zip(hash_list[::2], hash_list[1::2] + [hash_list[::2][-1]])]
    return hash_list[0]

def calculate_merkle_link(hashes, index):
    # XXX optimize this
    
    hash_list = [(lambda _h=h: _h, i == index, []) for i, h in enumerate(hashes)]
    
    while len(hash_list) > 1:
        hash_list = [
            (
                lambda _left=left, _right=right: hash256(merkle_record_type.pack(dict(left=_left(), right=_right()))),
                left_f or right_f,
                (left_l if left_f else right_l) + [dict(side=1, hash=right) if left_f else dict(side=0, hash=left)],
            )
            for (left, left_f, left_l), (right, right_f, right_l) in
                zip(hash_list[::2], hash_list[1::2] + [hash_list[::2][-1]])
        ]
    
    res = [x['hash']() for x in hash_list[0][2]]
    
    assert hash_list[0][1]
    if p2pool.DEBUG:
        new_hashes = [random.randrange(2**256) if x is None else x
            for x in hashes]
        assert check_merkle_link(new_hashes[index], dict(branch=res, index=index)) == merkle_hash(new_hashes)
    assert index == sum(k*2**i for i, k in enumerate([1-x['side'] for x in hash_list[0][2]]))
    
    return dict(branch=res, index=index)

def check_merkle_link(tip_hash, link):
    if link['index'] >= 2**len(link['branch']):
        raise ValueError('index too large')
    return reduce(lambda c, (i, h): hash256(merkle_record_type.pack(
        dict(left=h, right=c) if (link['index'] >> i) & 1 else
        dict(left=c, right=h)
    )), enumerate(link['branch']), tip_hash)

# targets

def target_to_average_attempts(target):
    assert 0 <= target and isinstance(target, (int, long)), target
    if target >= 2**256: warnings.warn('target >= 2**256!')
    return 2**256//(target + 1)

def average_attempts_to_target(average_attempts):
    assert average_attempts > 0
    return min(int(2**256/average_attempts - 1 + 0.5), 2**256-1)

def target_to_difficulty(target):
    assert 0 <= target and isinstance(target, (int, long)), target
    if target >= 2**256: warnings.warn('target >= 2**256!')
    return (0xffff0000 * 2**(256-64) + 1)/(target + 1)

def difficulty_to_target(difficulty):
    assert difficulty >= 0
    if difficulty == 0: return 2**256-1
    return min(int((0xffff0000 * 2**(256-64) + 1)/difficulty - 1 + 0.5), 2**256-1)

# human addresses

base58_alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'

def base58_encode(bindata):
    bindata2 = bindata.lstrip(chr(0))
    return base58_alphabet[0]*(len(bindata) - len(bindata2)) + math.natural_to_string(math.string_to_natural(bindata2), base58_alphabet)

def base58_decode(b58data):
    b58data2 = b58data.lstrip(base58_alphabet[0])
    return chr(0)*(len(b58data) - len(b58data2)) + math.natural_to_string(math.string_to_natural(b58data2, base58_alphabet))

human_address_type = ChecksummedType(pack.ComposedType([
    ('version', pack.IntType(8)),
    ('pubkey_hash', pack.IntType(160)),
]))

def pubkey_hash_to_address(pubkey_hash, net):
    return base58_encode(human_address_type.pack(dict(version=net.ADDRESS_VERSION, pubkey_hash=pubkey_hash)))

def pubkey_hash_to_script_address(pubkey_hash, net):
    return base58_encode(human_address_type.pack(dict(version=net.SCRIPT_ADDRESS_VERSION, pubkey_hash=pubkey_hash)))

def pubkey_to_address(pubkey, net):
    return pubkey_hash_to_address(hash160(pubkey), net)

# SegWit / Taproot address functions
def pubkey_to_segwit_address(pubkey, net):
    """Convert pubkey to native SegWit v0 address (P2WPKH)"""
    if not hasattr(net, 'HRP'):
        raise ValueError('Network does not support SegWit addresses')
    pubkey_hash = hash160(pubkey)
    return segwit_addr.encode(net.HRP, 0, pack.IntType(160).pack(pubkey_hash))

def pubkey_to_taproot_address(pubkey, net):
    """Convert pubkey to Taproot address (P2TR / SegWit v1)
    
    For Taproot, we need a 32-byte x-only public key.
    If given a 33-byte compressed pubkey, we strip the first byte.
    """
    if not hasattr(net, 'HRP'):
        raise ValueError('Network does not support Taproot addresses')
    
    # Handle compressed pubkey (33 bytes) - strip the prefix byte to get x-only
    if len(pubkey) == 33 and (ord(pubkey[0]) == 0x02 or ord(pubkey[0]) == 0x03):
        x_only_pubkey = pubkey[1:]
    elif len(pubkey) == 32:
        # Already x-only
        x_only_pubkey = pubkey
    else:
        raise ValueError('Invalid pubkey length for Taproot: %d' % len(pubkey))
    
    return segwit_addr.encode(net.HRP, 1, x_only_pubkey)

def script_hash_to_segwit_address(script_hash, net):
    """Convert script hash to native SegWit v0 script address (P2WSH)"""
    if not hasattr(net, 'HRP'):
        raise ValueError('Network does not support SegWit addresses')
    return segwit_addr.encode(net.HRP, 0, script_hash)

def address_to_pubkey_hash(address, net):
    # Try base58 addresses first (legacy P2PKH / P2SH)
    try:
        x = human_address_type.unpack(base58_decode(address))
        if x['version'] != net.ADDRESS_VERSION and x['version'] != net.SCRIPT_ADDRESS_VERSION:
            raise ValueError('address not for this net!')
        return x['pubkey_hash']
    except:
        pass
    
    # Try bech32/bech32m addresses (SegWit / Taproot)
    if hasattr(net, 'HRP'):
        try:
            witver, witprog = segwit_addr.decode(net.HRP, address)
            if witver is not None and witprog is not None:
                # For P2WPKH (20 bytes) return as pubkey_hash
                if witver == 0 and len(witprog) == 20:
                    return pack.IntType(160).unpack(''.join(chr(b) for b in witprog))
                # For P2TR (32 bytes) or P2WSH (32 bytes), we can't return a simple pubkey_hash
                # This function is mainly used for legacy addresses
                raise ValueError('SegWit script or Taproot address - use address_to_script2 instead')
        except:
            pass
    
    raise ValueError('invalid address')

# transactions

def pubkey_to_script2(pubkey):
    assert len(pubkey) <= 75
    return (chr(len(pubkey)) + pubkey) + '\xac'

def pubkey_hash_to_script2(pubkey_hash):
    return '\x76\xa9' + ('\x14' + pack.IntType(160).pack(pubkey_hash)) + '\x88\xac'

def pubkey_hash_script_to_script2(pubkey_hash):
    return '\xa9' + ('\x14' + pack.IntType(160).pack(pubkey_hash)) + '\x87'

# Create Script from Human Address
def address_to_script2(address, net):
    # Try base58 addresses first (legacy P2PKH / P2SH)
    try:
        x = human_address_type.unpack(base58_decode(address))
        if x['version'] == net.ADDRESS_VERSION:
            return pubkey_hash_to_script2(x['pubkey_hash'])
        if x['version'] == net.SCRIPT_ADDRESS_VERSION:
            return pubkey_hash_script_to_script2(x['pubkey_hash'])
    except:
        pass
    
    # Try bech32/bech32m addresses (SegWit / Taproot)
    if hasattr(net, 'HRP'):
        try:
            witver, witprog = segwit_addr.decode(net.HRP, address)
            if witver is not None and witprog is not None:
                # Convert witprog list to bytes
                witprog_bytes = ''.join(chr(b) for b in witprog)
                
                # SegWit v0 (P2WPKH: 20 bytes, P2WSH: 32 bytes)
                if witver == 0:
                    if len(witprog_bytes) == 20:
                        # P2WPKH: OP_0 <20-byte-pubkey-hash>
                        return '\x00\x14' + witprog_bytes
                    elif len(witprog_bytes) == 32:
                        # P2WSH: OP_0 <32-byte-script-hash>
                        return '\x00\x20' + witprog_bytes
                
                # Taproot / SegWit v1 (P2TR: 32 bytes)
                elif witver == 1:
                    if len(witprog_bytes) == 32:
                        # P2TR: OP_1 <32-byte-x-only-pubkey>
                        return '\x51\x20' + witprog_bytes
                
                # Future SegWit versions (v2-v16)
                elif witver >= 2 and witver <= 16:
                    # OP_N <len> <data> where N = witver
                    op_n = chr(0x50 + witver)  # OP_1=0x51, OP_2=0x52, ..., OP_16=0x60
                    return op_n + chr(len(witprog_bytes)) + witprog_bytes
        except:
            pass
    
    raise ValueError('address not for this net!')

def script2_to_address(script2, net):
    # Try P2PK (Pay to Public Key)
    try:
        pubkey = script2[1:-1]
        script2_test = pubkey_to_script2(pubkey)
    except:
        pass
    else:
        if script2_test == script2:
            return pubkey_to_address(pubkey, net)
    
    # Try P2PKH (Pay to Public Key Hash)
    try:
        pubkey_hash = pack.IntType(160).unpack(script2[3:-2])
        script2_test2 = pubkey_hash_to_script2(pubkey_hash)
    except:
        pass
    else:
        if script2_test2 == script2:
            return pubkey_hash_to_address(pubkey_hash, net)
    
    # Try P2SH (Pay to Script Hash)
    try:
        pubkey_hash = pack.IntType(160).unpack(script2[2:-1])
        script2_test3 = pubkey_hash_script_to_script2(pubkey_hash)
    except:
        pass
    else:
        if script2_test3 == script2:
            return pubkey_hash_to_script_address(pubkey_hash, net)
    
    # Try SegWit / Taproot (if network supports it)
    if hasattr(net, 'HRP') and len(script2) >= 2:
        try:
            # Check if it's a witness program: OP_0/OP_1..OP_16 followed by data
            first_byte = ord(script2[0])
            
            # SegWit v0: OP_0 (0x00)
            if first_byte == 0x00:
                data_len = ord(script2[1])
                if len(script2) == 2 + data_len:
                    witprog = [ord(b) for b in script2[2:]]
                    addr = segwit_addr.encode(net.HRP, 0, witprog)
                    if addr:
                        return addr
            
            # Taproot v1: OP_1 (0x51)
            elif first_byte == 0x51:
                data_len = ord(script2[1])
                if len(script2) == 2 + data_len and data_len == 32:
                    witprog = [ord(b) for b in script2[2:]]
                    addr = segwit_addr.encode(net.HRP, 1, witprog)
                    if addr:
                        return addr
            
            # Future SegWit versions: OP_2..OP_16 (0x52..0x60)
            elif first_byte >= 0x52 and first_byte <= 0x60:
                witver = first_byte - 0x50
                data_len = ord(script2[1])
                if len(script2) == 2 + data_len:
                    witprog = [ord(b) for b in script2[2:]]
                    addr = segwit_addr.encode(net.HRP, witver, witprog)
                    if addr:
                        return addr
        except:
            pass
    
    return None


def script2_to_human(script2, net):
    try:
        pubkey = script2[1:-1]
        script2_test = pubkey_to_script2(pubkey)
    except:
        pass
    else:
        if script2_test == script2:
            return 'Pubkey. Address: %s' % (pubkey_to_address(pubkey, net),)
    
    try:
        pubkey_hash = pack.IntType(160).unpack(script2[3:-2])
        script2_test2 = pubkey_hash_to_script2(pubkey_hash)
    except:
        pass
    else:
        if script2_test2 == script2:
            return 'Address. Address: %s' % (pubkey_hash_to_address(pubkey_hash, net),)
    
    try:
        pubkey_hash = pack.IntType(160).unpack(script2[2:-1])
        script2_test3 = pubkey_hash_script_to_script2(pubkey_hash)
    except:
        pass
    else:
        if script2_test3 == script2:
            return 'Address. Address: %s' % (pubkey_hash_to_script_address(pubkey_hash, net),)
    
    return 'Unknown. Script: %s'  % (script2.encode('hex'),)
