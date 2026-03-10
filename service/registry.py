"""
P2Pool Node Registry

Nodes announce themselves periodically. Stale nodes are pruned.
Provides peer discovery for p2pool nodes across all supported chains.

Endpoints:
    POST /registry/announce   — Node announces itself
    GET  /registry/nodes      — List all known nodes (optionally filter by chain)
    GET  /registry/nodes/{chain} — Nodes for a specific chain
    GET  /registry/stats      — Registry summary statistics
"""

import hashlib
import hmac
import logging
import time
from collections import defaultdict

from aiohttp import web

log = logging.getLogger('p2pool-service.registry')


class NodeRegistry:
    """In-memory node registry with TTL-based expiry."""

    def __init__(self, config):
        self.config = config.get('registry', {})
        self.node_ttl = self.config.get('node_ttl', 600)
        self.max_per_chain = self.config.get('max_nodes_per_chain', 500)
        # nodes[chain][node_id] = node_info
        self.nodes = defaultdict(dict)

    def _node_id(self, chain, host, port):
        """Deterministic node ID from chain + address."""
        raw = '%s:%s:%s' % (chain, host, port)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def announce(self, chain, host, p2pool_port, web_port=None,
                 version=None, protocol_version=None, hashrate=None,
                 miners=None, uptime=None, merged_chains=None):
        """Register or refresh a node."""
        node_id = self._node_id(chain, host, p2pool_port)
        now = time.time()

        existing = self.nodes[chain].get(node_id)
        first_seen = existing['first_seen'] if existing else now

        self.nodes[chain][node_id] = {
            'node_id': node_id,
            'chain': chain,
            'host': host,
            'p2pool_port': int(p2pool_port),
            'web_port': int(web_port) if web_port else None,
            'version': version,
            'protocol_version': protocol_version,
            'hashrate': hashrate,
            'miners': miners,
            'uptime': uptime,
            'merged_chains': merged_chains or [],
            'first_seen': first_seen,
            'last_seen': now,
            'announce_count': (existing['announce_count'] + 1) if existing else 1,
        }

        # Enforce per-chain limit (drop oldest)
        if len(self.nodes[chain]) > self.max_per_chain:
            self._prune_oldest(chain)

        log.info('Node announced: %s %s:%s (id=%s)', chain, host, p2pool_port, node_id)
        return node_id

    def get_nodes(self, chain=None, include_stale=False):
        """Return list of nodes, optionally filtered by chain."""
        now = time.time()
        cutoff = now - self.node_ttl
        result = []

        chains = [chain] if chain else list(self.nodes.keys())
        for c in chains:
            for node_id, info in list(self.nodes.get(c, {}).items()):
                if not include_stale and info['last_seen'] < cutoff:
                    continue
                entry = dict(info)
                entry['stale'] = info['last_seen'] < cutoff
                entry['age'] = int(now - info['last_seen'])
                result.append(entry)

        result.sort(key=lambda n: n['last_seen'], reverse=True)
        return result

    def get_stats(self):
        """Return registry summary."""
        now = time.time()
        cutoff = now - self.node_ttl

        stats = {
            'total_nodes': 0,
            'active_nodes': 0,
            'chains': {},
        }
        for chain, nodes in self.nodes.items():
            active = sum(1 for n in nodes.values() if n['last_seen'] >= cutoff)
            total = len(nodes)
            stats['chains'][chain] = {
                'total': total,
                'active': active,
            }
            stats['total_nodes'] += total
            stats['active_nodes'] += active

        stats['node_ttl'] = self.node_ttl
        stats['announce_interval'] = self.config.get('announce_interval', 300)
        return stats

    def prune(self):
        """Remove all stale nodes (called periodically)."""
        now = time.time()
        cutoff = now - (self.node_ttl * 3)  # Keep 3x TTL before hard removal
        removed = 0
        for chain in list(self.nodes.keys()):
            for node_id in list(self.nodes[chain].keys()):
                if self.nodes[chain][node_id]['last_seen'] < cutoff:
                    del self.nodes[chain][node_id]
                    removed += 1
            if not self.nodes[chain]:
                del self.nodes[chain]
        if removed:
            log.info('Pruned %d stale nodes', removed)
        return removed

    def _prune_oldest(self, chain):
        """Drop oldest nodes when chain exceeds max capacity."""
        nodes = self.nodes[chain]
        by_age = sorted(nodes.items(), key=lambda kv: kv[1]['last_seen'])
        while len(nodes) > self.max_per_chain:
            oldest_id = by_age.pop(0)[0]
            del nodes[oldest_id]


def setup_routes(app, registry, auth_check):
    """Register registry HTTP routes."""

    async def handle_announce(request):
        """POST /registry/announce — Node announces itself."""
        if registry.config.get('require_api_key') and not auth_check(request):
            return web.json_response({'error': 'unauthorized'}, status=401)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({'error': 'invalid JSON body'}, status=400)

        chain = data.get('chain', '').lower().strip()
        host = data.get('host', '').strip()
        p2pool_port = data.get('p2pool_port')

        if not chain or not host or not p2pool_port:
            return web.json_response(
                {'error': 'required fields: chain, host, p2pool_port'},
                status=400
            )

        # Basic input validation
        if len(chain) > 20 or len(host) > 255:
            return web.json_response({'error': 'field too long'}, status=400)
        try:
            p2pool_port = int(p2pool_port)
            if not (1 <= p2pool_port <= 65535):
                raise ValueError
        except (ValueError, TypeError):
            return web.json_response({'error': 'invalid port'}, status=400)

        node_id = registry.announce(
            chain=chain,
            host=host,
            p2pool_port=p2pool_port,
            web_port=data.get('web_port'),
            version=str(data.get('version', ''))[:64],
            protocol_version=data.get('protocol_version'),
            hashrate=data.get('hashrate'),
            miners=data.get('miners'),
            uptime=data.get('uptime'),
            merged_chains=data.get('merged_chains'),
        )

        return web.json_response({
            'ok': True,
            'node_id': node_id,
            'ttl': registry.node_ttl,
            'next_announce': registry.config.get('announce_interval', 300),
        })

    async def handle_nodes(request):
        """GET /registry/nodes[/{chain}] — List known nodes."""
        chain = request.match_info.get('chain')
        include_stale = request.query.get('include_stale', '').lower() in ('1', 'true', 'yes')
        nodes = registry.get_nodes(chain=chain, include_stale=include_stale)
        return web.json_response({
            'nodes': nodes,
            'count': len(nodes),
        }, headers={'Access-Control-Allow-Origin': '*'})

    async def handle_stats(request):
        """GET /registry/stats — Registry summary."""
        stats = registry.get_stats()
        return web.json_response(stats, headers={'Access-Control-Allow-Origin': '*'})

    app.router.add_post('/registry/announce', handle_announce)
    app.router.add_get('/registry/nodes', handle_nodes)
    app.router.add_get('/registry/nodes/{chain}', handle_nodes)
    app.router.add_get('/registry/stats', handle_stats)
