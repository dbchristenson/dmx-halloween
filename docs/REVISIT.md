# Assumptions to revisit (blind web-app build, 2026-08-24)

Decisions made without you in the loop while building out the web control
features. Each is working and tested; flag anything you want changed.

## Safety-shaped decisions (deliberate narrowings)

1. **Pyro test firing is bursts, not on/off toggles.** The ask was
   "activation/deactivation for the CO2 … and LPG machine", but safety
   invariant 2 (DESIGN.md) says pyro only ever runs under a time-bounded
   permit. So the test console fires **time-boxed bursts** through the same
   `SafetyGate` path as eruption cues — flame ≤ 2.0s, CO2 ≤ 10s, min gaps
   enforced, arming required. There is deliberately no unbounded "flame on".
   Lights and fog, being non-pyro, did get true toggles.
2. **The config `pyro_enabled` flag is now enforced** (it was read but
   ignored): `SafetyGate.arm()` refuses while it is false, so `/api/arm`
   cannot arm in rehearsal mode. Enabling pyro takes a config edit + restart
   by design. Existing tests were updated to open this interlock explicitly.
3. **Test mode requires dormant and auto-engages "hold".** Exiting test mode
   leaves hold engaged — resuming the show is a separate, explicit act.
4. **CO2 test bursts consume the tank meter** and can trip the low-tank SMS,
   because the gas is really spent.

## Behavior choices

5. **Hold**: engaging hold while *active* returns to dormant immediately
   (active has no pyro); an in-progress imminent/eruption always completes
   first — nobody can stretch or truncate the warning. Manual triggers are
   refused while held.
6. **Pause** freezes phase timing (dwell clocks, cooldown) but only engages
   in dormant/active. Requested during imminent/eruption it queues
   ("pause queued" in the UI) and engages once the show is back in a safe
   phase. Ambient lighting keeps breathing while paused — only *timing* is
   frozen.
7. **Test mode blacks out the ambient render**: a fixture is lit iff its
   toggle is on, so a wiring check is unambiguous. Default test light color
   is amber (255,140,40) at full dimmer.
8. **Sim fast-forward** (`SimClock`, 0.1–100×) exists only in sim mode; the
   real driver always runs wall-clock. Audio still plays at wall-clock speed,
   so it desyncs at speeds ≠ 1× — acceptable for dwell-distribution checks.

## UI choices

9. `/impeccable` wasn't available in this session; the design pass used the
   `frontend-design` skill instead.
10. **No webfonts.** The console must render with the Starlink uplink down,
    so type is system-stack only (condensed grotesque labels + monospace
    numerals).
11. The phase annunciator + amber hazard band (armed) are the page's
    signature; "REHEARSAL (locked by config)" shows when arming is
    config-blocked.
12. State polls at 10Hz including the full 512-channel frame (~3KB) — fine
    on the show LAN; revisit only if phones struggle.
13. Fogger test slider assumes continuous fog is hardware-safe (MK-F01 has
    its own thermostat). Confirm duty-cycle guidance from the printed manual.
