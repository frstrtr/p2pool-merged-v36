#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
wif_convert.py — standalone WIF private-key converter
      LTC <-> DOGE <-> DGB <-> BTC   (mainnet + testnet)

Zero external dependencies — pure Python 2.7 / PyPy 2.7 / Python 3.x stdlib.

Usage
-----
  python wif_convert.py <WIF>                 # decode + show all coins
  python wif_convert.py <WIF> -t ltc doge     # show only LTC and DOGE
  python wif_convert.py --hex <32-byte-hex>   # import raw private key (hex)
  python wif_convert.py --gen ltc             # generate a fresh key pair
  python wif_convert.py --help

Coins supported
---------------
  btc    Bitcoin          mainnet   WIF 0x80
  ltc    Litecoin         mainnet   WIF 0xB0
  doge   Dogecoin         mainnet   WIF 0x9E
  dgb    DigiByte         mainnet   WIF 0x80  (same byte as BTC — both shown)
  tbtc   Bitcoin          testnet   WIF 0xEF
  tltc   Litecoin         testnet   WIF 0xEF  (same byte as tBTC — both shown)
  tdoge  Dogecoin         testnet   WIF 0xF1
  tdgb   DigiByte         testnet   WIF 0xFE

Notes
-----
* P2PKH (legacy) addresses are derived on the fly via a pure-Python secp256k1
  scalar multiply — no extra library required.
* DGB and BTC share WIF version byte 0x80; their WIF strings are identical.
  Import the same WIF into both wallets — they will work fine.
* Bech32/segwit addresses (ltc1q…, dgb1q…) are NOT shown here;
  most wallets auto-derive them from the same private key.
* Never copy/paste private keys over unencrypted channels.
"""

from __future__ import print_function

import argparse
import hashlib
import os
import sys

# ---------------------------------------------------------------------------
# Python 2 / 3 shims
# ---------------------------------------------------------------------------
PY3 = sys.version_info[0] >= 3

if PY3:
    def _int_to_bytes(n, length):
        return n.to_bytes(length, 'big')

    def _bytes_to_int(b):
        return int.from_bytes(b, 'big')

    def _byte_at(b, i):
        return b[i]

    def _hex(b):
        return b.hex()
else:
    def _int_to_bytes(n, length):
        h = '%0*x' % (length * 2, n)
        return h.decode('hex')

    def _bytes_to_int(b):
        return int(b.encode('hex'), 16)

    def _byte_at(b, i):
        return ord(b[i])

    def _hex(b):
        return b.encode('hex')

# ---------------------------------------------------------------------------
# Base58Check
# ---------------------------------------------------------------------------
_B58ALPHA = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'


def _b58enc(data):
    n = _bytes_to_int(data)
    res = ''
    while n:
        n, r = divmod(n, 58)
        res = _B58ALPHA[r] + res
    for byte in data:
        if (byte if PY3 else ord(byte)) != 0:
            break
        res = '1' + res
    return res


def _b58dec(s):
    n = 0
    for c in s:
        if c not in _B58ALPHA:
            raise ValueError('Invalid base58 character: %r' % c)
        n = n * 58 + _B58ALPHA.index(c)
    # count only leading '1' chars (each → leading zero byte)
    pad = 0
    for c in s:
        if c != '1':
            break
        pad += 1
    raw = _int_to_bytes(n, max(1, (n.bit_length() + 7) // 8)) if n else b''
    return b'\x00' * pad + raw


def _sha256d(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def b58check_encode(version_byte, payload):
    """→ base58check string from (version_byte int, payload bytes)"""
    if PY3:
        raw = bytes([version_byte]) + payload
    else:
        raw = chr(version_byte) + payload
    checksum = _sha256d(raw)[:4]
    return _b58enc(raw + checksum)


def b58check_decode(s):
    """→ (version_byte int, payload bytes)  raises ValueError on bad input"""
    raw = _b58dec(s)
    if len(raw) < 5:
        raise ValueError('Too short')
    body, chk = raw[:-4], raw[-4:]
    if _sha256d(body)[:4] != chk:
        raise ValueError('Bad checksum')
    return _byte_at(body, 0), body[1:]


# ---------------------------------------------------------------------------
# Coin registry
# ---------------------------------------------------------------------------
# Columns: (id, wif_version, addr_version, description)
_COINS = [
    ('btc',   0x80, 0x00, 'Bitcoin (mainnet)'),
    ('ltc',   0xB0, 0x30, 'Litecoin (mainnet)'),
    ('doge',  0x9E, 0x1E, 'Dogecoin (mainnet)'),
    ('dgb',   0x80, 0x1E, 'DigiByte (mainnet)'),   # 0x80 = same as BTC
    ('tbtc',  0xEF, 0x6F, 'Bitcoin (testnet)'),
    ('tltc',  0xEF, 0x6F, 'Litecoin (testnet)'),   # 0xEF = same as tBTC
    ('tdoge', 0xF1, 0x71, 'Dogecoin (testnet)'),
    ('tdgb',  0xFE, 0x7E, 'DigiByte (testnet)'),
]

_COIN_BY_ID   = {c[0]: c for c in _COINS}
_WIF_TO_COINS = {}   # wif_version → [coin_id, ...]
for _c in _COINS:
    _WIF_TO_COINS.setdefault(_c[1], []).append(_c[0])


def _coin_ids():
    return [c[0] for c in _COINS]


def _wif_ver(cid):
    return _COIN_BY_ID[cid][1]


def _addr_ver(cid):
    return _COIN_BY_ID[cid][2]


def _coin_label(cid):
    return _COIN_BY_ID[cid][3]


# ---------------------------------------------------------------------------
# secp256k1 — pure Python, double-and-add scalar multiply
# ---------------------------------------------------------------------------
_P  = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N  = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_Gx = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_Gy = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_G  = (_Gx, _Gy)


def _modinv(a):
    return pow(a % _P, _P - 2, _P)


def _point_add(P, Q):
    if P is None:
        return Q
    if Q is None:
        return P
    if P[0] == Q[0]:
        if P[1] != Q[1] or P[1] == 0:
            return None
        # point doubling
        lam = (3 * P[0] * P[0] * _modinv(2 * P[1])) % _P
    else:
        lam = ((Q[1] - P[1]) * _modinv(Q[0] - P[0])) % _P
    rx = (lam * lam - P[0] - Q[0]) % _P
    ry = (lam * (P[0] - rx) - P[1]) % _P
    return (rx, ry)


def _point_mul(k, P):
    R = None
    while k:
        if k & 1:
            R = _point_add(R, P)
        P = _point_add(P, P)
        k >>= 1
    return R


def _privkey_to_pubkey(privkey_bytes, compressed=True):
    k = _bytes_to_int(privkey_bytes)
    if not (1 <= k < _N):
        raise ValueError('Private key value out of valid range')
    pt = _point_mul(k, _G)
    if pt is None:
        raise ValueError('Private key maps to point at infinity')
    if compressed:
        prefix = b'\x02' if pt[1] % 2 == 0 else b'\x03'
        return prefix + _int_to_bytes(pt[0], 32)
    else:
        return b'\x04' + _int_to_bytes(pt[0], 32) + _int_to_bytes(pt[1], 32)


def _pubkey_to_address(pubkey_bytes, addr_version):
    sha  = hashlib.sha256(pubkey_bytes).digest()
    h160 = hashlib.new('ripemd160', sha).digest()
    return b58check_encode(addr_version, h160)


# ---------------------------------------------------------------------------
# WIF encode / decode
# ---------------------------------------------------------------------------

def wif_encode(privkey_bytes, coin_id, compressed=True):
    """Encode 32-byte private key as WIF for the given coin."""
    payload = privkey_bytes + (b'\x01' if compressed else b'')
    return b58check_encode(_wif_ver(coin_id), payload)


def wif_decode(wif_str):
    """Decode WIF string → (privkey_bytes_32, compressed_bool, [coin_ids]).
    Raises ValueError on bad WIF."""
    ver, payload = b58check_decode(wif_str)
    if ver not in _WIF_TO_COINS:
        raise ValueError('Unknown WIF version byte 0x%02x' % ver)
    coins = _WIF_TO_COINS[ver]
    length = len(payload)
    if length == 33 and _byte_at(payload, 32) == 0x01:
        return payload[:32], True, coins
    elif length == 32:
        return payload, False, coins
    else:
        raise ValueError(
            'Unexpected payload length %d (expected 32 or 33)' % length)


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

def generate_privkey():
    """Return a cryptographically random valid secp256k1 private key (32 bytes)."""
    while True:
        raw = os.urandom(32)
        k = _bytes_to_int(raw)
        if 1 <= k < _N:
            return raw


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _hr(n):
    """Human-readable hex with spaces every 8 chars."""
    h = _hex(n) if isinstance(n, bytes) else '%064x' % n
    return ' '.join(h[i:i+8] for i in range(0, len(h), 8))


def show_conversion(privkey_bytes, compressed, source_label, targets=None):
    """Print a formatted table: WIF + P2PKH address for each target coin."""
    show = targets if targets else [c[0] for c in _COINS]

    print()
    print('  Private key : %s' % _hr(privkey_bytes))
    print('  Compression : %s' % ('yes (compressed pubkey)' if compressed
                                  else 'no  (uncompressed pubkey — legacy)'))
    if source_label:
        print('  Source      : %s' % source_label)
    print()

    # Derive pubkey once (same key for all coins)
    try:
        pubkey = _privkey_to_pubkey(privkey_bytes, compressed)
    except ValueError as e:
        print('  ERROR: %s' % e)
        return

    header = '  %-7s  %-52s  %s' % ('COIN', 'WIF', 'P2PKH address')
    print(header)
    print('  ' + '-' * (len(header) - 2))

    for cid in show:
        if cid not in _COIN_BY_ID:
            print('  Unknown coin id: %s' % cid)
            continue
        wif  = wif_encode(privkey_bytes, cid, compressed)
        addr = _pubkey_to_address(pubkey, _addr_ver(cid))
        print('  %-7s  %-52s  %s' % (cid.upper(), wif, addr))

    print()
    if not targets:
        print('  NOTE: DGB and BTC share WIF prefix 0x80 — their WIF strings are identical.')
        print('        tBTC and tLTC also share WIF prefix 0xEF.')
        print('        Import the same WIF into either wallet; it will work correctly.')
        print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog='wif_convert.py',
        description='Convert WIF private keys between LTC / DOGE / DGB / BTC',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''\
examples:
  %(prog)s T52...abc                      # decode LTC WIF, show all coins
  %(prog)s T52...abc -t ltc doge          # show only LTC and DOGE
  %(prog)s --hex deadbeef...cafe          # import 32-byte raw hex key
  %(prog)s --gen ltc                      # generate fresh LTC key pair
  %(prog)s --gen doge -t doge dgb         # generate DOGE key, show DOGE+DGB

valid coin ids:  btc  ltc  doge  dgb  tbtc  tltc  tdoge  tdgb
''')

    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument(
        'wif', nargs='?', metavar='WIF',
        help='WIF-encoded private key to decode/convert')
    grp.add_argument(
        '--hex', metavar='HEX', dest='raw_hex',
        help='32-byte private key as 64 hex characters')
    grp.add_argument(
        '--gen', metavar='COIN', choices=_coin_ids(),
        help='Generate a fresh private key (shown for all coins unless -t)')

    parser.add_argument(
        '-t', '--to', metavar='COIN', nargs='+', choices=_coin_ids(),
        dest='targets',
        help='Show output only for these coin(s) (default: all)')
    parser.add_argument(
        '-u', '--uncompressed', action='store_true',
        help='Use uncompressed pubkey format (legacy; not recommended)')

    args = parser.parse_args()
    compressed = not args.uncompressed

    if args.gen:
        privkey = generate_privkey()
        show_conversion(
            privkey, compressed,
            'Generated fresh key (source coin for labelling: %s)' % _coin_label(args.gen),
            args.targets)

    elif args.raw_hex:
        raw = args.raw_hex.strip().lower().replace('0x', '').replace(' ', '')
        if len(raw) != 64:
            parser.error('--hex requires exactly 64 hex characters (32 bytes)')
        try:
            privkey = bytes.fromhex(raw) if PY3 else raw.decode('hex')
        except (ValueError, TypeError) as e:
            parser.error('Invalid hex: %s' % e)
        show_conversion(privkey, compressed, 'Raw hex import', args.targets)

    else:
        wif_str = args.wif
        try:
            privkey, comp_from_wif, detected = wif_decode(wif_str)
        except (ValueError, IndexError) as e:
            parser.error('Invalid WIF key: %s' % e)

        if args.uncompressed:
            comp_from_wif = False

        label = 'WIF decoded  (version byte matches: %s)' % ', '.join(
            _coin_label(c) for c in detected)
        show_conversion(privkey, comp_from_wif, label, args.targets)


if __name__ == '__main__':
    main()
