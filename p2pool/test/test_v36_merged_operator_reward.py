# -*- coding: utf-8 -*-
# Python 2.7 reward-path regression: --merged-operator-address on the -f fee path.
#
# BUG (reproduced by test_bug_*): when the operator's -f/--fee reassigns a
# requesting miner's share to the operator, the requester's DOGE merged_addresses
# rode along on the operator's share. get_v36_merged_weights() then keyed the
# operator's merged weight as 'MERGED:<foreign_doge_script>', and
# build_canonical_merged_coinbase() -- the deterministic coinbase that peers
# REBUILD AND VERIFY (data.py verify_share_merged_coinbase raises ValueError on
# mismatch) -- paid that FOREIGN DOGE script. The old work.py operator override
# only rewrote the *served* coinbase, which build_canonical_merged_coinbase never
# sees, so on any multi-node pool the served coinbase diverged from the committed
# distribution and was rejected.
#
# FIX (proved by test_operator_stamp_*): the operator's chosen merged payout
# address is committed INTO the share at creation
# (WorkerBridge._operator_merged_addresses), so get_v36_merged_weights() keys the
# operator's weight as 'MERGED:<operator_doge_script>'. The served coinbase, the
# canonical verifier, and compute_merged_payout_hash() then all consume the SAME
# key -> served == committed on every node. This is the invariant the KAT asserts.
#
# On master the WorkerBridge._operator_merged_addresses method does not exist, so
# the fix tests error (RED). With the fix they pass (GREEN).
#
# Self-skips (honest-green, matching pr-verify.yml) when twisted / the p2pool
# package are unavailable, e.g. a bare CI container with no deps installed.

import unittest

try:
    from p2pool.work import WorkerBridge
    from p2pool import data as p2pool_data
    from p2pool.bitcoin import data as bitcoin_data
    from p2pool.util import pack
    from p2pool.bitcoin.networks import dogecoin as DOGE
    HAVE_DEPS = DOGE is not None and hasattr(DOGE, 'ADDRESS_VERSION')
    _IMPORT_ERR = None
except Exception as _e:  # ImportError or any transitive failure (twisted, etc.)
    WorkerBridge = None
    p2pool_data = None
    bitcoin_data = None
    pack = None
    DOGE = None
    HAVE_DEPS = False
    _IMPORT_ERR = repr(_e)


CHAIN_ID_DOGE = 98

# Three distinct keys so "operator's chosen commission address" is provably not
# the foreign miner's, nor a plain auto-conversion of anyone else's key.
OPERATOR_LTC_PH_BYTES = b'\xaa' * 20   # operator's parent (LTC) key -> my_pubkey_hash
FOREIGN_PH_BYTES = b'\xbb' * 20        # a requesting miner's key
OPERATOR_DOGE_PH_BYTES = b'\xcc' * 20  # operator's CHOSEN DOGE commission key


def _ph_int(bytes20):
    return pack.IntType(160).unpack(bytes20)


def _doge_p2pkh_script(bytes20):
    ph = _ph_int(bytes20)
    return bitcoin_data.pubkey_hash_to_script2(ph, DOGE.ADDRESS_VERSION, -1, DOGE)


def _doge_addr(bytes20):
    ph = _ph_int(bytes20)
    return bitcoin_data.pubkey_hash_to_address(ph, DOGE.ADDRESS_VERSION, -1, DOGE)


def _ltc_p2pkh_script(bytes20):
    # P2PKH script template is chain-independent (opcodes are identical);
    # the operator's parent new_script carries the operator's LTC pubkey_hash.
    return '\x76\xa9\x14' + bytes20 + '\x88\xac'


class _FakeShare(object):
    """Exactly the attributes get_v36_merged_weights()' O(n) fallback reads."""
    VERSION = 36
    desired_version = 36

    def __init__(self, new_script, merged_addresses, target=2 ** 240, donation=0):
        self.new_script = new_script
        self.merged_addresses = merged_addresses   # stored list form, or None
        self.target = target
        self.share_data = {'donation': donation}
        self.share_info = {'merged_addresses': merged_addresses}


class _FakeTracker(object):
    """No get_v36_merged_cumulative_weights -> forces the O(n) fallback walk.
    No _miner_merged_addr -> Tier 1.5 retroactive lookup is a no-op."""
    def __init__(self, shares):
        self._shares = shares

    def get_chain(self, best_share_hash, chain_length):
        for s in self._shares[:chain_length]:
            yield s


def _make_operator_bridge():
    """A REAL WorkerBridge (via __new__, mirroring test_whale_latch.py) with just
    the operator identity fields the stamp helper reads."""
    wb = WorkerBridge.__new__(WorkerBridge)
    wb.my_pubkey_hash = _ph_int(OPERATOR_LTC_PH_BYTES)
    wb.my_pubkey_type = 0
    wb.merged_operator_address = _doge_addr(OPERATOR_DOGE_PH_BYTES)
    wb._merged_op_addr_cache = {}
    return wb


def _committed_doge_output_scripts(share_field_merged_addresses, new_script):
    """Run the committed/verified path end to end for a one-share window:
    get_v36_merged_weights() -> build_canonical_merged_coinbase(), returning the
    set of DOGE output scripts AND the weight-map keys. Both the served path
    (work.py distribution loop) and the peer verifier consume these same weights,
    so their DOGE payout is a pure function of this key set."""
    share = _FakeShare(new_script=new_script, merged_addresses=share_field_merged_addresses)
    tracker = _FakeTracker([share])
    weights, total_weight, donation_weight = p2pool_data.get_v36_merged_weights(
        tracker, best_share_hash='HEAD', chain_length=1, max_weight=2 ** 288 - 1,
        chain_id=CHAIN_ID_DOGE)
    coinbase = p2pool_data.build_canonical_merged_coinbase(
        weights, total_weight, donation_weight,
        coinbase_value=100000000, block_height=1,
        finder_script=None, merged_addr_net=DOGE, parent_net=DOGE)
    out_scripts = set(o['script'] for o in coinbase['tx_outs'])
    return weights, out_scripts


@unittest.skipUnless(HAVE_DEPS, 'p2pool/twisted/dogecoin_net unavailable: %r' % (_IMPORT_ERR,))
class MergedOperatorRewardPath(unittest.TestCase):

    def setUp(self):
        self.operator_doge_script = _doge_p2pkh_script(OPERATOR_DOGE_PH_BYTES)
        self.foreign_doge_script = _doge_p2pkh_script(FOREIGN_PH_BYTES)
        self.operator_new_script = _ltc_p2pkh_script(OPERATOR_LTC_PH_BYTES)
        self.operator_merged_key = 'MERGED:' + self.operator_doge_script.encode('hex')
        self.foreign_merged_key = 'MERGED:' + self.foreign_doge_script.encode('hex')

    # ---- RED-documenting: the bug, simulated at the data layer (runs on both) ----
    def test_bug_foreign_binding_pays_foreign_not_operator(self):
        """Master behaviour: the foreign requester's DOGE rides on the operator's
        -f-reassigned share -> the committed/verified DOGE coinbase pays the
        FOREIGN script, never the operator's --merged-operator-address."""
        foreign_field = [{'chain_id': CHAIN_ID_DOGE, 'script': self.foreign_doge_script}]
        weights, out_scripts = _committed_doge_output_scripts(
            foreign_field, self.operator_new_script)
        self.assertIn(self.foreign_merged_key, weights,
                      'precondition: foreign DOGE is what the share commits')
        self.assertIn(self.foreign_doge_script, out_scripts,
                      'bug: committed coinbase pays the foreign miner')
        self.assertNotIn(self.operator_doge_script, out_scripts,
                         'bug: operator --merged-operator-address is NOT paid')

    # ---- GREEN: the fix. RED on master (method absent -> AttributeError) ----
    def test_operator_stamp_makes_served_equal_committed(self):
        """The -f cross-stamp must commit the operator's chosen merged address so
        the served coinbase and the committed/verified coinbase agree and pay
        --merged-operator-address."""
        wb = _make_operator_bridge()
        # State at the -f cross-stamp point: pubkey_hash already reassigned to the
        # operator, but merged_addresses still holds the REQUESTER's foreign DOGE.
        requester_merged = {
            'dogecoin': _doge_addr(FOREIGN_PH_BYTES),
            '_validated': [{'chain_id': CHAIN_ID_DOGE, 'script': self.foreign_doge_script}],
        }
        stamped = wb._operator_merged_addresses(requester_merged, DOGE, CHAIN_ID_DOGE)

        # The share now commits the operator's chosen DOGE script, not the foreign one.
        share_field = stamped['_validated']
        self.assertEqual(share_field, [{'chain_id': CHAIN_ID_DOGE, 'script': self.operator_doge_script}])

        weights, out_scripts = _committed_doge_output_scripts(
            share_field, self.operator_new_script)

        # served == committed: BOTH the work.py distribution loop and the peer
        # verifier key the operator's weight identically, on the operator address.
        self.assertEqual(set(weights.keys()), set([self.operator_merged_key]),
                         'weight map keyed solely on the operator merged address')
        self.assertIn(self.operator_doge_script, out_scripts,
                      'fix: committed coinbase pays --merged-operator-address')
        self.assertNotIn(self.foreign_doge_script, out_scripts,
                         'fix: foreign DOGE is never paid on the operator share')

    def test_no_operator_address_drops_foreign_binding(self):
        """Without --merged-operator-address, a foreign DOGE must still be dropped
        from an operator-owned share (so it auto-converts to the operator's own
        hash-preserving DOGE, deterministic on every node) -- never bound foreign."""
        wb = _make_operator_bridge()
        wb.merged_operator_address = None
        requester_merged = {
            'dogecoin': _doge_addr(FOREIGN_PH_BYTES),
            '_validated': [{'chain_id': CHAIN_ID_DOGE, 'script': self.foreign_doge_script}],
        }
        result = wb._operator_merged_addresses(requester_merged, DOGE, CHAIN_ID_DOGE)
        self.assertEqual(result, {}, 'foreign merged addresses dropped, not bound to operator')

    def test_operator_own_combined_address_preserved(self):
        """Legitimate case: the operator themselves supplied a combined LTC,DOGE
        whose DOGE key IS the operator's key. With no explicit override configured
        it must be preserved (not clobbered)."""
        wb = _make_operator_bridge()
        wb.merged_operator_address = None
        own_doge_script = _doge_p2pkh_script(OPERATOR_LTC_PH_BYTES)  # hash == my_pubkey_hash
        own_merged = {
            'dogecoin': _doge_addr(OPERATOR_LTC_PH_BYTES),
            '_validated': [{'chain_id': CHAIN_ID_DOGE, 'script': own_doge_script}],
        }
        result = wb._operator_merged_addresses(own_merged, DOGE, CHAIN_ID_DOGE)
        self.assertIs(result, own_merged, 'operator-owned merged addresses passed through unchanged')


if __name__ == '__main__':
    unittest.main()
