# -*- coding: utf-8 -*-
"""Pure operator merged-address helpers, shared by main.py (startup banner +
reverse auto-conversion) and unit tests.

These are deliberately dependency-light (only p2pool.bitcoin.data and the
pubkey-type constants) so the merged/DOGE node-operator-fee behaviour can be
exercised under clean CPython 2.7 without importing twisted / stratum / the
reactor. The runtime resolver that COMMITS these choices into a share lives on
WorkerBridge (work.py, _resolve_operator_merged_addresses); the functions here
mirror the same cascade for the boot banner and the startup reverse-conversion
so the three can never disagree.
"""
from p2pool.bitcoin import data as bitcoin_data


def reverse_convert_operator_address(merged_operator_address, doge_net, parent_net):
    """Reverse (merged -> main) operator-address auto-conversion.

    When the main (parent, e.g. LTC) operator identity is unresolved but
    --merged-operator-address validates on the merged (DOGE) net, derive the
    parent-chain pubkey_hash / address from it, hash-preserving, mapping
    DOGE-P2SH -> parent-P2SH and DOGE-P2PKH -> parent-P2PKH. Startup mirror of
    work.py miner Case 4 (invalid parent + valid explicit DOGE).

    Returns (pubkey_hash, parent_address, pubkey_type) or None if not derivable.
    """
    from p2pool.data import PUBKEY_TYPE_P2SH, PUBKEY_TYPE_P2PKH
    if not merged_operator_address or doge_net is None:
        return None
    try:
        ph, version, witver = bitcoin_data.address_to_pubkey_hash(merged_operator_address, doge_net)
    except Exception:
        return None
    doge_p2sh_version = getattr(doge_net, 'ADDRESS_P2SH_VERSION', None)
    if doge_p2sh_version is not None and version == doge_p2sh_version:
        parent_addr = bitcoin_data.pubkey_hash_to_address(ph, parent_net.ADDRESS_P2SH_VERSION, -1, parent_net)
        return ph, parent_addr, PUBKEY_TYPE_P2SH
    parent_addr = bitcoin_data.pubkey_hash_to_address(ph, parent_net.ADDRESS_VERSION, -1, parent_net)
    return ph, parent_addr, PUBKEY_TYPE_P2PKH


def operator_merged_display_address(merged_operator_address, my_pubkey_hash, my_pubkey_type, doge_net, parent_net=None):
    """Compute the operator's merged (DOGE) payout address for the [CHECK 3]
    startup banner using the SAME cascade the runtime resolver
    (WorkerBridge._resolve_operator_merged_addresses) applies:

      P1. --merged-operator-address set AND valid on the DOGE net -> show it.
      P2. else auto-convert the operator's own parent key (my_pubkey_hash),
          honouring P2SH vs P2PKH.

    Returns (address_str_or_None, source_label). An explicit address that is
    INVALID on the DOGE net falls through to P2 -- exactly what the resolver
    will commit -- so the banner never claims an address the share won't pay.
    """
    from p2pool.data import PUBKEY_TYPE_P2SH
    # P1
    if merged_operator_address and doge_net is not None:
        try:
            bitcoin_data.address_to_pubkey_hash(merged_operator_address, doge_net)
            return merged_operator_address, 'explicit via --merged-operator-address'
        except Exception:
            pass  # invalid on DOGE net -> fall to auto-conversion (matches resolver P2)
    # P2
    if doge_net is not None and my_pubkey_hash is not None:
        if my_pubkey_type == PUBKEY_TYPE_P2SH:
            return bitcoin_data.pubkey_hash_to_address(my_pubkey_hash, doge_net.ADDRESS_P2SH_VERSION, -1, doge_net), 'auto-converted from operator parent key'
        return bitcoin_data.pubkey_hash_to_address(my_pubkey_hash, doge_net.ADDRESS_VERSION, -1, doge_net), 'auto-converted from operator parent key'
    return None, 'unresolved (dogecoin network not loaded)'
