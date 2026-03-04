from p2pool.bitcoin import networks

# DigiByte Scrypt p2pool network configuration (mainnet)
# Based on farsider350/p2pool-dgb-scrypt-350 with v36 merged mining extensions

PARENT = networks.nets['digibyte']
SHARE_PERIOD = 25  # seconds target between shares
CHAIN_LENGTH = 24*60*60//10  # shares (8640 = ~24 hours of shares)
REAL_CHAIN_LENGTH = 24*60*60//10  # shares
TARGET_LOOKBEHIND = 200  # shares
SPREAD = 30  # blocks
# Network identifiers from farsider350 (established DGB scrypt p2pool network)
IDENTIFIER = '1bfe01eff5ba4e38'.decode('hex')
PREFIX = '1bfe01eff652e4b7'.decode('hex')
P2P_PORT = 5024
MIN_TARGET = 0
MAX_TARGET = 2**256//2**20 - 1
PERSIST = False  # Bootstrap mode: starting fresh canonical sharechain
WORKER_PORT = 5025
BOOTSTRAP_ADDRS = [
        # Bootstrapping our own sharechain - no external peers yet
]
ANNOUNCE_CHANNEL = '#p2pool'
VERSION_CHECK = lambda v: None if 82200 <= v else 'DigiByte version too old. Upgrade to 8.22 or newer!'
VERSION_WARNING = lambda v: None
SOFTFORKS_REQUIRED = set(['csv', 'segwit'])
MINIMUM_PROTOCOL_VERSION = 3301
SEGWIT_ACTIVATION_VERSION = 17
BLOCK_MAX_SIZE = 1000000
BLOCK_MAX_WEIGHT = 4000000
IMMUTABLE_BLOCKS = False
# DigiByte is a multi-algo coin; getblocktemplate requires specifying the algorithm
GBT_ALGO = 'scrypt'
