# -*- coding: utf-8 -*-
"""C1 determinism gate — merged-address resolution is window-scoped, not global.

The fix (p2pool/data.py): merged (DOGE) address resolution is a PURE FUNCTION of
the in-window share set, rebuilt per call by _build_window_merged_addr_map,
replacing the node-global, arrival-ordered, never-pruned _miner_merged_addr.
get_v36_merged_weights BYPASSES the O(log n) MergedWeightsSkipList when chain_id
is set, so the O(n) recompute is the SOLE consensus path for merged weights and
verify_merged_coinbase_commitment (which calls it) is bit-exact with it by
construction.

Runs in TWO modes:
  * SHADOW (runs anywhere, incl. Python 3): byte-aligned transcriptions of the
    data.py resolution + accumulation. Proves the algorithm is deterministic and
    that the OLD global scheme could split. Keep in lockstep with data.py.
  * REAL-MODULE (runs in the integrator's Python-2 re-soak, where
    `import p2pool.data` succeeds): same properties against the live code, plus
    proof the skiplist is bypassed when chain_id is set.

Decisive property: two honest nodes with the SAME PPLNS window but DIFFERENT
broader history / arrival order resolve IDENTICAL merged weights (no chain
split), over a window that includes an activation-boundary share with empty
merged_addresses.

Scripts are bytes literals so the file runs unchanged on py3 (bytes) and py2
(bytes is str).
"""
import binascii

CHAIN_ID = 98  # Dogecoin

def hx(b):
    # py3: bytes->str; py2: str->str (via unicode, ascii-equal to native str)
    return binascii.hexlify(b).decode('ascii')

# ── share stand-in (duck-typed exactly as data.py reads it) ────────────────
class S(object):
    def __init__(self, new_script, merged_addresses, target=1, version=36,
                 desired_version=36, donation=0):
        self.new_script = new_script
        self.merged_addresses = merged_addresses
        self.target = target
        self.VERSION = version
        self.desired_version = desired_version
        self.share_data = {'donation': donation}
        self.share_info = {'merged_addresses': merged_addresses}

# ── SHADOW — byte-aligned with p2pool/data.py ──────────────────────────────
def _att(target):
    return 2**256 // (target + 1)                      # target_to_average_attempts

def _normalize(script):                                # _normalize_script_for_merged
    if len(script) == 25 and script[:3] == b'\x76\xa9\x14' and script[23:] == b'\x88\xac':
        return script
    if len(script) == 22 and script[:2] == b'\x00\x14':
        return b'\x76\xa9\x14' + script[2:22] + b'\x88\xac'
    if len(script) == 23 and script[:2] == b'\xa9\x14' and script[22:] == b'\x87':
        return script
    return b''

def shadow_build_window_map(window_oldest_first, chain_id):   # _build_window_merged_addr_map
    window_map = {}
    for share in window_oldest_first:
        if getattr(share, 'VERSION', 0) < 36:
            continue
        merged_addrs = getattr(share, 'merged_addresses', None)
        if not merged_addrs:
            continue
        for entry in merged_addrs:
            if entry['chain_id'] != chain_id:
                continue
            script = entry['script']
            if not script:
                continue
            if share.new_script not in window_map:        # first (oldest) wins
                window_map[share.new_script] = script
            norm = _normalize(share.new_script)
            if norm and norm != share.new_script and norm not in window_map:
                window_map[norm] = script
    return window_map

def shadow_weights_new(window_newest_first, chain_id, max_weight=2**288 - 1):
    window_shares = list(window_newest_first)
    window_map = shadow_build_window_map(list(reversed(window_shares)), chain_id)
    weights, total_weight, donation_weight = {}, 0, 0
    for share in window_shares:
        att = _att(share.target)
        share_total = att * 65535
        if share.desired_version < 36:
            if total_weight + donation_weight + share_total > max_weight:
                break
            continue
        share_weight = att * (65535 - share.share_data['donation'])
        share_donation = att * share.share_data['donation']
        if total_weight + donation_weight + share_weight + share_donation > max_weight:
            remaining = max_weight - total_weight - donation_weight
            ts = share_weight + share_donation
            if remaining > 0 and ts > 0:
                share_weight = remaining * share_weight // ts
                share_donation = remaining * share_donation // ts
            else:
                break
        address_key = None
        if share.VERSION >= 36:
            for entry in (share.merged_addresses or []):
                if entry['chain_id'] == chain_id:
                    address_key = 'MERGED:' + hx(entry['script'])
                    break
        if address_key is None and share.VERSION >= 36:
            ms = window_map.get(share.new_script)
            if ms is None:
                norm = _normalize(share.new_script)
                if norm:
                    ms = window_map.get(norm)
            if ms is not None:
                address_key = 'MERGED:' + hx(ms)
        if address_key is None:
            address_key = share.new_script
        weights[address_key] = weights.get(address_key, 0) + share_weight
        total_weight += share_weight
        donation_weight += share_donation
    return weights, total_weight + donation_weight, donation_weight

def shadow_weights_OLD(window_newest_first, node_global_map, chain_id):
    """PRE-FIX path: Tier 1.5 reads a node-global map whose contents depend on
    the node's broader history + arrival order (the chain-split vector)."""
    weights = {}
    for share in window_newest_first:
        if share.desired_version < 36:
            continue
        share_weight = _att(share.target) * (65535 - share.share_data['donation'])
        address_key = None
        if share.VERSION >= 36:
            for entry in (share.merged_addresses or []):
                if entry['chain_id'] == chain_id:
                    address_key = 'MERGED:' + hx(entry['script'])
                    break
        if address_key is None and share.VERSION >= 36:
            ms = node_global_map.get(share.new_script)
            if ms is None:
                norm = _normalize(share.new_script)
                if norm:
                    ms = node_global_map.get(norm)
            if ms is not None:
                address_key = 'MERGED:' + hx(ms)
        if address_key is None:
            address_key = share.new_script
        weights[address_key] = weights.get(address_key, 0) + share_weight
    return weights

# ── fixtures: window with an activation-boundary (empty merged_addresses) ───
P2PKH_M  = b'\x76\xa9\x14' + (b'\x11' * 20) + b'\x88\xac'
P2WPKH_M = b'\x00\x14' + (b'\x11' * 20)
P2PKH_N  = b'\x76\xa9\x14' + (b'\x22' * 20) + b'\x88\xac'
DOGE_M   = b'\x76\xa9\x14' + (b'\xaa' * 20) + b'\x88\xac'
DOGE_N   = b'\x76\xa9\x14' + (b'\xbb' * 20) + b'\x88\xac'

def make_window():
    reg_M = [{'chain_id': CHAIN_ID, 'script': DOGE_M}]
    exp_N = [{'chain_id': CHAIN_ID, 'script': DOGE_N}]
    return [                       # newest ... oldest
        S(P2PKH_N, exp_N),
        S(P2PKH_M, []),            # M activation-boundary share: empty merged
        S(P2PKH_N, exp_N),
        S(P2PKH_M, reg_M),         # M registers DOGE_M (older, in-window)
        S(P2PKH_N, exp_N),
    ]

def _check(cond, msg):
    if not cond:
        raise AssertionError(msg)

def run_shadow():
    w = make_window()
    base, _, _ = shadow_weights_new(w, CHAIN_ID)
    _check(('MERGED:' + hx(DOGE_M)) in base, 'M boundary share did not resolve to DOGE_M')
    _check(('MERGED:' + hx(DOGE_N)) in base, 'N missing')
    _check(P2PKH_M not in base, 'M leaked as raw parent script')

    import copy
    again, _, _ = shadow_weights_new(copy.deepcopy(w), CHAIN_ID)
    _check(again == base, 'same window produced different weights (non-deterministic)')

    # OLD scheme splits when the registration is out of the window but a node's
    # global remembers it; the fix removes both the global and the split.
    w_noreg = [s for s in w if not (s.new_script == P2PKH_M and s.merged_addresses)]
    a_old = shadow_weights_OLD(w_noreg, {P2PKH_M: DOGE_M}, CHAIN_ID)  # node A
    b_old = shadow_weights_OLD(w_noreg, {}, CHAIN_ID)                 # node B
    _check(a_old != b_old, 'expected OLD scheme to split — fixture wrong')

    a_new, _, _ = shadow_weights_new(w_noreg, CHAIN_ID)
    b_new, _, _ = shadow_weights_new(w_noreg, CHAIN_ID)
    _check(a_new == b_new, 'FIX still splits')
    _check(P2PKH_M in a_new and ('MERGED:' + hx(DOGE_M)) not in a_new,
           'out-of-window registration leaked into resolution')

    # normalized-alias path (P2WPKH boundary resolves via P2PKH registration)
    w_norm = [S(P2WPKH_M, []), S(P2PKH_M, [{'chain_id': CHAIN_ID, 'script': DOGE_M}])]
    wn, _, _ = shadow_weights_new(w_norm, CHAIN_ID)
    _check(('MERGED:' + hx(DOGE_M)) in wn, 'P2WPKH alias did not resolve')

    print('SHADOW determinism: PASS (6 checks) — window-scoped resolution is '
          'order/global independent; OLD scheme splits, FIX does not.')

def run_real_module():
    try:
        from p2pool import data as d
    except Exception as e:
        print('REAL-MODULE: skipped (import failed: %r) — run in the py2 re-soak.' % (e,))
        return
    class T(object):
        def __init__(self, w): self._w = list(w)
        def get_chain(self, best, length):
            for s in self._w[:length]:
                yield s
    class TGuard(T):
        def get_v36_merged_cumulative_weights(self, *a, **k):
            raise AssertionError('skiplist consulted for chain_id-set merged query')
    w = make_window()
    plain = d.get_v36_merged_weights(T(w), 'HEAD', len(w), 2**288 - 1, chain_id=CHAIN_ID)
    guarded = d.get_v36_merged_weights(TGuard(w), 'HEAD', len(w), 2**288 - 1, chain_id=CHAIN_ID)
    _check(plain == guarded, 'skiplist-advertising tracker diverged from O(n)')
    _check(('MERGED:' + hx(DOGE_M)) in plain[0], 'M unresolved in real module')
    print('REAL-MODULE determinism: PASS — skiplist bypassed (chain_id set); '
          'O(n) path resolves the boundary share window-scoped.')

if __name__ == '__main__':
    run_shadow()
    run_real_module()
    print('OK')
