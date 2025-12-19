import os
import platform

from twisted.internet import defer

from .. import data, helper
from p2pool.util import pack


P2P_PREFIX = '0b110907'.decode('hex')  # Bitcoin testnet magic bytes
P2P_PORT = 18333
ADDRESS_VERSION = 111  # Bitcoin testnet address version (legacy P2PKH)
SCRIPT_ADDRESS_VERSION = 196  # Bitcoin testnet P2SH version
WITNESS_VERSION = 0  # SegWit v0 (P2WPKH, P2WSH)
TAPROOT_VERSION = 1  # SegWit v1 (P2TR - Taproot)
HRP = 'tb'  # Bech32/Bech32m HRP for testnet (Human Readable Part)
RPC_PORT = 18332
RPC_CHECK = defer.inlineCallbacks(lambda bitcoind: defer.returnValue(
            (yield bitcoind.rpc_getblockchaininfo())['chain'] == 'test'
        ))
BLOCKHASH_FUNC = lambda data: pack.IntType(256).unpack(__import__('hashlib').sha256(__import__('hashlib').sha256(data).digest()).digest())
POW_FUNC = lambda data: pack.IntType(256).unpack(__import__('hashlib').sha256(__import__('hashlib').sha256(data).digest()).digest())
BLOCK_PERIOD = 600 # s (Bitcoin block time)
SYMBOL = 'tBTC'
CONF_FILE_FUNC = lambda: os.path.join(os.path.join(os.environ['APPDATA'], 'Bitcoin') if platform.system() == 'Windows' else os.path.expanduser('~/Library/Application Support/Bitcoin/') if platform.system() == 'Darwin' else os.path.expanduser('~/.bitcoin'), 'bitcoin.conf')
BLOCK_EXPLORER_URL_PREFIX = 'https://blockstream.info/testnet/block/'
ADDRESS_EXPLORER_URL_PREFIX = 'https://blockstream.info/testnet/address/'
TX_EXPLORER_URL_PREFIX = 'https://blockstream.info/testnet/tx/'
# SANE_TARGET_RANGE for Bitcoin testnet
_DIFF1_TARGET = 0xFFFF * 2**208  # Standard difficulty 1 target
SANE_TARGET_RANGE = (_DIFF1_TARGET // 10000, _DIFF1_TARGET)  # Max diff 10000, min diff 1
DUST_THRESHOLD = 0.00001e8  # 1000 satoshis
DUMB_SCRYPT_DIFF = 1  # Bitcoin uses 1:1 difficulty
STRATUM_SHARE_RATE = 10  # Target seconds per pseudoshare

# Address type priority for miner payouts
# P2TR (Taproot) preferred for privacy and future features
SUPPORTED_ADDRESS_TYPES = ['p2tr', 'p2wpkh', 'p2wsh', 'p2sh', 'p2pkh']
PREFERRED_ADDRESS_TYPE = 'p2tr'  # Use Taproot by default for miner payouts

