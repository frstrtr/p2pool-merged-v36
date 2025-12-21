from p2pool.litecoin import networks

PARENT = networks.nets['litecoin_testnet']
SHARE_PERIOD = 15  # seconds (faster than Litecoin's 2.5 min blocks)
CHAIN_LENGTH = 24*60*60//15  # 24 hours worth of shares
REAL_CHAIN_LENGTH = 24*60*60//15
TARGET_LOOKBEHIND = 100  # shares
SPREAD = 10  # blocks
IDENTIFIER = 'e037d5b8c6923410'.decode('hex')  # Unique identifier for this p2pool network
PREFIX = '7208c1a53ef629b0'.decode('hex')  # Share chain prefix
COINBASEEXT = '0D2F5032506F6F6C2D744C54432F'.decode('hex')  # "/P2Pool-tLTC/" in hex
P2P_PORT = 19327  # P2Pool share chain port for Litecoin testnet
MIN_TARGET = 0
MAX_TARGET = 2**256//2**20 - 1
PERSIST = False  # Don't persist shares for testnet
WORKER_PORT = 19332  # Stratum port for miners (different from RPC)
BOOTSTRAP_ADDRS = ''.split(' ')  # No bootstrap for testnet initially
ANNOUNCE_CHANNEL = ''
VERSION_CHECK = lambda v: True  # Accept all versions for testnet
MINIMUM_PROTOCOL_VERSION = 1700

# Stratum Vardiff Configuration
STRATUM_SHARE_RATE = 10  # Target 10 seconds per pseudoshare

# Vardiff settings
VARDIFF_SHARES_TRIGGER = 5
VARDIFF_TIMEOUT_MULT = 3
VARDIFF_QUICKUP_SHARES = 2
VARDIFF_QUICKUP_DIVISOR = 4
VARDIFF_MIN_ADJUST = 0.25
VARDIFF_MAX_ADJUST = 4.0

# Connection threat detection  
CONNECTION_WORKER_ELEVATED = 4.0
CONNECTION_WORKER_WARNING = 6.0
