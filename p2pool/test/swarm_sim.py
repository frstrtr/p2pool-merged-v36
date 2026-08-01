"""
T4 stratum-swarm simulator (DOA-under-load harness) -- p2pool v35 / c2pool v36.

Opens N *distinct* stratum connections at share-difficulty with NO hashrate, all
pointed at a single p2pool (or c2pool) stratum endpoint. Instruments the two
signals that matter for the G1 DOA fix:

  1. reactor stall  -- wall time the reactor spends inside a single get_work
                       response (proxy: subscribe->first-notify latency, and
                       inter-notify gap under load). G1 target: never > ~50 ms.
  2. DOA%           -- of shares the swarm submits (replayed from a share-diff
                       template), fraction the node tags stale/expired/DOA.

This is the transition-scenario proof 3 physical rigs cannot produce: hundreds
of connections x uncapped-template width. Methodology: v36-master #41.

DOA replay path (this commit)
-----------------------------
The swarm has no hashrate, so it cannot mine a fresh share. Instead each conn
REPLAYS a canonical share-diff template (extranonce2 = zeros of the server's
extranonce2_size, ntime from the job, a fixed nonce) as a mining.submit. The
submit is scheduled --submit-delay-ms AFTER the notify that carried the job, to
model a real miner's find-time. It is submitted against the job_id that was
current when the delay was armed.

The p2pool/stratum server validates job-currency BEFORE proof-of-work, so a
submit whose job_id the server has already superseded comes back tagged
stale / "job not found" / expired -- independent of the (invalid) nonce. That
is exactly the DOA signal: under reactor stall the notify fan-out and the
submit drain both queue behind the O(users x tx) get_work rebuild, the job
advances before our submit is processed, and the stale count climbs. A
G1-fixed node holds the reactor <50 ms, drains submits promptly, and keeps
DOA% flat as connection count rises; an unfixed node tracks the kr1z1s 10-12%.

Only explicit stale / job-not-found / expired tags are counted as DOA. A
high-hash / low-difficulty reject (the replayed nonce failing PoW on a job that
WAS still current) is counted as reject-other, never as DOA -- so the DOA% is
not inflated by the fact that we cannot produce real PoW.

Python2 / Twisted (reactor) -- matches the fork runtime.
"""
import json, time
from twisted.internet import reactor, defer, protocol
from twisted.protocols import basic

# stratum error signatures that mean "your job is no longer the tip" == DOA.
_STALE_TOKENS = ("stale", "job not found", "unknown job", "expired",
                 "job_not_found", "not found")
# JSON-RPC error codes some stratum servers use for a stale/unknown job.
_STALE_CODES = (21, 22, 24)


def _looks_stale(err):
    if err is None:
        return False
    code = None
    msg = ""
    if isinstance(err, list):
        if len(err) >= 1:
            code = err[0]
        if len(err) >= 2 and err[1] is not None:
            msg = str(err[1])
    elif isinstance(err, dict):
        code = err.get("code")
        msg = str(err.get("message", ""))
    else:
        msg = str(err)
    if code in _STALE_CODES:
        return True
    m = msg.lower()
    return any(tok in m for tok in _STALE_TOKENS)


class StratumConn(basic.LineReceiver):
    delimiter = '\n'
    MAX_LENGTH = 1 << 20

    def __init__(self, cid, stats, cfg):
        self.cid = cid
        self.stats = stats
        self.cfg = cfg
        self._id = 0
        self._t_subscribe = None
        self.extranonce1 = ""
        self.extranonce2_size = 4
        self.cur_job = None            # job_id from the latest notify
        self.cur_ntime = "00000000"
        self._subscribe_id = None
        self._submits = {}             # rpc id -> job_id submitted against
        self._n_submitted = 0

    def connectionMade(self):
        self._t_subscribe = time.time()
        self._subscribe_id = self._send("mining.subscribe", ["t4-swarm/0.2"])
        self._send("mining.authorize", ["swarm.%d" % self.cid, "x"])

    def connectionLost(self, reason):
        self.stats.record_disconnect(self.cid)

    def _send(self, method, params):
        self._id += 1
        rid = self._id
        self.sendLine(json.dumps({"id": rid, "method": method, "params": params}))
        return rid

    def lineReceived(self, line):
        try:
            msg = json.loads(line)
        except ValueError:
            return
        method = msg.get("method")
        if method == "mining.notify":
            self._on_notify(msg.get("params") or [])
        elif method == "mining.set_difficulty":
            params = msg.get("params") or []
            if params:
                self.stats.record_difficulty(params[0])
        elif "result" in msg and msg.get("id") is not None:
            self._on_result(msg)

    def _on_result(self, msg):
        rid = msg.get("id")
        # subscribe response: result = [ [[...subs...]], extranonce1, extranonce2_size ]
        if rid == self._subscribe_id:
            res = msg.get("result")
            try:
                self.extranonce1 = res[1]
                self.extranonce2_size = int(res[2])
            except (TypeError, IndexError, ValueError):
                pass
            return
        # submit response: classify accepted / stale(DOA) / reject-other
        if rid in self._submits:
            self._submits.pop(rid, None)
            err = msg.get("error")
            if msg.get("result") is True and err is None:
                self.stats.record_submit("accepted")
            elif _looks_stale(err):
                self.stats.record_submit("stale")
            else:
                self.stats.record_submit("reject")

    def _on_notify(self, params):
        now = time.time()
        if self._t_subscribe is not None:
            self.stats.record_first_notify(now - self._t_subscribe)
            self._t_subscribe = None
        self.stats.record_notify(self.cid, now)
        if params:
            self.cur_job = params[0]
        # ntime is params[7] in the standard stratum notify layout.
        if len(params) > 7 and params[7]:
            self.cur_ntime = params[7]
        # arm a replayed share submit for the job we just saw.
        if self.cfg["submit"] and self._n_submitted < self.cfg["max_submits"]:
            job_at_arm = self.cur_job
            ntime_at_arm = self.cur_ntime
            reactor.callLater(self.cfg["submit_delay"],
                              self._replay_submit, job_at_arm, ntime_at_arm)

    def _replay_submit(self, job_id, ntime):
        if job_id is None or self.transport is None:
            return
        extranonce2 = "00" * self.extranonce2_size
        nonce = self.cfg["nonce"]
        rid = self._send("mining.submit",
                         ["swarm.%d" % self.cid, job_id, extranonce2, ntime, nonce])
        self._submits[rid] = job_id
        self._n_submitted += 1
        self.stats.record_submit("sent")


class Stats(object):
    def __init__(self):
        self.first_notify = []
        self.last_notify_at = {}
        self.inter_notify = []
        self.disconnects = 0
        self.difficulty = None
        self.submits = {"sent": 0, "accepted": 0, "stale": 0, "reject": 0}

    def record_first_notify(self, dt):
        self.first_notify.append(dt)

    def record_notify(self, cid, now):
        prev = self.last_notify_at.get(cid)
        if prev is not None:
            self.inter_notify.append(now - prev)
        self.last_notify_at[cid] = now

    def record_disconnect(self, cid):
        self.disconnects += 1

    def record_difficulty(self, d):
        self.difficulty = d

    def record_submit(self, kind):
        self.submits[kind] = self.submits.get(kind, 0) + 1

    def report(self):
        def pct(xs, p):
            if not xs:
                return float("nan")
            s = sorted(xs)
            return s[min(len(s) - 1, int(len(s) * p))]

        def ms(x):
            return x * 1e3

        print("=" * 66)
        print("T4 stratum-swarm DOA-under-load report")
        print("-" * 66)
        print("connections that saw first notify: %d   disconnects: %d"
              % (len(self.first_notify), self.disconnects))
        if self.difficulty is not None:
            print("share difficulty (last set_difficulty): %s" % (self.difficulty,))
        print("first-notify latency  p50=%.1fms p95=%.1fms max=%.1fms"
              % (ms(pct(self.first_notify, .5)),
                 ms(pct(self.first_notify, .95)),
                 ms(max(self.first_notify) if self.first_notify else float("nan"))))
        print("inter-notify gap      p50=%.1fms p95=%.1fms p99=%.1fms max=%.1fms"
              % (ms(pct(self.inter_notify, .5)),
                 ms(pct(self.inter_notify, .95)),
                 ms(pct(self.inter_notify, .99)),
                 ms(max(self.inter_notify) if self.inter_notify else float("nan"))))
        print("  (inter-notify gap = reactor-stall proxy; G1 target: never >50ms)")
        s = self.submits
        resolved = s["accepted"] + s["stale"] + s["reject"]
        doa_pct = (100.0 * s["stale"] / resolved) if resolved else float("nan")
        print("-" * 66)
        print("submits sent=%d  accepted=%d  stale/DOA=%d  reject-other=%d  pending=%d"
              % (s["sent"], s["accepted"], s["stale"], s["reject"],
                 s["sent"] - resolved))
        print("DOA%%  = %.2f%%  (stale / resolved; kr1z1s baseline 10-12%%, G1 target ~1-2%%)"
              % doa_pct)
        print("=" * 66)


class ConnFactory(protocol.ClientFactory):
    def __init__(self, cid, stats, cfg):
        self.cid, self.stats, self.cfg = cid, stats, cfg

    def buildProtocol(self, addr):
        return StratumConn(self.cid, self.stats, self.cfg)

    def clientConnectionFailed(self, connector, reason):
        self.stats.record_disconnect(self.cid)


@defer.inlineCallbacks
def run(host, port, n, seconds, cfg):
    stats = Stats()
    print("swarm: %d connections -> %s:%d for %ds  (submit=%s delay=%.0fms)"
          % (n, host, port, seconds, cfg["submit"], cfg["submit_delay"] * 1e3))
    for cid in range(n):
        reactor.connectTCP(host, port, ConnFactory(cid, stats, cfg))
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
    ap.add_argument("--no-submit", dest="submit", action="store_false",
                    help="measure notify/reactor-stall only, do not replay submits")
    ap.add_argument("--submit-delay-ms", type=float, default=250.0,
                    help="model miner find-time: delay after notify before replay submit")
    ap.add_argument("--max-submits-per-conn", type=int, default=1000,
                    help="cap replayed submits per connection")
    ap.add_argument("--nonce", default="00000000",
                    help="replayed share-diff template nonce (8 hex)")
    a = ap.parse_args()
    cfg = {
        "submit": a.submit,
        "submit_delay": a.submit_delay_ms / 1e3,
        "max_submits": a.max_submits_per_conn,
        "nonce": a.nonce,
    }
    reactor.callWhenRunning(run, a.host, a.port, a.n, a.seconds, cfg)
    reactor.run()
