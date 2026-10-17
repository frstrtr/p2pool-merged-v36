# -*- coding: utf-8 -*-
'''
OFFLINE golden-vector generator, set #2: LTC gentx over a POPULATED tracker.

Set #1 (p2pool/test/gen_gentx_golden.py) runs against an EMPTY tracker, so
every payout collapses into the donation output.  This set seeds the tracker
with a short, fixed share chain from five payout scripts (P2PKH + P2WPKH,
unequal targets and donation values) so the gentx carries real PPLNS payout
outputs, and the V36 script-keyed weight path, the (amount, script) dests
sort, the integer rounding remainder into the donation output and the
merged_payout_hash commitment are all exercised.

The tracker is an OkayTracker (same class the node uses) filled with stub
shares that carry ONLY the attributes the V36 gentx path reads.  Real Share
objects would need scrypt PoW at MAX_TARGET for every seed share; the gentx
path never looks at PoW, header or hash_link, so the stub is exact for this
purpose.  The fixture records the full tracker state (one record per share)
so the c2pool side can rebuild the same chain without p2pool code:

  tracker_state.shares[i]  (genesis first, tip last)
    hash, previous_share_hash       chain linkage (tip = previous_share_hash
                                    of the share being generated)
    bits / target_hex               share target (weight = 2**256//(target+1))
    max_bits / max_target_hex       max_target (tracker min_work delta only)
    timestamp, absheight, abswork   copied into the next share_info
    pubkey_hash, pubkey_type        -> new_script (the PPLNS weight key)
    new_script_hex                  the derived scriptPubKey, for cross-check
    donation                        per-share donation (0..65535)
    desired_version                 36 (merged weights skip < 36)
    merged_addresses                None (no explicit merged address)

Intermediate expectations are emitted next to the artifact so a mismatch can
be localised: the decayed PPLNS weights (keyed by script hex), total and
donation weight, the ordered payout outputs and merged_payout_hash.

OLD (Share.generate_transaction) and NEW (generate_transaction_template +
finalize_generate_transaction) are both run and must be byte-identical; the
O(n) linear merged-weights walk is cross-checked against the OkayTracker
skip-list result.

Run:
    ~/.pyenv/versions/2.7.18/bin/python -m p2pool.test.gen_gentx_golden_pplns
'''

from __future__ import division

import json
import os

from p2pool import data as p2pool_data
from p2pool.bitcoin import data as bitcoin_data
from p2pool.util import forest
from p2pool.networks import litecoin as ltc_net
from p2pool.test import gen_gentx_golden as gen


GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden', 'pplns')


# --------------------------------------------------------------------------
# tracker seed
# --------------------------------------------------------------------------
_P2PKH, _P2WPKH = 0, 1

def seed_miners():
    '''(label, pubkey_hash, pubkey_type).  The first two reuse set #1's
    difftest hashes so the generating miner also holds PPLNS weight.
    p2wpkh_c and p2pkh_e share one pubkey_hash on purpose: V36 keys weights
    by script bytes, so they must stay two distinct payout outputs.'''
    base = 0x0123456789abcdef0123456789abcdef01234567
    shared = 0x89abcdef0123456789abcdef0123456789abcdef
    return [
        ('p2pkh_a',  base % 2**160,       _P2PKH),
        ('p2wpkh_b', (base * 2) % 2**160, _P2WPKH),
        ('p2wpkh_c', shared,              _P2WPKH),
        ('p2pkh_d',  0xfedcba9876543210fedcba9876543210fedcba98, _P2PKH),
        ('p2pkh_e',  shared,              _P2PKH),
    ]


def seed_chain():
    '''Genesis-first (miner_label, target_divisor, donation).  Unequal
    targets/donations so no two scripts end up with equal weight.'''
    return [
        ('p2pkh_a',  1, 655),
        ('p2wpkh_c', 2, 0),
        ('p2wpkh_b', 1, 655),
        ('p2pkh_d',  3, 1310),
        ('p2pkh_a',  2, 655),
        ('p2pkh_e',  1, 100),
        ('p2wpkh_b', 4, 655),
    ]


class StubShare(object):
    '''Carries exactly the share attributes the V36 gentx path reads
    (generate_transaction, generate_transaction_template,
    get_decayed_cumulative_weights, MergedWeightsSkipList, OkayTracker).'''
    VERSION = 36

    def __init__(self, net, hash, previous_hash, bits, max_bits, timestamp,
                 absheight, abswork, pubkey_hash, pubkey_type, donation):
        self.hash = hash
        self.previous_hash = previous_hash
        self.target = bits.target
        self.max_target = max_bits.target
        self.timestamp = timestamp
        self.absheight = absheight
        self.abswork = abswork
        self.desired_version = 36
        self.merged_addresses = None
        self.share_data = dict(
            previous_share_hash=previous_hash,
            pubkey_hash=pubkey_hash,
            pubkey_type=pubkey_type,
            donation=donation,
            desired_version=36,
        )
        self.share_info = dict(share_data=self.share_data, merged_addresses=None,
                               bits=bits, max_bits=max_bits)
        # same derivation as BaseShare.__init__ for VERSION >= 36
        _ver, _witver = p2pool_data.pubkey_type_to_version_witver(pubkey_type, net.PARENT)
        self.new_script = bitcoin_data.pubkey_hash_to_script2(
            pubkey_hash, _ver, _witver, net.PARENT)


def build_seed_shares(net):
    miners = dict((label, (ph, pt)) for label, ph, pt in seed_miners())
    chain = seed_chain()
    max_bits = bitcoin_data.FloatingInteger.from_target_upper_bound(net.MAX_TARGET)
    shares = []
    prev_hash = None
    abswork = 0
    for i, (label, divisor, donation) in enumerate(chain):
        bits = bitcoin_data.FloatingInteger.from_target_upper_bound(net.MAX_TARGET // divisor)
        abswork = (abswork + bitcoin_data.target_to_average_attempts(bits.target)) % 2**128
        pubkey_hash, pubkey_type = miners[label]
        # deterministic synthetic share hash
        share_hash = bitcoin_data.hash256('t5-pplns-seed-share-%d' % i)
        shares.append((label, StubShare(
            net, share_hash, prev_hash, bits, max_bits,
            timestamp=int(gen.FROZEN_EPOCH) - net.SHARE_PERIOD * (len(chain) - i),
            absheight=i + 1, abswork=abswork,
            pubkey_hash=pubkey_hash, pubkey_type=pubkey_type, donation=donation)))
        prev_hash = share_hash
    return shares


def make_tracker(net, shares, cls=None):
    tracker = p2pool_data.OkayTracker(net) if cls is None else cls()
    for _, s in shares:
        tracker.add(s)
    return tracker


def tracker_state_json(shares):
    out = []
    for label, s in shares:
        out.append(dict(
            miner=label,
            hash='%064x' % s.hash,
            previous_share_hash=None if s.previous_hash is None else '%064x' % s.previous_hash,
            bits=s.share_info['bits'].bits,
            target_hex='%064x' % s.target,
            max_bits=s.share_info['max_bits'].bits,
            max_target_hex='%064x' % s.max_target,
            timestamp=s.timestamp,
            absheight=s.absheight,
            abswork=s.abswork,
            pubkey_hash='%040x' % s.share_data['pubkey_hash'],
            pubkey_type=s.share_data['pubkey_type'],
            new_script_hex=s.new_script.encode('hex'),
            donation=s.share_data['donation'],
            desired_version=s.desired_version,
            merged_addresses=None,
        ))
    return dict(order='genesis_first', tip_hash=out[-1]['hash'], shares=out)


# --------------------------------------------------------------------------
# intermediate expectations
# --------------------------------------------------------------------------
def pplns_expectations(net, tracker, tip_hash, block_target, gentx):
    height = tracker.get_height(tip_hash)
    weights, total, donation = p2pool_data.get_decayed_cumulative_weights(
        tracker, tip_hash, min(height, net.REAL_CHAIN_LENGTH), 2**288 - 1, net)
    payouts = [dict(script_hex=o['script'].encode('hex'), value=o['value'])
               for o in gentx['tx_outs'] if o['value'] > 0]
    mph = p2pool_data.compute_merged_payout_hash(tracker, tip_hash, block_target, net)
    return dict(
        pplns_window_shares=min(height, net.REAL_CHAIN_LENGTH),
        decayed_weights=dict((k.encode('hex'), v) for k, v in weights.iteritems()),
        total_weight=total,
        donation_weight=donation,
        payout_outputs=payouts,
        merged_payout_hash_hex=None if mph is None else '%064x' % mph,
    )


def scenarios():
    return [('empty', 0), ('twotx', 2)]


class _PaddingBugfix(object):
    '''A live node sets net.PARENT.padding_bugfix = True once it holds >= v35
    shares (data.load_share / OkayTracker.think).  Without it a P2WPKH
    pubkey_hash with a leading zero nibble cannot be turned into a script
    ('{:x}' drops the zero), so pin the live-node value for the run and
    restore it afterwards (set #1 runs in the same process under unittest).'''
    def __init__(self, net):
        self.parent = net.PARENT

    def __enter__(self):
        self._had = hasattr(self.parent, 'padding_bugfix')
        self._old = getattr(self.parent, 'padding_bugfix', None)
        self.parent.padding_bugfix = True
        return self

    def __exit__(self, *exc):
        if self._had:
            self.parent.padding_bugfix = self._old
        else:
            del self.parent.padding_bugfix
        return False


def run(emit=True, verbose=True):
    with _PaddingBugfix(ltc_net):
        return _run(emit, verbose)


def _run(emit, verbose):
    net = ltc_net
    share_type = p2pool_data.MergedMiningShare
    v36_active = share_type.VERSION >= 36
    block_target = net.MAX_TARGET
    desired_target = 2**256 - 1
    subsidy = 1250000000
    height = 2000000

    if emit and not os.path.isdir(GOLDEN_DIR):
        os.makedirs(GOLDEN_DIR)

    shares = build_seed_shares(net)
    tip_hash = shares[-1][1].hash
    state = tracker_state_json(shares)

    # skip-list (OkayTracker) vs linear-walk merged weights must agree, or the
    # committed merged_payout_hash would depend on the tracker class
    mph_skip = p2pool_data.compute_merged_payout_hash(
        make_tracker(net, shares), tip_hash, block_target, net)
    mph_lin = p2pool_data.compute_merged_payout_hash(
        make_tracker(net, shares, forest.Tracker), tip_hash, block_target, net)

    all_mismatches = []
    if mph_skip != mph_lin:
        all_mismatches.append('merged_payout_hash: skip list %r != linear walk %r' % (mph_skip, mph_lin))

    results = []
    for scen_name, n in scenarios():
        tx_hashes, txs, fees = gen.build_gbt_txset(n)
        for user, pubkey_hash, pubkey_type in gen.default_users():
            tracker = make_tracker(net, shares)
            with gen._FrozenDeterminism():
                share_data = gen.build_share_data(net, share_type, pubkey_hash,
                                                  pubkey_type, subsidy, height)
                share_data['previous_share_hash'] = tip_hash
                old = gen.run_old_path(share_type, tracker, net, share_data,
                                       block_target, desired_target,
                                       tx_hashes, txs, fees, subsidy, v36_active)
                new = gen.run_new_path(share_type, tracker, net, share_data,
                                       block_target, desired_target,
                                       tx_hashes, txs, fees, subsidy, v36_active)

            tag = '%s/%s' % (scen_name, user)
            for msg in gen.compare_paths(old, new):
                all_mismatches.append('%s: %s' % (tag, msg))

            art = gen.build_artifact(share_type, net, old)
            if art != gen.build_artifact(share_type, net, new):
                all_mismatches.append('%s: serialized artifact OLD != NEW' % tag)
            if art['new_tx_bytes'] > gen.NEW_TX_BUDGET_BYTES:
                all_mismatches.append('%s: new_tx_bytes %d exceed 50kB budget'
                                      % (tag, art['new_tx_bytes']))

            expect = pplns_expectations(net, tracker, tip_hash, block_target, old['gentx'])
            # coverage guards: real PPLNS outputs, P2PKH + P2WPKH, no ties
            miner_outs = expect['payout_outputs'][:-1]   # last = donation
            if len(miner_outs) < 3:
                all_mismatches.append('%s: only %d miner payout outputs' % (tag, len(miner_outs)))
            kinds = set(o['script_hex'][:4] for o in miner_outs)
            if not ('76a9' in kinds and '0014' in kinds):
                all_mismatches.append('%s: payouts lack P2PKH+P2WPKH mix (%r)' % (tag, sorted(kinds)))
            vals = [o['value'] for o in miner_outs]
            if len(set(vals)) != len(vals):
                all_mismatches.append('%s: equal payout values %r' % (tag, vals))

            if verbose:
                print '[GOLDEN-PPLNS] %-22s txs=%d outs=%d gentx=%s... OLD==NEW:%s' % (
                    tag, n, len(expect['payout_outputs']), art['gentx_hex'][:32],
                    'yes' if not gen.compare_paths(old, new) else 'NO')

            fixture = dict(
                _comment=('OFFLINE golden vector set #2 for the LTC gentx path: '
                          'populated tracker (PPLNS payout outputs). Frozen '
                          'reference target for c2pool ltc_gentx_dump. '
                          'Regenerate with p2pool.test.gen_gentx_golden_pplns.'),
                scenario=scen_name,
                user=user,
                net='litecoin',
                padding_bugfix=True,
                share_version=share_type.VERSION,
                v36_active=v36_active,
                pubkey_hash='%040x' % pubkey_hash,
                pubkey_type=pubkey_type,
                subsidy=subsidy,
                height=height,
                frozen_epoch=gen.FROZEN_EPOCH,
                block_target_hex='%064x' % block_target,
                desired_target_hex='%064x' % desired_target,
                gbt_transactions=[bitcoin_data.tx_type.pack(t).encode('hex') for t in txs],
                gbt_transaction_hashes=['%064x' % h for h in tx_hashes],
                gbt_transaction_fees=fees,
                previous_share_hash='%064x' % tip_hash,
                tracker_state=state,
                expected_intermediate=expect,
                artifact=art,
            )
            basename = 'ltc_gentx_pplns_%s_%s.json' % (scen_name, user)
            results.append((basename, fixture))
            if emit:
                with open(os.path.join(GOLDEN_DIR, basename), 'wb') as f:
                    json.dump(fixture, f, indent=2, sort_keys=True)
                    f.write('\n')

    if verbose and emit:
        print '[GOLDEN-PPLNS] emitted %d fixtures to %s' % (len(results), GOLDEN_DIR)

    if all_mismatches:
        raise AssertionError('gentx PPLNS OFFLINE differential FAILED (%d):\n%s'
                             % (len(all_mismatches), '\n'.join(all_mismatches)))
    return results


if __name__ == '__main__':
    results = run(emit=True, verbose=True)
    print 'OK: OLD == NEW byte-identical over populated tracker; %d fixtures written.' % len(results)
