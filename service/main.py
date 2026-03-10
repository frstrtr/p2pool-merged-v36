#!/usr/bin/env python3
"""
P2Pool Service — Node Registry, Explorer Proxy & RPC Provider

A unified API service for the p2pool ecosystem:
  - Node Registry:  Dynamic peer discovery for p2pool nodes
  - Explorer Proxy: Cached multi-chain block explorer API
  - RPC Proxy:      Authenticated JSON-RPC relay to hosted daemons

Architecture:
    p2pool node ──► p2pool-service (GCP) ──┬──► Registry (in-memory)
                                            ├──► Explorer upstream APIs
                                            └──► Blockchain daemon RPCs

Deploy on GCP Cloud Run / App Engine behind a global HTTPS load balancer.

Usage:
    python3 -m service.main [--config config.yaml] [--port 8920]
    python3 service/main.py [--config config.yaml] [--port 8920]

Environment variables:
    P2POOL_SERVICE_HOST     Override bind host
    P2POOL_SERVICE_PORT     Override bind port
    P2POOL_API_KEYS         Comma-separated API keys
    P2POOL_RPC_REQUIRE_KEY  Require API key for RPC (true/false)
"""

import argparse
import asyncio
import logging
import os
import sys

from aiohttp import web

# Handle both module and direct execution
try:
    from .config import load_config
    from . import registry as registry_mod
    from . import explorer as explorer_mod
    from . import rpc_proxy as rpc_proxy_mod
except ImportError:
    from config import load_config
    import registry as registry_mod
    import explorer as explorer_mod
    import rpc_proxy as rpc_proxy_mod


# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger('p2pool-service')
logging.getLogger('aiohttp.access').setLevel(logging.WARNING)


class P2PoolService:
    """Main service orchestrator."""

    def __init__(self, config):
        self.config = config
        self.app = web.Application()
        self.api_keys = set(config.get('api_keys', []))

        # Sub-components
        self.registry = registry_mod.NodeRegistry(config)
        self.explorer = explorer_mod.ExplorerProxy(config)
        self.rpc_proxy = rpc_proxy_mod.RPCProxy(config)

        # Register routes
        self._setup_core_routes()
        if config.get('registry', {}).get('enabled', True):
            registry_mod.setup_routes(self.app, self.registry, self._auth_check)
            log.info('Registry module enabled')
        if config.get('explorer', {}).get('enabled', True):
            explorer_mod.setup_routes(self.app, self.explorer, self._auth_check)
            log.info('Explorer module enabled')
        if config.get('rpc_proxy', {}).get('enabled', True):
            rpc_proxy_mod.setup_routes(self.app, self.rpc_proxy, self._auth_check)
            log.info('RPC proxy module enabled')

        # Lifecycle hooks
        self.app.on_startup.append(self._on_startup)
        self.app.on_cleanup.append(self._on_cleanup)

    def _auth_check(self, request):
        """Validate API key from header or query param."""
        if not self.api_keys:
            return True  # No keys configured = open access
        api_key = request.headers.get('X-API-Key', '')
        if not api_key:
            api_key = request.query.get('api_key', '')
        # Strip write-tier suffix for validation
        base_key = api_key.rstrip(':w') if api_key.endswith(':w') else api_key
        return base_key in self.api_keys

    def _setup_core_routes(self):
        """Health, info, and root endpoints."""

        async def handle_root(request):
            return web.json_response({
                'service': 'p2pool-service',
                'version': '1.0.0',
                'modules': {
                    'registry': self.config.get('registry', {}).get('enabled', True),
                    'explorer': self.config.get('explorer', {}).get('enabled', True),
                    'rpc_proxy': self.config.get('rpc_proxy', {}).get('enabled', True),
                },
                'endpoints': {
                    'registry': [
                        'POST /registry/announce',
                        'GET  /registry/nodes',
                        'GET  /registry/nodes/{chain}',
                        'GET  /registry/stats',
                    ],
                    'explorer': [
                        'GET /explorer/chains',
                        'GET /explorer/{chain}/block/{id}',
                        'GET /explorer/{chain}/address/{addr}',
                        'GET /explorer/{chain}/tx/{txid}',
                        'GET /explorer/cache',
                    ],
                    'rpc': [
                        'POST /rpc/{chain}',
                        'GET  /rpc/{chain}/status',
                        'GET  /rpc/chains',
                    ],
                },
            }, headers={'Access-Control-Allow-Origin': '*'})

        async def handle_health(request):
            """GCP health check endpoint."""
            return web.json_response({
                'status': 'healthy',
                'registry_nodes': self.registry.get_stats()['active_nodes'],
                'explorer_cache': self.explorer.cache.size,
                'rpc_chains': len(self.rpc_proxy.daemons),
            })

        self.app.router.add_get('/', handle_root)
        self.app.router.add_get('/health', handle_health)
        self.app.router.add_get('/_ah/health', handle_health)  # GCP App Engine

    async def _on_startup(self, app):
        """Initialize async components."""
        await self.explorer.start()
        await self.rpc_proxy.start()
        # Start periodic registry pruning
        self._prune_task = asyncio.ensure_future(self._prune_loop())
        log.info('P2Pool Service started')

    async def _on_cleanup(self, app):
        """Clean shutdown."""
        self._prune_task.cancel()
        await self.explorer.stop()
        await self.rpc_proxy.stop()
        log.info('P2Pool Service stopped')

    async def _prune_loop(self):
        """Periodically prune stale nodes from registry."""
        while True:
            try:
                await asyncio.sleep(120)
                self.registry.prune()
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error('Prune error: %s', e)

    def run(self, host=None, port=None):
        """Start the service."""
        host = host or self.config.get('server', {}).get('host', '0.0.0.0')
        port = port or self.config.get('server', {}).get('port', 8920)
        log.info('Starting on %s:%s', host, port)
        web.run_app(self.app, host=host, port=port, print=None)


def main():
    parser = argparse.ArgumentParser(description='P2Pool Service')
    parser.add_argument('--config', '-c', default='service/config.yaml',
                        help='Path to YAML config file')
    parser.add_argument('--port', '-p', type=int, default=None,
                        help='Override listen port')
    parser.add_argument('--host', default=None,
                        help='Override bind address')
    args = parser.parse_args()

    config = load_config(args.config)
    service = P2PoolService(config)
    service.run(host=args.host, port=args.port)


if __name__ == '__main__':
    main()
