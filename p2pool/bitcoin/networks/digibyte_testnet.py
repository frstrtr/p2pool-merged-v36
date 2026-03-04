import os
import platform

from twisted.internet import defer

from .. import data, helper
from p2pool.util import pack


# DigiByte testnet (Scrypt algo) network parameters
# Reference: https://github.com/DigiByte-Core/digibyte/blob/develop/src/kernel/chainparams.cpp
# Testnet magic: fd c8 bd dd, P2P port 12026, RPC port 14025

P2P_PREFIX = 'fdc8bddd'.decode('hex')  # DigiByte testnet magic bytes
P2P_PORT = 12026
P2P_VERSION = 70019  # DGB Core protocol version
ADDRESS_VERSION = 126  # 0x7e - testnet addresses
ADDRESS_P2SH_VERSION = 140  # 0x8c - testnet P2SH addresses
HUMAN_READABLE_PART = 'dgbt'  # bech32 HRP for testnet segwit
RPC_PORT = 14025
RPC_CHECK = defer.inlineCallbacks(lambda bitcoind: defer.returnValue(
            (yield helper.check_block_header(bitcoind, '308ea0711d5763be2995670dd9ca9872753561285a84da1d58be58acaa822252')) and
            (yield bitcoind.rpc_getblockchaininfo())['chain'] == 'test'
        ))

# Import mainnet subsidy function (same formula)
from digibyte import _dgb_subsidy
SUBSIDY_FUNC = _dgb_subsidy

# DigiByte Scrypt PoW (identical to Litecoin)
POW_FUNC = lambda data: pack.IntType(256).unpack(__import__('ltc_scrypt').getPoWHash(data))
BLOCKHASH_FUNC = POW_FUNC

BLOCK_PERIOD = 15  # 15 seconds
SYMBOL = 'tDGB'
CONF_FILE_FUNC = lambda: os.path.join(
    os.path.join(os.environ['APPDATA'], 'DigiByte') if platform.system() == 'Windows'
    else os.path.expanduser('~/Library/Application Support/DigiByte/') if platform.system() == 'Darwin'
    else os.path.expanduser('~/.digibyte'),
    'digibyte.conf'
)
BLOCK_EXPLORER_URL_PREFIX = 'https://digiexplorer.info/block/'
ADDRESS_EXPLORER_URL_PREFIX = 'https://digiexplorer.info/address/'
TX_EXPLORER_URL_PREFIX = 'https://digiexplorer.info/tx/'

SANE_TARGET_RANGE = (2**256//10**17 - 1, 2**256//2**20 - 1)
DUMB_SCRYPT_DIFF = 2**16
DUST_THRESHOLD = 0.03e8

SOFTFORKS_REQUIRED = set(['csv', 'segwit'])
