'''
CooperativeEvent: drop-in replacement for p2pool.util.variable.Event whose
fan-out is time-sliced across reactor turns.

Part of the DOA-under-load fix: new_work_event has one watcher per stratum
connection (plus longpoll one-time watchers).  The stock Event calls all of
them synchronously in ONE reactor turn; with many connections that turn
blocks mining.submit handling and p2p share processing, converting hashing
time into DOA.  This subclass runs watchers in registration order but yields
back to the reactor whenever a time budget is exceeded, so the reactor never
holds longer than ~budget_s per turn during fan-out.

Semantics preserved relative to variable.Event (verified against this fork's
p2pool/util/variable.py: Event carries observers / _once / times):
  * self.times is incremented synchronously in happened() (get_work reads it
    for longpoll counting).
  * observers are snapshotted at happened() time and run in sorted(id) order
    — same ordering as the stock implementation.
  * one-time observers (the `once` Event used by get_deferred) always
    eventually fire, after the regular observers, exactly as in the stock
    happened().  The `once` Event is itself a CooperativeEvent, so longpoll
    fan-out is sliced too.  No coalescing/dropping: a burst of events runs
    each watcher once per event, just sliced.
  * an exception in one observer is logged and does not prevent the
    remaining observers from running (same as this fork's stock Event).
'''

from __future__ import division

import time

from twisted.internet import reactor
from twisted.python import log

from p2pool.util import variable


class CooperativeEvent(variable.Event):
    def __init__(self, budget_s=0.025):  # TODO(maintainer): tune; 25 ms keeps submit/p2p handling responsive
        variable.Event.__init__(self)
        self.budget_s = budget_s

    @property
    def once(self):
        # same lazy-creation contract as variable.Event.once, but the once
        # Event is cooperative too, so its fan-out is also time-sliced
        res = self._once
        if res is None:
            res = self._once = CooperativeEvent(self.budget_s)
        return res

    def happened(self, *event):
        self.times += 1
        once, self._once = self._once, None
        callbacks = [func for _id, func in sorted(self.observers.iteritems())]
        if once is not None:
            # stock Event calls once.happened(*event) after the observer
            # loop; keep that ordering — once is itself a CooperativeEvent
            # (see the once property), so its own fan-out is sliced as well
            callbacks.append(once.happened)
        self._run_chunk(callbacks, 0, event)

    def _run_chunk(self, callbacks, start, event):
        deadline = time.time() + self.budget_s
        i = start
        n = len(callbacks)
        while i < n:
            try:
                callbacks[i](*event)
            except Exception:
                log.err(None, 'Error in CooperativeEvent observer:')
            i += 1
            if i < n and time.time() > deadline:
                # yield the reactor: pending mining.submit / p2p traffic is
                # processed before the remaining watchers are notified
                reactor.callLater(0, self._run_chunk, callbacks, i, event)
                return
