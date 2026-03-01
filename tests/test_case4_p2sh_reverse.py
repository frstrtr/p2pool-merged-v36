#!/usr/bin/env python
"""
Test Case 4 P2SH reverse conversion: DOGE P2SH → LTC P2SH

Verifies that the reverse-conversion logic in work.py correctly handles
P2SH addresses from Dogecoin when deriving LTC addresses.

Tests:
1. DOGE P2PKH (D...) → LTC P2PKH (L...) - baseline
2. DOGE P2SH (9.../A...) → LTC P2SH (M...) - the key test
3. Round-trip: pubkey_hash is preserved across chains
4. Edge cases: leading-zero hashes, boundary version bytes
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from p2pool.bitcoin import data as bitcoin_data
from p2pool.bitcoin.networks import dogecoin as doge_net
from p2pool.bitcoin.networks import litecoin as ltc_net

def test_p2pkh_reverse():
    """DOGE P2PKH → LTC P2PKH: basic reverse conversion"""
    # Use a well-known DOGE P2PKH address format
    # Generate one from a known pubkey_hash
    known_hash = 0xabcdef1234567890abcdef1234567890abcdef12
    
    # Create DOGE P2PKH address
    doge_addr = bitcoin_data.pubkey_hash_to_address(
        known_hash, doge_net.ADDRESS_VERSION, -1, doge_net)
    print('DOGE P2PKH address: %s' % doge_addr)
    assert doge_addr.startswith('D'), 'DOGE P2PKH should start with D, got: %s' % doge_addr
    
    # Parse it back
    parsed_hash, parsed_ver, parsed_wit = bitcoin_data.address_to_pubkey_hash(doge_addr, doge_net)
    assert parsed_hash == known_hash, 'Hash mismatch: %x != %x' % (parsed_hash, known_hash)
    assert parsed_ver == doge_net.ADDRESS_VERSION, 'Version mismatch: %d != %d' % (parsed_ver, doge_net.ADDRESS_VERSION)
    assert parsed_wit == -1, 'Witness version should be -1 for legacy'
    
    # Reverse convert to LTC P2PKH
    ltc_addr = bitcoin_data.pubkey_hash_to_address(
        parsed_hash, ltc_net.ADDRESS_VERSION, -1, ltc_net)
    print('LTC P2PKH address:  %s' % ltc_addr)
    assert ltc_addr.startswith('L'), 'LTC P2PKH should start with L, got: %s' % ltc_addr
    
    # Verify round-trip
    ltc_hash, ltc_ver, ltc_wit = bitcoin_data.address_to_pubkey_hash(ltc_addr, ltc_net)
    assert ltc_hash == known_hash, 'Round-trip hash mismatch: %x != %x' % (ltc_hash, known_hash)
    assert ltc_ver == ltc_net.ADDRESS_VERSION, 'LTC version mismatch'
    
    print('  PASS: P2PKH pubkey_hash preserved: 0x%x' % known_hash)
    return True

def test_p2sh_reverse():
    """DOGE P2SH → LTC P2SH: the critical Case 4 test"""
    known_hash = 0x1234567890abcdef1234567890abcdef12345678

    # Create DOGE P2SH address (version 22)
    doge_p2sh = bitcoin_data.pubkey_hash_to_address(
        known_hash, doge_net.ADDRESS_P2SH_VERSION, -1, doge_net)
    print('DOGE P2SH address:  %s' % doge_p2sh)
    assert doge_p2sh[0] in ('9', 'A'), 'DOGE P2SH should start with 9 or A, got: %s' % doge_p2sh
    
    # Parse it
    parsed_hash, parsed_ver, parsed_wit = bitcoin_data.address_to_pubkey_hash(doge_p2sh, doge_net)
    assert parsed_hash == known_hash, 'Hash mismatch: %x != %x' % (parsed_hash, known_hash)
    assert parsed_ver == doge_net.ADDRESS_P2SH_VERSION, \
        'Should be P2SH version %d, got %d' % (doge_net.ADDRESS_P2SH_VERSION, parsed_ver)
    
    # === KEY CHECK: the code path from work.py ===
    # This mirrors lines 1583-1588 of work.py:
    #   if doge_version == doge_net.ADDRESS_P2SH_VERSION:
    #       pubkey_hash = doge_pubkey_hash
    #       pubkey_type = PUBKEY_TYPE_P2SH
    #       ltc_addr = pubkey_hash_to_address(doge_pubkey_hash, parent_net.ADDRESS_P2SH_VERSION, -1, parent_net)
    
    assert parsed_ver == doge_net.ADDRESS_P2SH_VERSION, \
        'Version check would FAIL in work.py! %d != %d' % (parsed_ver, doge_net.ADDRESS_P2SH_VERSION)
    
    ltc_p2sh = bitcoin_data.pubkey_hash_to_address(
        parsed_hash, ltc_net.ADDRESS_P2SH_VERSION, -1, ltc_net)
    print('LTC P2SH address:   %s' % ltc_p2sh)
    assert ltc_p2sh[0] in ('M', '3'), 'LTC P2SH should start with M or 3, got: %s' % ltc_p2sh
    
    # Verify round-trip
    ltc_hash, ltc_ver, ltc_wit = bitcoin_data.address_to_pubkey_hash(ltc_p2sh, ltc_net)
    assert ltc_hash == known_hash, 'Round-trip hash mismatch: %x != %x' % (ltc_hash, known_hash)
    assert ltc_ver == ltc_net.ADDRESS_P2SH_VERSION, \
        'LTC version should be P2SH (%d), got %d' % (ltc_net.ADDRESS_P2SH_VERSION, ltc_ver)
    
    print('  PASS: P2SH pubkey_hash preserved: 0x%x' % known_hash)
    print('  PASS: DOGE P2SH ver=%d → LTC P2SH ver=%d' % (
        doge_net.ADDRESS_P2SH_VERSION, ltc_net.ADDRESS_P2SH_VERSION))
    return True

def test_p2sh_multiple_addresses():
    """Test several different P2SH hashes to catch edge cases"""
    test_hashes = [
        0x0000000000000000000000000000000000000001,  # near-zero (leading zeros)
        0xffffffffffffffffffffffffffffffffffffffff,  # max 160-bit
        0x89abcdef0123456789abcdef0123456789abcdef,  # mid-range
        0x0000000000000000000000000000000000000000,  # actual zero (edge case!)
        0x00000000000000000000000000000000000000ff,  # small value with leading zeros
    ]
    
    for i, h in enumerate(test_hashes):
        doge_p2sh = bitcoin_data.pubkey_hash_to_address(
            h, doge_net.ADDRESS_P2SH_VERSION, -1, doge_net)
        parsed_h, parsed_v, _ = bitcoin_data.address_to_pubkey_hash(doge_p2sh, doge_net)
        
        # Reverse convert
        ltc_p2sh = bitcoin_data.pubkey_hash_to_address(
            parsed_h, ltc_net.ADDRESS_P2SH_VERSION, -1, ltc_net)
        ltc_h, ltc_v, _ = bitcoin_data.address_to_pubkey_hash(ltc_p2sh, ltc_net)
        
        assert ltc_h == h, 'Hash %d failed: %x != %x' % (i, ltc_h, h)
        assert ltc_v == ltc_net.ADDRESS_P2SH_VERSION, 'Version %d wrong' % i
        print('  hash[%d] 0x%040x → DOGE %s → LTC %s  PASS' % (i, h, doge_p2sh, ltc_p2sh))
    
    return True

def test_cross_parse_fails():
    """Verify DOGE addresses can't parse as LTC and vice versa"""
    known_hash = 0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
    
    doge_p2sh = bitcoin_data.pubkey_hash_to_address(
        known_hash, doge_net.ADDRESS_P2SH_VERSION, -1, doge_net)
    ltc_p2sh = bitcoin_data.pubkey_hash_to_address(
        known_hash, ltc_net.ADDRESS_P2SH_VERSION, -1, ltc_net)
    
    # DOGE P2SH should NOT parse as LTC
    try:
        bitcoin_data.address_to_pubkey_hash(doge_p2sh, ltc_net)
        print('  FAIL: DOGE P2SH %s parsed as LTC (should have thrown!)' % doge_p2sh)
        return False
    except (ValueError, Exception):
        print('  PASS: DOGE P2SH %s correctly rejected by LTC parser' % doge_p2sh)
    
    # LTC P2SH should NOT parse as DOGE
    try:
        bitcoin_data.address_to_pubkey_hash(ltc_p2sh, doge_net)
        print('  FAIL: LTC P2SH %s parsed as DOGE (should have thrown!)' % ltc_p2sh)
        return False
    except (ValueError, Exception):
        print('  PASS: LTC P2SH %s correctly rejected by DOGE parser' % ltc_p2sh)
    
    return True

def test_version_byte_check():
    """Verify the version-byte comparison that work.py uses"""
    print('DOGE ADDRESS_VERSION (P2PKH):     %d (0x%02x)' % (doge_net.ADDRESS_VERSION, doge_net.ADDRESS_VERSION))
    print('DOGE ADDRESS_P2SH_VERSION:        %d (0x%02x)' % (doge_net.ADDRESS_P2SH_VERSION, doge_net.ADDRESS_P2SH_VERSION))
    print('LTC  ADDRESS_VERSION (P2PKH):     %d (0x%02x)' % (ltc_net.ADDRESS_VERSION, ltc_net.ADDRESS_VERSION))
    print('LTC  ADDRESS_P2SH_VERSION:        %d (0x%02x)' % (ltc_net.ADDRESS_P2SH_VERSION, ltc_net.ADDRESS_P2SH_VERSION))
    
    # The critical check from work.py line 1583:
    #   if doge_version == doge_net.ADDRESS_P2SH_VERSION:
    # We need to make sure these version bytes are DISTINCT
    assert doge_net.ADDRESS_VERSION != doge_net.ADDRESS_P2SH_VERSION, \
        'DOGE P2PKH and P2SH versions must differ!'
    assert ltc_net.ADDRESS_VERSION != ltc_net.ADDRESS_P2SH_VERSION, \
        'LTC P2PKH and P2SH versions must differ!'
    
    # Also check no cross-collision between DOGE P2SH and DOGE P2PKH versions
    # (which would make the if/else in work.py ambiguous)
    print('  PASS: All version bytes are distinct')
    return True

def test_swapped_comma_detection_predicate():
    """Sanity-check swapped order detection: DOGE first, LTC second."""
    known_hash = 0x314159265358979323846264338327950288419716 % (1 << 160)

    doge_p2pkh = bitcoin_data.pubkey_hash_to_address(
        known_hash, doge_net.ADDRESS_VERSION, -1, doge_net)
    ltc_p2pkh = bitcoin_data.pubkey_hash_to_address(
        known_hash, ltc_net.ADDRESS_VERSION, -1, ltc_net)

    # Simulate stratum username split parts: first (user) + second (merged_addr)
    first = doge_p2pkh   # wrong position (should be second)
    second = ltc_p2pkh   # wrong position (should be first)

    # Predicate used by work.py swapped-order guardrail:
    # 1) first parses as DOGE
    # 2) second parses as LTC parent
    doge_hash, doge_ver, _ = bitcoin_data.address_to_pubkey_hash(first, doge_net)
    ltc_hash, ltc_ver, _ = bitcoin_data.address_to_pubkey_hash(second, ltc_net)

    assert doge_hash == known_hash and ltc_hash == known_hash, 'Expected same 20-byte hash in both addresses'
    assert doge_ver == doge_net.ADDRESS_VERSION, 'First address should classify as DOGE P2PKH'
    assert ltc_ver == ltc_net.ADDRESS_VERSION, 'Second address should classify as LTC P2PKH'

    print('  PASS: swapped-order predicate matches DOGE-first/LTC-second input')
    print('  sample swapped input: %s,%s' % (first, second))
    return True


if __name__ == '__main__':
    print('=' * 60)
    print('Case 4 P2SH Reverse Conversion Tests')
    print('=' * 60)
    
    tests = [
        ('Version byte sanity',            test_version_byte_check),
        ('Swapped comma detection',        test_swapped_comma_detection_predicate),
        ('P2PKH reverse (baseline)',        test_p2pkh_reverse),
        ('P2SH reverse (critical)',         test_p2sh_reverse),
        ('P2SH multiple hashes',           test_p2sh_multiple_addresses),
        ('Cross-chain parse rejection',     test_cross_parse_fails),
    ]
    
    passed = 0
    failed = 0
    for name, fn in tests:
        print('\n--- %s ---' % name)
        try:
            if fn():
                passed += 1
            else:
                failed += 1
                print('  *** FAILED ***')
        except Exception as e:
            failed += 1
            print('  *** EXCEPTION: %s ***' % e)
            import traceback
            traceback.print_exc()
    
    print('\n' + '=' * 60)
    print('Results: %d passed, %d failed' % (passed, failed))
    print('=' * 60)
    sys.exit(1 if failed else 0)
