"""
T4 stratum-swarm simulator (DOA-under-load harness) -- p2pool v35 / c2pool v36.

Opens N *distinct* stratum connections at share-difficulty with NO hashrate, all
pointed at a single p2pool (or c2pool) stratum endpoint. Instruments the two
signals that matter for the G1 DOA fix:

  1. reactor stall  -- wall time the reactor spends inside a single get_work
                       response (proxy: subscribe->first-notify latency, and
                       inter-notify gap under load). G1 target: never > ~50 ms.
  2. DOA%           -- of shares the swarm submits (replayed from a share-diff
                       template), fraction the node tags stale/DOA.

This is the transition-scenario proof 3 physical rigs cannot produce: hundreds
of connections x uncapped-template width. Methodology: v36-master #41.

Python2 / Twisted (reactor) -- matches the fork runtime. NOT YET WIRED to a live
node; run modes are scaffolded below. This is the T4 skeleton commit.
"""
import json, time
from twisted.internet import reactor, defer, protocol
from twisted.internet.endpoints import TCP4ClientEndpoint
from twisted.protocols import basic


class StratumConn(basic.LineReceiver):
    delimiter = '\n'

    def __init__(self, cid, stats):
        self.cid = cid
        self.stats = stats
        self._id = 0
        self._t_subscribe = None

    def connectionMade(self):
        self._t_subscribe = time.time()
        self._send("mining.subscribe", ["t4-swarm/0.1"])
        self._send("mining.authorize", ["swarm.%d" % self.cid, "x"])

    def _send(self, method, params):
        self._id += 1
        self.sendLine(json.dumps({"id": self._id, "method": method, "params": params}))

    def lineReceived(self, line):
        try:
            msg = json.loads(line)
        except ValueError:
            return
        if msg.get("method") == "mining.notify":
            now = time.time()
            if self._t_subscribe is not None:
                self.stats.record_first_notify(now - self._t_subscribe)
                self._t_subscribe = None
            self.stats.record_notify(self.cid, now)
            # share-diff, no hashrate: do NOT submit real work here in the
            # skeleton -- DOA replay path is wired in the next commit.


class Stats(object):
    def __init__(self):
        self.first_notify = []
        self.last_notify_at = {}
        self.inter_notify = []

    def record_first_notify(self, dt):
        self.first_notify.append(dt)

    def record_notify(self, cid, now):
        prev = self.last_notify_at.get(cid)
        if prev is not None:
            self.inter_notify.append(now - prev)
        self.last_notify_at[cid] = now

    def report(self):
        def pct(xs, p):
            if not xs:
                return float("nan")
            s = sorted(xs)
            return s[min(len(s) - 1, int(len(s) * p))]
        print("connections that saw first notify: %d" % len(self.first_notify))
        print("first-notify latency  p50=%.1fms p95=%.1fms max=%.1fms"
              % (pct(self.first_notify, .5) * 1e3,
                 pct(self.first_notify, .95) * 1e3,
                 (max(self.first_notify) if self.first_notify else float("nan")) * 1e3))
        print("inter-notify gap      p50=%.1fms p95=%.1fms  (reactor-stall proxy; G1 target <50ms)"
              % (pct(self.inter_notify, .5) * 1e3, pct(self.inter_notify, .95) * 1e3))


class ConnFactory(protocol.ClientFactory):
    def __init__(self, cid, stats):
        self.cid, self.stats = cid, stats

    def buildProtocol(self, addr):
        return StratumConn(self.cid, self.stats)


@defer.inlineCallbacks
def run(host, port, n, seconds):
    stats = Stats()
    for cid in range(n):
        ep = TCP4ClientEndpoint(reactor, host, port)
        ep.connect(ConnFactory(cid, stats))
    yield task_sleep(seconds)
    stats.report()
    reactor.stop()


def task_sleep(seconds):
    d = defer.Deferred()
    reactor.callLater(seconds, d.callback, None)
    return d


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="T4 stratum-swarm DOA-under-load harness")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9327)
    ap.add_argument("--n", type=int, default=200, help="distinct connections")
    ap.add_argument("--seconds", type=int, default=60)
    a = ap.parse_args()
    reactor.callWhenRunning(run, a.host, a.port, a.n, a.seconds)
    reactor.run()
