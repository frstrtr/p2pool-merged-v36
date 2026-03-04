#!/usr/bin/env python3
"""
P2Pool Multi-Pool Reverse Proxy Dashboard

A lightweight aiohttp-based reverse proxy that aggregates multiple p2pool
instances behind a single web interface with chain-selector tabs.

Architecture:
    Browser ──► multipool_proxy (port 8080) ──┬──► p2pool LTC (port 9327)
                                               └──► p2pool DGB (port 5025)

All requests to /api/<chain>/* are proxied to the corresponding backend.
Static dashboard files are served directly with pools auto-configured.

Usage:
    python3 multipool_proxy.py [--config config.yaml] [--port 8080]

Requirements:
    pip3 install aiohttp pyyaml
"""

import argparse
import asyncio
import json
import os
import sys

import aiohttp
from aiohttp import web

try:
    import yaml
except ImportError:
    yaml = None


DEFAULT_CONFIG = {
    'server': {
        'host': '0.0.0.0',
        'port': 8080
    },
    'pools': {
        'dgb': {
            'name': 'DigiByte',
            'symbol': 'DGB',
            'url': 'http://127.0.0.1:5025',
            'color': '#0066cc'
        },
        'ltc': {
            'name': 'Litecoin',
            'symbol': 'LTC',
            'url': 'http://127.0.0.1:9327',
            'color': '#345d9d'
        }
    }
}


class MultiPoolProxy:
    def __init__(self, config):
        self.config = config
        self.pools = config.get('pools', {})
        self.session = None

    async def start(self):
        """Create shared HTTP session for proxying."""
        timeout = aiohttp.ClientTimeout(total=15)
        self.session = aiohttp.ClientSession(timeout=timeout)

    async def stop(self):
        """Clean up HTTP session."""
        if self.session:
            await self.session.close()

    # ── Pool discovery endpoint ───────────────────────────────────────────
    async def handle_pools(self, request):
        """Return list of configured pools for the frontend."""
        pools_list = []
        for pool_id, pool_cfg in self.pools.items():
            pools_list.append({
                'id': pool_id,
                'name': pool_cfg.get('name', pool_cfg.get('symbol', pool_id)),
                'symbol': pool_cfg.get('symbol', pool_id.upper()),
                'color': pool_cfg.get('color', '#008de4'),
                'url': '/api/' + pool_id,  # Relative proxy URL
                'direct_url': pool_cfg.get('url', ''),
            })
        return web.json_response(pools_list, headers={
            'Access-Control-Allow-Origin': '*'
        })

    # ── Core proxy logic ──────────────────────────────────────────────────
    async def _do_proxy(self, request, pool_id, path):
        """Forward a request to the specified pool backend."""
        if pool_id not in self.pools:
            return web.json_response(
                {'error': 'Unknown pool: %s' % pool_id},
                status=404
            )

        backend_url = self.pools[pool_id]['url'].rstrip('/')
        target_url = '%s/%s' % (backend_url, path)

        # Forward query string
        if request.query_string:
            target_url += '?' + request.query_string

        try:
            async with self.session.request(
                method=request.method,
                url=target_url,
                headers={
                    k: v for k, v in request.headers.items()
                    if k.lower() not in ('host', 'connection')
                },
                data=await request.read() if request.can_read_body else None,
            ) as resp:
                body = await resp.read()
                headers = dict(resp.headers)
                headers['Access-Control-Allow-Origin'] = '*'
                for h in ('Transfer-Encoding', 'Content-Encoding', 'Connection'):
                    headers.pop(h, None)

                return web.Response(
                    body=body,
                    status=resp.status,
                    headers=headers
                )
        except aiohttp.ClientError as e:
            return web.json_response(
                {'error': 'Backend unreachable: %s (%s)' % (pool_id, str(e))},
                status=502,
                headers={'Access-Control-Allow-Origin': '*'}
            )

    # ── Proxy handler ─────────────────────────────────────────────────────
    async def handle_proxy(self, request):
        """Proxy /api/<chain>/<path> to the corresponding p2pool backend."""
        pool_id = request.match_info['pool']
        path = request.match_info.get('path', '')
        return await self._do_proxy(request, pool_id, path)

    # ── Default pool fallback ─────────────────────────────────────────────
    async def handle_default_proxy(self, request):
        """Proxy unmatched paths to the first configured pool (fallback)."""
        path = request.path.lstrip('/')
        first_pool_id = next(iter(self.pools), None)
        if not first_pool_id:
            return web.json_response({'error': 'No pools configured'}, status=500)
        return await self._do_proxy(request, first_pool_id, path)

    # ── Health check ──────────────────────────────────────────────────────
    async def handle_health(self, request):
        """Health check with per-pool backend status."""
        results = {}
        for pool_id, pool_cfg in self.pools.items():
            try:
                url = pool_cfg['url'].rstrip('/') + '/web/currency_info'
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        info = await resp.json()
                        results[pool_id] = {
                            'status': 'ok',
                            'symbol': info.get('symbol', '?'),
                            'name': info.get('name', '?')
                        }
                    else:
                        results[pool_id] = {'status': 'error', 'http_code': resp.status}
            except Exception as e:
                results[pool_id] = {'status': 'unreachable', 'error': str(e)}

        return web.json_response({
            'status': 'ok',
            'pools': results
        }, headers={'Access-Control-Allow-Origin': '*'})


def create_app(config):
    """Build the aiohttp web application."""
    proxy = MultiPoolProxy(config)
    app = web.Application()

    # Startup / shutdown hooks
    app.on_startup.append(lambda _: proxy.start())
    app.on_shutdown.append(lambda _: proxy.stop())

    # API routes
    app.router.add_get('/api/pools', proxy.handle_pools)
    app.router.add_get('/api/health', proxy.handle_health)
    app.router.add_route('*', '/api/{pool}/{path:.*}', proxy.handle_proxy)

    # Serve static dashboard files from web-static/
    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'web-static')
    if os.path.isdir(static_dir):
        # Serve index at root
        async def handle_root(request):
            raise web.HTTPFound('/static/dashboard.html')
        app.router.add_get('/', handle_root)
        app.router.add_static('/static/', static_dir, show_index=True)

    # Fallback: proxy any unmatched request to the first (default) pool.
    # This allows the dashboard to work before multipool.js sets up API routing.
    app.router.add_route('*', '/{path:.*}', proxy.handle_default_proxy)

    return app


def load_config(config_path):
    """Load config from YAML file or return defaults."""
    if config_path and os.path.exists(config_path):
        if yaml is None:
            print('WARNING: pyyaml not installed, using default config')
            return DEFAULT_CONFIG
        with open(config_path) as f:
            return yaml.safe_load(f)
    return DEFAULT_CONFIG


def main():
    parser = argparse.ArgumentParser(description='P2Pool Multi-Pool Dashboard Proxy')
    parser.add_argument('--config', '-c', default='config.yaml',
                        help='Path to config YAML (default: config.yaml)')
    parser.add_argument('--port', '-p', type=int, default=None,
                        help='Override listen port')
    parser.add_argument('--host', type=str, default=None,
                        help='Override listen address')
    args = parser.parse_args()

    # Try the path as-is first (absolute or CWD-relative), then relative to script dir
    if os.path.isabs(args.config) or os.path.exists(args.config):
        config_path = args.config
    else:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), args.config)
    config = load_config(config_path)

    if args.port:
        config.setdefault('server', {})['port'] = args.port
    if args.host:
        config.setdefault('server', {})['host'] = args.host

    host = config.get('server', {}).get('host', '0.0.0.0')
    port = config.get('server', {}).get('port', 8080)

    print('=' * 60)
    print('  P2Pool Multi-Pool Dashboard Proxy')
    print('=' * 60)
    print('  Listen: %s:%d' % (host, port))
    print('  Pools:')
    for pid, pcfg in config.get('pools', {}).items():
        print('    [%s] %s → %s' % (pid.upper(), pcfg.get('name', pid), pcfg.get('url', '?')))
    print('=' * 60)
    print()

    app = create_app(config)
    web.run_app(app, host=host, port=port, print=lambda msg: print(msg))


if __name__ == '__main__':
    main()
