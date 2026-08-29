# Simulation: does the deployed OverrideGovernor allow a permanent easy-mode
# loop at runtime? Two properties probed:
#  P1: duration_bound exit sets NO cooldown -> immediate re-arm allowed.
#  P2: if own minting is starved (refusal path: < MIN_OWN_DELTA fresh shares in
#      the 600s window), the orphan circuit-breaker NEVER fires, so the ONLY
#      exit is duration_bound -> combined with P1, easy mode re-arms forever.
from p2pool.override_governor import OverrideGovernor

class Clock(object):
    def __init__(self): self.t = 1000000.0
    def __call__(self): return self.t

clk = Clock()
mint = [0]; notchain = [0]
gov = OverrideGovernor(own_hr_fn=lambda: 1e9,
                       own_mint_fn=lambda: mint[0],
                       own_notchain_fn=lambda: notchain[0],
                       clock=clk)

# Scenario: node in trouble; whale entry condition stays true (caller re-arms
# whenever arm() permits). Minting is starved: only 5 shares/600s get minted
# (sibling refusal kills the rest), ALL of them orphan.
arms = 0
exits = []
armed = gov.arm('whale')
assert armed
arms += 1
for step in range(int(4 * 3600 / 5)):   # 4 hours at 5s ticks
    clk.t += 5.0
    # starved minting: one share every 120s, always orphaned
    if step % 24 == 0:
        mint[0] += 1; notchain[0] += 1
    gov.sample()
    active = gov.tick('whale')
    if not active:
        st = gov.status()['whale']
        exits.append((round(clk.t - 1000000.0), st['exit_reason'], st['rearm_blocked']))
        # caller (work.py) re-arms next tick because ratio<=0.5 still true
        if gov.arm('whale'):
            arms += 1

dur = 4 * 3600
print 'window: %ds' % dur
print 'arms: %d  exits: %d' % (arms, exits and len(exits) or 0)
print 'exit reasons:', set(r for _, r, _ in exits)
print 'orphan_breaker ever fired:', any(r == 'orphan_breaker' for _, r, _ in exits)
active_time = arms * 1800.0
print 'fraction of window in easy mode (upper bound): %.2f' % (min(active_time, dur) / dur)
# P1/P2 verdicts
p1 = all(not blocked for _, r, blocked in exits if r == 'duration_bound')
print 'P1 duration exit leaves rearm UNBLOCKED:', p1
print 'P2 breaker starved (no orphan_breaker despite 100%% own-orphan):', \
    not any(r == 'orphan_breaker' for _, r, _ in exits)
