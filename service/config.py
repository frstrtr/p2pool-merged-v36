"""
Configuration management for p2pool-service.

Loads config from YAML file with environment variable overrides.
"""

import os
import logging

log = logging.getLogger('p2pool-service.config')

try:
    import yaml
except ImportError:
    yaml = None


# Supported chains and their default RPC/explorer settings
CHAIN_DEFAULTS = {
    'ltc': {
        'name': 'Litecoin',
        'symbol': 'LTC',
        'explorer_base': 'https://chainz.cryptoid.info/ltc',
        'explorer_block': 'https://chainz.cryptoid.info/ltc/block.dws?',
        'explorer_address': 'https://chainz.cryptoid.info/ltc/address.dws?',
        'explorer_tx': 'https://chainz.cryptoid.info/ltc/tx.dws?',
        'rpc_port': 9332,
    },
    'doge': {
        'name': 'Dogecoin',
        'symbol': 'DOGE',
        'explorer_base': 'https://blockchair.com/dogecoin',
        'explorer_block': 'https://blockchair.com/dogecoin/block/',
        'explorer_address': 'https://blockchair.com/dogecoin/address/',
        'explorer_tx': 'https://blockchair.com/dogecoin/transaction/',
        'rpc_port': 22555,
    },
    'dgb': {
        'name': 'DigiByte',
        'symbol': 'DGB',
        'explorer_base': 'https://digiexplorer.info',
        'explorer_block': 'https://digiexplorer.info/block/',
        'explorer_address': 'https://digiexplorer.info/address/',
        'explorer_tx': 'https://digiexplorer.info/tx/',
        'rpc_port': 14022,
    },
    'btc': {
        'name': 'Bitcoin',
        'symbol': 'BTC',
        'explorer_base': 'https://blockchain.info',
        'explorer_block': 'https://blockchain.info/block/',
        'explorer_address': 'https://blockchain.info/address/',
        'explorer_tx': 'https://blockchain.info/tx/',
        'rpc_port': 8332,
    },
    'bch': {
        'name': 'Bitcoin Cash',
        'symbol': 'BCH',
        'explorer_base': 'https://explorer.bitcoin.com/bch',
        'explorer_block': 'https://explorer.bitcoin.com/bch/block/',
        'explorer_address': 'https://explorer.bitcoin.com/bch/address/',
        'explorer_tx': 'https://explorer.bitcoin.com/bch/tx/',
        'rpc_port': 8332,
    },
    'bsv': {
        'name': 'Bitcoin SV',
        'symbol': 'BSV',
        'explorer_base': 'https://blockchair.com/bitcoin-sv',
        'explorer_block': 'https://blockchair.com/bitcoin-sv/block/',
        'explorer_address': 'https://blockchair.com/bitcoin-sv/address/',
        'explorer_tx': 'https://blockchair.com/bitcoin-sv/transaction/',
        'rpc_port': 8332,
    },
}


DEFAULT_CONFIG = {
    'server': {
        'host': '0.0.0.0',
        'port': 8920,
        'cors_origins': ['*'],
    },
    'registry': {
        'enabled': True,
        'node_ttl': 600,        # Seconds before a node is considered stale
        'announce_interval': 300,  # Recommended re-announce interval
        'max_nodes_per_chain': 500,
        'require_api_key': False,
    },
    'explorer': {
        'enabled': True,
        'cache_ttl_block': 86400,   # Immutable blocks: 24h cache
        'cache_ttl_tx': 86400,      # Confirmed txs: 24h cache
        'cache_ttl_address': 60,    # Addresses: 1min (balance changes)
        'cache_max_items': 10000,
        'upstream_timeout': 15,
        'rate_limit_per_min': 120,  # Upstream API rate limit budget
        'blockchair_api_key': '',   # Optional blockchair.com API key
    },
    'rpc_proxy': {
        'enabled': True,
        'require_api_key': True,
        'rate_limit_per_min': 60,
        'daemons': {},  # chain -> {host, port, user, password}
    },
    'api_keys': [],  # List of valid API keys (empty = no auth)
    'chains': list(CHAIN_DEFAULTS.keys()),
}


def load_config(path=None):
    """Load configuration from YAML file, with env var overrides."""
    config = dict(DEFAULT_CONFIG)

    # Load from file
    if path and os.path.exists(path):
        if yaml is None:
            log.warning('PyYAML not installed, using default config')
        else:
            with open(path, 'r') as f:
                file_config = yaml.safe_load(f) or {}
            _deep_merge(config, file_config)
            log.info('Loaded config from %s', path)

    # Environment variable overrides
    env_map = {
        'P2POOL_SERVICE_HOST': ('server', 'host'),
        'P2POOL_SERVICE_PORT': ('server', 'port'),
        'P2POOL_REGISTRY_TTL': ('registry', 'node_ttl'),
        'P2POOL_EXPLORER_CACHE_TTL': ('explorer', 'cache_ttl_block'),
        'P2POOL_RPC_REQUIRE_KEY': ('rpc_proxy', 'require_api_key'),
    }
    for env_key, config_path in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            _set_nested(config, config_path, _auto_type(val))
            log.info('Override from env: %s', env_key)

    # API keys from env (comma-separated)
    api_keys_env = os.environ.get('P2POOL_API_KEYS')
    if api_keys_env:
        config['api_keys'] = [k.strip() for k in api_keys_env.split(',') if k.strip()]

    return config


def _deep_merge(base, override):
    """Recursively merge override dict into base dict."""
    for key, val in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(val, dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val


def _set_nested(d, keys, value):
    """Set a value in a nested dict by key path tuple."""
    for key in keys[:-1]:
        d = d.setdefault(key, {})
    d[keys[-1]] = value


def _auto_type(val):
    """Convert string env values to appropriate Python types."""
    if val.lower() in ('true', 'yes', '1'):
        return True
    if val.lower() in ('false', 'no', '0'):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val
