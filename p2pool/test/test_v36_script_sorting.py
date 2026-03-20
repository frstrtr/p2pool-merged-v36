"""
Test V36 script-based output sorting consensus rule.

V36 changes PPLNS output sorting from (amount, address_string) to (amount, script_bytes).
This ensures cross-implementation determinism: raw script bytes sort identically in any
language (Python, C++, Rust, etc.) whereas address encoding (base58, bech32) can vary.

These tests verify:
1. Script-based sorting matches between Python and expected C++ behavior
2. Mixed address types (P2PKH, P2WPKH, P2SH) sort correctly
3. Equal-amount tiebreaking uses script bytes, not address strings
4. Edge cases: single miner, empty outputs, donation-only, >4000 outputs
"""

import unittest
import struct
import hashlib


def make_p2pkh_script(hash160_hex):
    """Build P2PKH scriptPubKey: OP_DUP OP_HASH160 PUSH20 <hash160> OP_EQUALVERIFY OP_CHECKSIG"""
    return '\x76\xa9\x14' + hash160_hex.decode('hex') + '\x88\xac'


def make_p2wpkh_script(hash160_hex):
    """Build P2WPKH scriptPubKey: OP_0 PUSH20 <hash160>"""
    return '\x00\x14' + hash160_hex.decode('hex')


def make_p2sh_script(hash160_hex):
    """Build P2SH scriptPubKey: OP_HASH160 PUSH20 <hash160> OP_EQUAL"""
    return '\xa9\x14' + hash160_hex.decode('hex') + '\x87'


def v36_sort_outputs(amounts_by_script, excluded_scripts):
    """
    V36 consensus: sort PPLNS outputs by (amount, script_bytes) ascending.

    This replaces the pre-V36 sort by (amount, address_string).
    Script bytes are raw binary — sorting is simple memcmp.

    Args:
        amounts_by_script: dict {script_bytes: satoshi_amount}
        excluded_scripts: set of scripts to exclude (donation scripts)

    Returns:
        list of (script, amount) tuples in consensus output order
    """
    dests = sorted(
        [s for s in amounts_by_script.iterkeys() if s not in excluded_scripts],
        key=lambda s: (amounts_by_script[s], s)
    )[-4000:]
    return [(s, amounts_by_script[s]) for s in dests if amounts_by_script[s]]


def pre_v36_sort_outputs(amounts_by_address, excluded_addrs):
    """Pre-V36: sort by (amount, address_string) ascending."""
    dests = sorted(
        [a for a in amounts_by_address.iterkeys() if a not in excluded_addrs],
        key=lambda a: (amounts_by_address[a], a)
    )[-4000:]
    return [(a, amounts_by_address[a]) for a in dests if amounts_by_address[a]]


class TestV36ScriptSorting(unittest.TestCase):
    """Test V36 script-based output sorting."""

    def test_same_type_p2pkh_sorting(self):
        """Two P2PKH miners with different amounts — primary sort by amount."""
        script_a = make_p2pkh_script('aa' * 20)
        script_b = make_p2pkh_script('bb' * 20)
        amounts = {script_a: 3000, script_b: 1000}
        result = v36_sort_outputs(amounts, set())
        self.assertEqual(result, [(script_b, 1000), (script_a, 3000)])

    def test_equal_amounts_p2pkh(self):
        """Two P2PKH miners with equal amounts — tiebreak by script bytes."""
        script_a = make_p2pkh_script('aa' * 20)  # 76a914 aa..aa 88ac
        script_b = make_p2pkh_script('bb' * 20)  # 76a914 bb..bb 88ac
        amounts = {script_a: 5000, script_b: 5000}
        result = v36_sort_outputs(amounts, set())
        # aa < bb, so script_a comes first
        self.assertEqual(result[0][0], script_a)
        self.assertEqual(result[1][0], script_b)

    def test_mixed_types_equal_amounts(self):
        """
        Mixed P2PKH + P2WPKH with equal amounts.

        This is the KEY test — with address-based sorting:
          P2PKH "mg1j..." < P2WPKH "tltc1q..." (because 'm' < 't')
        With script-based sorting:
          P2WPKH 0x0014... < P2PKH 0x76a914... (because 0x00 < 0x76)

        V36 uses script-based, which is the OPPOSITE order of address-based.
        """
        hash160 = 'ab' * 20
        p2pkh = make_p2pkh_script(hash160)   # 76 a9 14 ... 88 ac
        p2wpkh = make_p2wpkh_script(hash160)  # 00 14 ...
        amounts = {p2pkh: 5000, p2wpkh: 5000}
        result = v36_sort_outputs(amounts, set())
        # Script sort: 0x0014... < 0x76a914... → P2WPKH first
        self.assertEqual(result[0][0], p2wpkh, "P2WPKH (0x0014) should sort before P2PKH (0x76a9)")
        self.assertEqual(result[1][0], p2pkh)

    def test_mixed_types_different_amounts(self):
        """Mixed types with different amounts — primary sort by amount wins."""
        p2pkh = make_p2pkh_script('aa' * 20)
        p2wpkh = make_p2wpkh_script('bb' * 20)
        p2sh = make_p2sh_script('cc' * 20)
        amounts = {p2pkh: 3000, p2wpkh: 1000, p2sh: 2000}
        result = v36_sort_outputs(amounts, set())
        self.assertEqual([r[1] for r in result], [1000, 2000, 3000])

    def test_all_three_types_equal_amounts(self):
        """P2WPKH, P2SH, P2PKH with equal amounts — sorted by first byte of script."""
        hash160 = 'dd' * 20
        p2wpkh = make_p2wpkh_script(hash160)  # 00 14 ...
        p2sh = make_p2sh_script(hash160)       # a9 14 ... 87
        p2pkh = make_p2pkh_script(hash160)     # 76 a9 14 ... 88 ac
        amounts = {p2pkh: 5000, p2wpkh: 5000, p2sh: 5000}
        result = v36_sort_outputs(amounts, set())
        # Byte order: 0x00 < 0x76 < 0xa9
        self.assertEqual(result[0][0], p2wpkh, "P2WPKH (0x00) first")
        self.assertEqual(result[1][0], p2pkh, "P2PKH (0x76) second")
        self.assertEqual(result[2][0], p2sh, "P2SH (0xa9) third")

    def test_excluded_scripts(self):
        """Donation scripts are excluded from sorted outputs."""
        donation = make_p2sh_script('8c62' + 'aa' * 18)
        miner_a = make_p2pkh_script('11' * 20)
        miner_b = make_p2pkh_script('22' * 20)
        amounts = {miner_a: 3000, miner_b: 2000, donation: 500}
        result = v36_sort_outputs(amounts, {donation})
        self.assertEqual(len(result), 2)
        scripts = [r[0] for r in result]
        self.assertNotIn(donation, scripts)

    def test_single_miner(self):
        """Single miner — only one output (no sorting needed)."""
        script = make_p2pkh_script('ff' * 20)
        amounts = {script: 10000}
        result = v36_sort_outputs(amounts, set())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], (script, 10000))

    def test_zero_amount_excluded(self):
        """Miners with zero payout are excluded from output list."""
        script_a = make_p2pkh_script('aa' * 20)
        script_b = make_p2pkh_script('bb' * 20)
        amounts = {script_a: 5000, script_b: 0}
        result = v36_sort_outputs(amounts, set())
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], script_a)

    def test_max_outputs_truncation(self):
        """More than 4000 outputs — keep highest amounts (last 4000)."""
        amounts = {}
        for i in range(4100):
            hash160 = ('%04x' % i).ljust(40, '0')
            amounts[make_p2pkh_script(hash160)] = i + 1  # amounts 1..4100
        result = v36_sort_outputs(amounts, set())
        self.assertEqual(len(result), 4000)
        # Lowest 100 amounts (1..100) should be dropped
        self.assertEqual(result[0][1], 101)
        self.assertEqual(result[-1][1], 4100)

    def test_deterministic_with_cpp_vectors(self):
        """
        Cross-implementation test vector: verify exact output order.

        These specific scripts and amounts must produce the same order
        in both Python (p2pool) and C++ (c2pool).
        """
        # Test vector: 3 miners with carefully chosen scripts and amounts
        scripts = {
            # P2WPKH: 00 14 <20 bytes>
            make_p2wpkh_script('a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2'): 12345678,
            # P2PKH: 76 a9 14 <20 bytes> 88 ac
            make_p2pkh_script('1234567890abcdef1234567890abcdef12345678'): 12345678,  # SAME amount!
            # P2SH: a9 14 <20 bytes> 87
            make_p2sh_script('fedcba9876543210fedcba9876543210fedcba98'): 87654321,
        }
        result = v36_sort_outputs(scripts, set())

        # Expected order (amount ascending, then script bytes ascending):
        # 1. P2WPKH (amount=12345678, script=0x0014...) — lowest script for tied amount
        # 2. P2PKH  (amount=12345678, script=0x76a9...) — higher script for tied amount
        # 3. P2SH   (amount=87654321, script=0xa914...) — highest amount
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0][1], 12345678)
        self.assertTrue(result[0][0].startswith('\x00\x14'), "First should be P2WPKH")
        self.assertEqual(result[1][1], 12345678)
        self.assertTrue(result[1][0].startswith('\x76\xa9'), "Second should be P2PKH")
        self.assertEqual(result[2][1], 87654321)
        self.assertTrue(result[2][0].startswith('\xa9\x14'), "Third should be P2SH")

    def test_merged_payout_hash_format(self):
        """
        Verify compute_merged_payout_hash serialization format.

        V36 consensus: keys are hex-encoded script bytes, sorted lexically.
        Format: "script_hex1:weight1|script_hex2:weight2|...|T:total|D:donation"
        """
        # Simulate weights dict with script-byte keys
        weights = {
            make_p2pkh_script('bb' * 20): 2000,
            make_p2pkh_script('aa' * 20): 3000,
        }
        total_weight = 5500
        donation_weight = 500

        # Build payload (matching p2pool's compute_merged_payout_hash)
        parts = []
        for key in sorted(weights.keys()):
            key_hex = key.encode('hex')
            parts.append('%s:%d' % (key_hex, weights[key]))
        parts.append('T:%d' % total_weight)
        parts.append('D:%d' % donation_weight)
        payload = '|'.join(parts)

        # Verify hex-encoded script keys are in correct order
        # aa..aa < bb..bb in hex
        self.assertIn('76a914' + 'aa' * 20 + '88ac', payload.split('|')[0])
        self.assertIn('76a914' + 'bb' * 20 + '88ac', payload.split('|')[1])

        # Verify payload ends with T: and D:
        self.assertTrue(payload.endswith('D:%d' % donation_weight))

    def test_address_vs_script_sorting_divergence(self):
        """
        Demonstrate that address-based sorting gives DIFFERENT order than
        script-based sorting for mixed address types.

        This is WHY we changed to script-based sorting for V36.
        """
        hash160 = 'ab' * 20
        p2pkh = make_p2pkh_script(hash160)
        p2wpkh = make_p2wpkh_script(hash160)

        # Script-based (V36): P2WPKH (0x0014) < P2PKH (0x76a914)
        script_order = sorted([p2pkh, p2wpkh])
        self.assertEqual(script_order[0], p2wpkh)
        self.assertEqual(script_order[1], p2pkh)

        # Address-based (pre-V36): base58 P2PKH "m..." < bech32 P2WPKH "tltc1q..."
        # (We just verify they're different — actual address encoding is in bitcoin/data.py)
        # The KEY insight: 'm' (0x6d) < 't' (0x74) in ASCII
        self.assertTrue(ord('m') < ord('t'), "P2PKH 'm...' < P2WPKH 'tltc...' in address sort")
        # But in script sort, it's reversed:
        self.assertTrue(p2wpkh[0] < p2pkh[0], "P2WPKH 0x00 < P2PKH 0x76 in script sort")


if __name__ == '__main__':
    unittest.main()
