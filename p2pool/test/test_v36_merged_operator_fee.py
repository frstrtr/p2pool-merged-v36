# -*- coding: utf-8 -*-
# Python 2.7 red->green KAT for the CORRECTED merged/DOGE node-operator fee.
#
# Supersedes the reverted PR #15 test (test_v36_merged_operator_reward.py), whose
# design was WRONG in three ways the operator called out:
#   (1) it did not mirror the LTC redistribute/never-empty fallback,
#   (2) it SKIPPED bidirectional auto-conversion, empty-dropping ({}) a missing
#       merged operator address instead of auto-converting the parent (LTC) key,
#   (3) it asserted a consensus break that does not exist for the correct design.
#
# The corrected behaviour under test (work.py):
#   * C1  operator_fee_draw flag set when the -f/--fee roll reassigns the whole
#         share to the operator's own key.
#   * C2  WorkerBridge._resolve_operator_merged_addresses(): P1 explicit valid ->
#         P2 auto-convert operator parent key -> P3 pool distribution. NEVER {}.
#   * C3  single guard before the return:
#           - operator_fee_draw  -> REPLACE merged_addresses with the operator's
#             resolved DOGE payout (closes the L2 foreign-DOGE leak),
#           - else never-empty floor: auto-convert whatever pubkey_hash the share
#             pays (covers the _redistribute_share landings).
#   * C5  main.py reverse (DOGE->LTC) auto-conversion + [CHECK 3] display, both in
#         the pure p2pool.operator_merged module.
#
# The KAT drives the REAL WorkerBridge.get_user_details end to end, then feeds the
# committed merged_addresses through the REAL data-layer verifier path
# (get_v36_merged_weights -> build_canonical_merged_coinbase) that every peer
# rebuilds. RED on the pre-fix tree (behaviour absent), GREEN on the branch.
#
# Self-skips honestly only if the p2pool package / twisted / dogecoin net cannot
# be imported at all (a bare CI container). On the CI's python:2.7.18 image with
# twisted installed it runs for real.

import random
import unittest

try:
    from p2pool.work import WorkerBridge
    from p2pool import data as p2pool_data
    from p2pool.bitcoin import data as bitcoin_data
    from p2pool.util import pack
    from p2pool.bitcoin.networks import dogecoin as DOGE
    from p2pool.bitcoin.networks import litecoin as LTC
    from p2pool import operator_merged
    HAVE_DEPS = (DOGE is not None and hasattr(DOGE, 'ADDRESS_VERSION')
                 and LTC is not None and hasattr(LTC, 'ADDRESS_VERSION'))
    _IMPORT_ERR = None
except Exception as _e:
    WorkerBridge = None
    p2pool_data = bitcoin_data = pack = DOGE = LTC = operator_merged = None
    HAVE_DEPS = False
    _IMPORT_ERR = repr(_e)

CHAIN_ID_DOGE = 98

# Three distinct keys: the operator's LTC (parent) key, a requesting miner's key,
# and the operator's SEPARATELY-CHOSEN DOGE commission key. Keeping all three
# distinct is what proves "operator's chosen address, not the miner's, not a
# plain auto-conversion of anyone else's key".
OPERATOR_LTC_PH = 0xaa * (16 ** 38)  # placeholder, overwritten below
FOREIGN_PH = None
OPERATOR_DOGE_PH = None


def _ph_int(b20):
    return pack.IntType(160).unpack(b20)


def _p2pkh_script(ph_int):
    return '\x76\xa9\x14' + pack.IntType(160).pack(ph_int) + '\x88\xac'


def _p2sh_script(ph_int):
    return '\xa9\x14' + pack.IntType(160).pack(ph_int) + '\x87'


def _doge_p2pkh_addr(ph_int):
    return bitcoin_data.pubkey_hash_to_address(ph_int, DOGE.ADDRESS_VERSION, -1, DOGE)


def _doge_p2sh_addr(ph_int):
    return bitcoin_data.pubkey_hash_to_address(ph_int, DOGE.ADDRESS_P2SH_VERSION, -1, DOGE)


def _ltc_p2pkh_addr(ph_int):
    return bitcoin_data.pubkey_hash_to_address(ph_int, LTC.ADDRESS_VERSION, -1, LTC)


class _Net(object):
    PARENT = LTC if LTC is not None else None


class _Node(object):
    net = _Net()


class _Args(object):
    def __init__(self, **kw):
        self.address = kw.get('address', None)
        self.redistribute_mode = kw.get('redistribute_mode', 'fee')
        self.timeaddresses = kw.get('timeaddresses', 1e9)
        self.merged_coind_p2p_port = kw.get('merged_coind_p2p_port', None)


def _make_bridge(operator_ltc_ph, merged_operator_address=None,
                 my_pubkey_type=None, node_owner_fee=0.0, redistribute_mode='fee',
                 address=None):
    """A REAL WorkerBridge (via __new__, like test_whale_latch.py) with exactly
    the fields get_user_details / _resolve_operator_merged_addresses read."""
    if my_pubkey_type is None:
        my_pubkey_type = p2pool_data.PUBKEY_TYPE_P2PKH
    wb = WorkerBridge.__new__(WorkerBridge)
    wb.my_pubkey_hash = operator_ltc_ph
    wb.my_pubkey_type = my_pubkey_type
    wb.merged_operator_address = merged_operator_address
    wb.node_owner_fee = node_owner_fee
    wb.node = _Node()
    wb.args = _Args(redistribute_mode=redistribute_mode, address=address)
    wb._merged_op_addr_cache = {}
    wb._merged_net_cache = {}
    wb._merged_chain_name_cache = {}
    wb._miner_addr_cache = {}
    wb._miner_merged_cache = {}
    wb._miner_merged_display_cache = {}
    return wb


class _FakeShare(object):
    """Just the attributes the O(n) get_v36_merged_weights fallback reads."""
    VERSION = 36
    desired_version = 36

    def __init__(self, new_script, merged_addresses, target=2 ** 240, donation=0):
        self.new_script = new_script
        self.merged_addresses = merged_addresses
        self.target = target
        self.share_data = {'donation': donation}
        self.share_info = {'merged_addresses': merged_addresses}


class _FakeTracker(object):
    """No get_v36_merged_cumulative_weights -> forces the O(n) fallback walk.
    _miner_merged_addr controls whether Tier-1.5 retroactive lookup can fire."""
    def __init__(self, shares, miner_merged_addr=None):
        self._shares = shares
        self._miner_merged_addr = miner_merged_addr if miner_merged_addr is not None else {}

    def get_chain(self, best_share_hash, chain_length):
        for s in self._shares[:chain_length]:
            yield s


def _committed_doge_scripts(share_field_merged_addresses, new_script, miner_merged_addr=None):
    """Run the committed/verified path for a one-share window and return
    (weight_keys, set(output_scripts)). This is exactly what every peer rebuilds
    and verifies via build_canonical_merged_coinbase()."""
    validated = None
    if share_field_merged_addresses:
        validated = share_field_merged_addresses.get('_validated')
    share = _FakeShare(new_script=new_script, merged_addresses=validated)
    tracker = _FakeTracker([share], miner_merged_addr=miner_merged_addr)
    weights, total_weight, donation_weight = p2pool_data.get_v36_merged_weights(
        tracker, best_share_hash='HEAD', chain_length=1, max_weight=2 ** 288 - 1,
        chain_id=CHAIN_ID_DOGE)
    coinbase = p2pool_data.build_canonical_merged_coinbase(
        weights, total_weight, donation_weight,
        coinbase_value=100000000, block_height=1,
        finder_script=None, merged_addr_net=DOGE, parent_net=LTC)
    out_scripts = set(o['script'] for o in coinbase['tx_outs'])
    return set(weights.keys()), out_scripts


@unittest.skipUnless(HAVE_DEPS, 'p2pool/twisted/dogecoin/litecoin net unavailable: %r' % (_IMPORT_ERR,))
class MergedOperatorFee(unittest.TestCase):

    def setUp(self):
        self._orig_uniform = random.uniform
        # Distinct keys.
        self.op_ltc_ph = _ph_int(b'\xaa' * 20)      # operator parent (LTC) key
        self.foreign_ph = _ph_int(b'\xbb' * 20)     # a requesting miner's key
        self.op_doge_ph = _ph_int(b'\xcc' * 20)     # operator's chosen DOGE key
        self.op_doge_addr = _doge_p2pkh_addr(self.op_doge_ph)
        self.op_doge_script = _p2pkh_script(self.op_doge_ph)
        self.foreign_doge_addr = _doge_p2pkh_addr(self.foreign_ph)
        self.foreign_doge_script = _p2pkh_script(self.foreign_ph)
        self.op_ltc_addr = _ltc_p2pkh_addr(self.op_ltc_ph)
        self.op_ltc_new_script = _p2pkh_script(self.op_ltc_ph)
        # operator key auto-converted to DOGE (P2 output) — same hash160
        self.op_autoconv_doge_script = _p2pkh_script(self.op_ltc_ph)
        self.merged_key = lambda s: 'MERGED:' + s.encode('hex')

    def tearDown(self):
        random.uniform = self._orig_uniform

    def _force_draw(self):
        random.uniform = lambda a, b: 0.0     # always < node_owner_fee (>0)

    def _no_draw(self):
        random.uniform = lambda a, b: 100.0   # always >= node_owner_fee

    # ---- K1: explicit valid merged operator addr + fee draw -> pays OPERATOR ----
    def test_K1_explicit_operator_addr_paid_on_fee_draw(self):
        wb = _make_bridge(self.op_ltc_ph, merged_operator_address=self.op_doge_addr,
                          node_owner_fee=100.0)
        self._force_draw()
        # A miner requests with their OWN foreign DOGE; the fee draw seizes the share.
        user = self.op_ltc_addr + ',' + self.foreign_doge_addr
        (u, ph, pt, dst, dpt, merged) = wb.get_user_details(user)
        self.assertTrue(merged.get('_operator_stamped'), 'operator stamp flag set')
        self.assertEqual(merged['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': self.op_doge_script}])
        keys, out_scripts = _committed_doge_scripts(merged, _p2pkh_script(ph))
        self.assertEqual(keys, set([self.merged_key(self.op_doge_script)]))
        self.assertIn(self.op_doge_script, out_scripts)
        self.assertNotIn(self.foreign_doge_script, out_scripts,
                         'foreign miner DOGE must NEVER ride an operator-keyed share')

    # ---- K2: unset/invalid merged addr + fee draw -> AUTO-CONVERT parent key ----
    def test_K2_autoconvert_parent_key_when_unset(self):
        wb = _make_bridge(self.op_ltc_ph, merged_operator_address=None,
                          node_owner_fee=100.0)
        self._force_draw()
        (u, ph, pt, dst, dpt, merged) = wb.get_user_details(
            self.op_ltc_addr + ',' + self.foreign_doge_addr)
        self.assertTrue(merged.get('_validated'), 'never empty')
        self.assertEqual(merged['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': self.op_autoconv_doge_script}])
        # equals what [CHECK 3] would print (same cascade, P2)
        disp_addr, _src = operator_merged.operator_merged_display_address(
            None, self.op_ltc_ph, p2pool_data.PUBKEY_TYPE_P2PKH, DOGE, LTC)
        self.assertEqual(disp_addr, _doge_p2pkh_addr(self.op_ltc_ph))
        keys, out_scripts = _committed_doge_scripts(merged, _p2pkh_script(ph))
        self.assertIn(self.op_autoconv_doge_script, out_scripts)
        self.assertNotIn(self.foreign_doge_script, out_scripts)

    def test_K2b_invalid_explicit_falls_to_autoconvert_not_empty(self):
        wb = _make_bridge(self.op_ltc_ph,
                          merged_operator_address='not-a-valid-doge-address',
                          node_owner_fee=100.0)
        self._force_draw()
        (u, ph, pt, dst, dpt, merged) = wb.get_user_details(
            self.op_ltc_addr + ',' + self.foreign_doge_addr)
        self.assertTrue(merged.get('_validated'), 'P1 failure must land in P2, never {}')
        self.assertEqual(merged['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': self.op_autoconv_doge_script}])

    # ---- K3: never-empty invariant across operator key types ----
    def test_K3_never_empty_for_all_operator_key_types(self):
        cases = [
            (p2pool_data.PUBKEY_TYPE_P2PKH, 'p2pkh'),
            (p2pool_data.PUBKEY_TYPE_P2WPKH, 'p2pkh'),   # normalizes to P2PKH hash160
            (p2pool_data.PUBKEY_TYPE_P2SH, 'p2sh'),
        ]
        for pt_val, kind in cases:
            wb = _make_bridge(self.op_ltc_ph, merged_operator_address=None,
                              my_pubkey_type=pt_val, node_owner_fee=100.0)
            self._force_draw()
            resolved = wb._resolve_operator_merged_addresses(CHAIN_ID_DOGE)
            self.assertIsNotNone(resolved, 'type %r must resolve, never None' % pt_val)
            self.assertTrue(resolved.get('_validated'))
            if kind == 'p2sh':
                self.assertEqual(resolved['_validated'][0]['script'],
                                 _p2sh_script(self.op_ltc_ph))
            else:
                self.assertEqual(resolved['_validated'][0]['script'],
                                 _p2pkh_script(self.op_ltc_ph))

    # ---- K4: redistribute-mirror never-empty floor ----
    def test_K4a_redistribute_fee_mode_commits_operator_doge(self):
        # Empty username, fee mode -> _redistribute_share returns operator key;
        # no explicit merged -> C3 floor auto-converts operator key to DOGE.
        wb = _make_bridge(self.op_ltc_ph, merged_operator_address=None,
                          node_owner_fee=0.0, redistribute_mode='fee')
        self._no_draw()  # not the fee draw; empty user triggers redistribution
        (u, ph, pt, dst, dpt, merged) = wb.get_user_details('')
        self.assertEqual(ph, self.op_ltc_ph, 'fee mode pays operator on LTC')
        self.assertTrue(merged.get('_validated'), 'floor must fill the DOGE leg')
        self.assertEqual(merged['_validated'][0]['script'], _p2pkh_script(self.op_ltc_ph))

    def test_K4b_redistribute_picked_miner_same_owner_both_chains(self):
        wb = _make_bridge(self.op_ltc_ph, merged_operator_address=None,
                          node_owner_fee=0.0, redistribute_mode='pplns')
        self._no_draw()
        # Redistribution lands on a specific miner (pplns pick). Only the pick is
        # stubbed; the floor under test converts whatever pubkey_hash it returns.
        wb._redistribute_share = lambda: (self.foreign_ph, p2pool_data.PUBKEY_TYPE_P2PKH)
        (u, ph, pt, dst, dpt, merged) = wb.get_user_details('')
        self.assertEqual(ph, self.foreign_ph)
        self.assertEqual(merged['_validated'][0]['script'], _p2pkh_script(self.foreign_ph),
                         'merged leg == conversion of the PICKED pubkey_hash')

    def test_K4c_redistribute_fee_honors_explicit_merged_operator_address(self):
        # REGRESSION (redistribute-fee/P1 leak): --redistribute fee collects the
        # unnamed/broken-miner shares onto the operator's OWN key via
        # _redistribute_share (NOT the -f/--fee draw, so operator_fee_draw is
        # False). The share lands on my_pubkey_hash and falls into the C3
        # never-empty floor. With a DISTINCT explicit --merged-operator-address
        # configured, the committed DOGE script MUST be that explicit address
        # (P1), NOT a plain hash-convert of the operator's parent key (P2).
        wb = _make_bridge(self.op_ltc_ph, merged_operator_address=self.op_doge_addr,
                          node_owner_fee=0.0, redistribute_mode='fee')
        self._no_draw()  # not the -f draw; empty user triggers redistribution
        (u, ph, pt, dst, dpt, merged) = wb.get_user_details('')
        # Redistribution in fee mode pays the operator on the parent (LTC) chain.
        self.assertEqual(ph, self.op_ltc_ph, 'fee mode redistributes to operator on LTC')
        self.assertTrue(merged.get('_validated'), 'floor must fill the DOGE leg (never empty)')
        # The explicit commission address (op_doge_ph, \xcc) -- NOT the auto-convert
        # of the parent key (op_ltc_ph, \xaa).
        self.assertEqual(merged['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': self.op_doge_script}],
                         'explicit --merged-operator-address must be honored on the '
                         'redistribute-fee collection path (P1, not P2 auto-convert)')
        self.assertNotEqual(merged['_validated'][0]['script'], self.op_autoconv_doge_script,
                            'must NOT silently pay convert(my_pubkey_hash)')
        # And the committed/verified coinbase (what every peer rebuilds) pays the
        # explicit script, never the parent-key auto-conversion.
        keys, out_scripts = _committed_doge_scripts(merged, _p2pkh_script(ph))
        self.assertIn(self.op_doge_script, out_scripts)
        self.assertNotIn(self.op_autoconv_doge_script, out_scripts,
                         'collected DOGE commission must go to the configured wallet')

    # ---- K9: P3 terminal never-empty -> FIXED pool DONATION fallback ----
    # The operator merged (DOGE) payout can no longer resolve to an EMPTY
    # merged_addresses. When P1 (explicit) and P2 (auto-convert operator parent
    # key) both fail -- an unconvertible / empty operator key -- P3 commits the
    # fixed pool donation script (COMBINED_DONATION_SCRIPT), a compile-time
    # pool constant identical on every node, NOT {}. This closes the only
    # remaining empty-commit path by construction.
    def test_K9a_p3_unresolvable_operator_routes_to_donation(self):
        from p2pool.data import COMBINED_DONATION_SCRIPT
        # No explicit --merged-operator-address AND no convertible operator
        # parent key (my_pubkey_hash None) -> P1 skip, P2 skip, P3 fires.
        wb = _make_bridge(self.op_ltc_ph, merged_operator_address=None,
                          node_owner_fee=100.0)
        wb.my_pubkey_hash = None  # force P3 (nothing to auto-convert)
        resolved = wb._resolve_operator_merged_addresses(CHAIN_ID_DOGE)
        self.assertIsNotNone(resolved, 'P3 must NEVER return None/empty any more')
        self.assertTrue(resolved.get('_donation_fallback'),
                        'P3 result is flagged as the donation fallback')
        self.assertEqual(resolved['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': COMBINED_DONATION_SCRIPT}],
                         'P3 commits the FIXED pool donation script, never {}')
        # And the committed/verified coinbase (what every peer rebuilds) pays
        # the donation script -- byte-for-byte from a node-invariant constant.
        keys, out_scripts = _committed_doge_scripts(resolved, self.op_ltc_new_script)
        self.assertIn(COMBINED_DONATION_SCRIPT, out_scripts,
                      'donation script must appear in the rebuilt merged coinbase')

    def test_K9b_operator_fee_draw_p3_commits_donation_end_to_end(self):
        from p2pool.data import COMBINED_DONATION_SCRIPT
        wb = _make_bridge(self.op_ltc_ph, merged_operator_address=None,
                          node_owner_fee=100.0)
        wb.my_pubkey_hash = None  # unconvertible/empty operator key -> P3
        self._force_draw()
        (u, ph, pt, dst, dpt, merged) = wb.get_user_details(
            self.op_ltc_addr + ',' + self.foreign_doge_addr)
        self.assertTrue(merged.get('_validated'),
                        'operator-fee P3 share must NEVER commit empty merged_addresses')
        self.assertEqual(merged['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': COMBINED_DONATION_SCRIPT}])
        _, out_scripts = _committed_doge_scripts(merged, self.op_ltc_new_script)
        self.assertNotIn(self.foreign_doge_script, out_scripts,
                         'foreign miner DOGE must never ride the P3 donation-fallback share')

    def test_K9c_pubkey_hash_none_edge_routes_to_donation(self):
        from p2pool.data import COMBINED_DONATION_SCRIPT
        wb = _make_bridge(self.op_ltc_ph, merged_operator_address=None,
                          node_owner_fee=0.0, redistribute_mode='pplns')
        self._no_draw()
        # A redistribute landing that pays NO ONE on the parent chain
        # (pubkey_hash None). The C3 elif skips (pubkey_hash is None); the
        # terminal never-empty backstop must still commit the donation script.
        wb._redistribute_share = lambda: (None, p2pool_data.PUBKEY_TYPE_P2PKH)
        (u, ph, pt, dst, dpt, merged) = wb.get_user_details('')
        self.assertIsNone(ph, 'this edge pays no one on the parent chain')
        self.assertTrue(merged.get('_validated'),
                        'pubkey_hash-None share must never commit empty merged_addresses')
        self.assertTrue(merged.get('_donation_fallback'))
        self.assertEqual(merged['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': COMBINED_DONATION_SCRIPT}])

    def test_K9d_p1_p2_unchanged_by_donation_fallback(self):
        # No regression: P1 (explicit) and P2 (auto-convert) still resolve to
        # the operator's OWN address, never the donation script.
        from p2pool.data import COMBINED_DONATION_SCRIPT
        wbP1 = _make_bridge(self.op_ltc_ph, merged_operator_address=self.op_doge_addr,
                            node_owner_fee=100.0)
        r1 = wbP1._resolve_operator_merged_addresses(CHAIN_ID_DOGE)
        self.assertEqual(r1['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': self.op_doge_script}])
        self.assertNotEqual(r1['_validated'][0]['script'], COMBINED_DONATION_SCRIPT)
        self.assertFalse(r1.get('_donation_fallback'), 'P1 is not a donation fallback')
        wbP2 = _make_bridge(self.op_ltc_ph, merged_operator_address=None,
                            node_owner_fee=100.0)
        r2 = wbP2._resolve_operator_merged_addresses(CHAIN_ID_DOGE)
        self.assertEqual(r2['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': self.op_autoconv_doge_script}])
        self.assertNotEqual(r2['_validated'][0]['script'], COMBINED_DONATION_SCRIPT)
        self.assertFalse(r2.get('_donation_fallback'), 'P2 is not a donation fallback')

    # ---- K5: reverse conversion (DOGE -> LTC) version mapping exact ----
    def test_K5_reverse_convert_p2pkh_and_p2sh(self):
        # DOGE P2PKH -> LTC P2PKH
        res = operator_merged.reverse_convert_operator_address(
            _doge_p2pkh_addr(self.op_doge_ph), DOGE, LTC)
        self.assertIsNotNone(res)
        ph, parent_addr, pt = res
        self.assertEqual(ph, self.op_doge_ph)
        self.assertEqual(pt, p2pool_data.PUBKEY_TYPE_P2PKH)
        self.assertEqual(parent_addr, _ltc_p2pkh_addr(self.op_doge_ph))
        # DOGE P2SH -> LTC P2SH
        res2 = operator_merged.reverse_convert_operator_address(
            _doge_p2sh_addr(self.op_doge_ph), DOGE, LTC)
        self.assertIsNotNone(res2)
        ph2, parent_addr2, pt2 = res2
        self.assertEqual(ph2, self.op_doge_ph)
        self.assertEqual(pt2, p2pool_data.PUBKEY_TYPE_P2SH)
        self.assertEqual(parent_addr2,
                         bitcoin_data.pubkey_hash_to_address(self.op_doge_ph, LTC.ADDRESS_P2SH_VERSION, -1, LTC))

    # ---- K6: explicit-beats-configured (operator mines directly, no draw) ----
    def test_K6_explicit_combined_beats_configured_no_draw(self):
        wb = _make_bridge(self.op_ltc_ph, merged_operator_address=self.op_doge_addr,
                          node_owner_fee=0.0)
        self._no_draw()
        # Operator connects with an explicit valid LTC,DOGE_x that is NOT the
        # configured commission address. No fee draw -> keep DOGE_x.
        user = self.op_ltc_addr + ',' + self.foreign_doge_addr
        (u, ph, pt, dst, dpt, merged) = wb.get_user_details(user)
        self.assertFalse(merged.get('_operator_stamped'),
                         'no fee draw -> operator stamp must NOT fire')
        self.assertEqual(merged['_validated'],
                         [{'chain_id': CHAIN_ID_DOGE, 'script': self.foreign_doge_script}],
                         'explicit request DOGE kept (tier1 > configured)')

    # ---- K7: multi-operator independence + Tier-1.5 is dead for explicit shares ----
    def test_K7_multi_operator_consensus_from_committed_bytes(self):
        opA = _make_bridge(_ph_int(b'\x11' * 20),
                           merged_operator_address=_doge_p2pkh_addr(_ph_int(b'\xa1' * 20)),
                           node_owner_fee=100.0)
        opB = _make_bridge(_ph_int(b'\x22' * 20),
                           merged_operator_address=_doge_p2pkh_addr(_ph_int(b'\xb2' * 20)),
                           node_owner_fee=100.0)
        self._force_draw()
        _, phA, _, _, _, mergedA = opA.get_user_details(
            _ltc_p2pkh_addr(_ph_int(b'\x11' * 20)) + ',' + self.foreign_doge_addr)
        _, phB, _, _, _, mergedB = opB.get_user_details(
            _ltc_p2pkh_addr(_ph_int(b'\x22' * 20)) + ',' + self.foreign_doge_addr)
        scriptA = _p2pkh_script(_ph_int(b'\xa1' * 20))
        scriptB = _p2pkh_script(_ph_int(b'\xb2' * 20))
        self.assertEqual(mergedA['_validated'], [{'chain_id': CHAIN_ID_DOGE, 'script': scriptA}])
        self.assertEqual(mergedB['_validated'], [{'chain_id': CHAIN_ID_DOGE, 'script': scriptB}])

        shareA = _FakeShare(_p2pkh_script(phA), mergedA['_validated'])
        shareB = _FakeShare(_p2pkh_script(phB), mergedB['_validated'])

        # Node 1: empty Tier-1.5 map. Node 2: a DELIBERATELY WRONG _miner_merged_addr
        # binding for the same new_scripts. Because every share carries an explicit
        # Tier-1 entry, Tier-1.5 is never consulted -> identical weights on both.
        wrong = {CHAIN_ID_DOGE: {
            shareA.new_script: self.foreign_doge_script,
            shareB.new_script: self.foreign_doge_script,
        }}
        w_clean, tw1, dw1 = p2pool_data.get_v36_merged_weights(
            _FakeTracker([shareA, shareB]), 'HEAD', 2, 2 ** 288 - 1, CHAIN_ID_DOGE)
        w_wrong, tw2, dw2 = p2pool_data.get_v36_merged_weights(
            _FakeTracker([shareA, shareB], miner_merged_addr=wrong), 'HEAD', 2, 2 ** 288 - 1, CHAIN_ID_DOGE)
        self.assertEqual(w_clean, w_wrong,
                         'Tier-1.5 latch must be dead code for explicit shares (consensus-safe)')
        self.assertEqual(set(w_clean.keys()),
                         set([self.merged_key(scriptA), self.merged_key(scriptB)]))

    # ---- K8: served==committed — the served-only override code is gone ----
    def test_K8_served_only_override_removed(self):
        import inspect
        src = inspect.getsource(WorkerBridge.get_current_txouts) \
            if hasattr(WorkerBridge, 'get_current_txouts') else ''
        # The removed served-only overrides called _get_validated_merged_operator_address
        # inside the merged distribution loop. With them gone, the served coinbase is a
        # pure function of the committed merged_addresses (== the verifier's input).
        import p2pool.work as _wsrc
        work_src = inspect.getsource(_wsrc)
        self.assertNotIn('Node operator override for raw script keys', work_src,
                         'served-only raw-script override must be deleted')
        self.assertNotIn('use it for the operator\'s own share of merged chain payout', work_src,
                         'served-only address-string override must be deleted')


if __name__ == '__main__':
    unittest.main()
