# Hardware Procurement List

Items still needed as of August 2026. Owned already: MK-F01 fogger, MK-C09 CO2
cannon, speakers. In transit: H-E01A flame machine.

## 1. Controller — Raspberry Pi

**Note:** the Pi 5 has **no 3.5mm audio jack** (the Pi 4 does). Either model
works; a Pi 5 needs a ~$10 USB audio adapter for wired aux out.

- **Recommended: CanaKit Raspberry Pi 5 Starter Kit** (includes 27W PSU,
  microSD, case with fan, cables) — [canakit.com/raspberry-pi-5](https://www.canakit.com/raspberry-pi-5) (~$120–160 depending on RAM/kit)
- Alternative single-board sources (all official approved resellers, see
  [raspberrypi.com/resellers](https://www.raspberrypi.com/resellers/)):
  [Adafruit](https://www.adafruit.com/), [PiShop.us](https://www.pishop.us/),
  [CanaKit](https://www.canakit.com/)
- If Pi 5: add a **USB audio adapter** (e.g., Adafruit or UGREEN USB-A to
  3.5mm, ~$10) for the aux feed to the speakers.

Any RAM size works for this project; 4GB is plenty.

## 2. USB→DMX adapter

- **Enttec DMX USB Pro (part 70304)** — hardware frame timing, isolated output;
  the industry standard.
  - [ENTTEC official](https://www.enttec.com/product/dmx-usb-interfaces/dmx-usb-pro-professional-1u-usb-to-dmx512-converter/) — $170
  - [Full Compass](https://www.fullcompass.com/prod/204868-enttec-dmx-usb-pro-usb-to-dmx-interface-with-isolation) — ~$162
  - [B&H Photo](https://www.bhphotovideo.com/c/product/1767486-REG/enttec_70304_dmx_usb_pro.html)
- Includes USB cable. Output is **5-pin XLR female** — adapter needed, see cabling.

Avoid "Open DMX" clones: no onboard frame engine, timing depends on the host
CPU. Not acceptable for a rig with pyro on the line.

## 3. LED PAR fixtures (×8)

Requirements: DMX512 with a documented channel map and red-capable output.
LEDs are the one non-safety-critical part of the rig — a cheap fixture that
flickers or dies costs a glow spot, not safety — so this is the right place
to economize. Note: RGB-only fixtures mix a perfectly good amber (red+green)
for lava glow; a dedicated amber emitter is nice-to-have, not required.

Three tiers, cheapest first:

- **SELECTED (cheapest): OPPSK 36x1W RGB par, 4-packs** —
  [Amazon B0CJLDM7WW](https://www.amazon.com/OPPSK-36LED-Par-Lights-4Pack/dp/B0CJLDM7WW).
  DMX512 (~7-channel mode; confirm map from boxed manual), RGB 12R/12G/12B,
  daisy-chainable. Lower output than bigger pars but ample for close-range
  night glow — dim red is the design goal anyway. **Not IP-rated** — fixtures
  *inside* the cone are effectively sheltered; any in the open need a simple
  rain shield (angled board, upside-down bin) and retrieval in real rain.
  Buy 2× 4-packs + consider 1 spare. Verify 3-pin XLR in/out on arrival.
- Similar class: **Betopper 54x3W RGB par** (brighter, ~2× price) —
  [4-pack](https://www.amazon.com/BETOPPER-Activated-Professional-Parties-Wedding/dp/B09L7Z544Z) /
  [single](https://www.amazon.com/Betopper-Lights-Uplights-Lighting-Wedding/dp/B075GNL1T7).
- **Budget waterproof (~$35–45 ea): no-name IP65/IP67 pars** — e.g.
  [U`King IP67 RGBW](https://www.amazon.com/U%60King-Waterproof-Activated-Control-Outdoor/dp/B0D2QBSGRV)
  or [DJXFLI IP65 2-pack](https://www.amazon.com/Waterproof-18x18W-Equipment-Halloween-Decorations/dp/B0CJX98W4M) —
  ~$280–350 for 8, no weather worries, brand quality is a lottery so buy one
  spare.
- **Step-up (~$70–90 ea): SHEHDS IP65 7x18W RGBWA+UV** — cast aluminum,
  fanless, waterproof connectors, dedicated amber+red emitters.
  [Amazon](https://www.amazon.com/SHEHDS-Waterproof-Lighting-Upligting-Christmas/dp/B0BPM3JX92) —
  ~$650 for 8.
- (Concert-grade for reference: [Chauvet SlimPAR Pro Q IP](https://www.chauvetdj.com/products/slimpar-pro-q-ip/),
  ~$300 ea — overkill for this project.)

Mixed models are fine — each model just gets its own channel-map entry in
config. Prices fluctuate; verify at the listing.

**Verify before ordering:** that the chosen listing states DMX-in/out on
3-pin XLR (or includes pigtails compatible with standard 3-pin DMX cable).

## 4. Cabling & DMX accessories

Reputable sources: [Sweetwater](https://www.sweetwater.com/c1447--Lighting_Cables),
B&H, Full Compass. Accu-Cable and Hosa are solid brands.

| Item | Qty | Purpose | Source |
|---|---|---|---|
| 5-pin male → 3-pin female DMX adapter (Hosa DMX-310 or equal) | 1 (+1 spare) | Enttec's 5-pin output → 3-pin fixture chain. **Verify gender/direction**: 5-pin male plugs into the Enttec. | [Sweetwater Hosa DMX adapters](https://www.sweetwater.com/store/detail/DMX306--hosa-dmx-306-110ohm-adapter-cable-6-inch-xlr3m-to-xlr5f) (DMX-306 page — order the opposite-direction DMX-310) |
| 3-pin DMX cables, 10ft (Accu-Cable AC3PDMX or Hosa DMX-3xx) | ~10 | Daisy chain: 8 PARs + fogger + CO2 + flame ≈ 11 hops; most short | [Accu-Cable at Sweetwater](https://www.sweetwater.com/c1447--Accu-Cable--Lighting_Cables) ($12–56 by length) |
| 3-pin DMX cables, 25ft | 2–3 | Adapter→first fixture, and any long hops across the volcano | [AC3PDMX25](https://www.sweetwater.com/store/detail/AC3PDMX25--accu-cable-ac3pdmx25-3-pin-3-conductor-dmx-cable-25-foot) |
| 3-pin DMX terminator (Hosa DMT-414) | 1 (+1 spare) | Terminate the last fixture in the chain — prevents signal reflections; cheap insurance on a line with pyro | Sweetwater, ~$11 |
| 3.5mm aux cable (male-male), length to suit speaker placement | 1 | Pi → speakers | any (Hosa/Amazon Basics) |

Buy **actual DMX cable (110Ω), not microphone XLR cable** — mic cable is the
classic source of flickering/glitching DMX runs.

Spares philosophy: one extra of anything that would kill the show if it failed
on October 30th (cable, terminator, adapter).

## 5. Power distribution (plan, source locally)

- Outdoor-rated extension cords and a **GFCI-protected** circuit/adapter for
  everything in the yard.
- Fogger is 1500W — give it its own circuit/cord; don't share with the Pi.
- Outdoor power strip or stake for the PAR cluster.
- Weather cover for the Pi + Enttec (dry box with cable glands, or at minimum
  a covered tote).

## Rough budget

| | |
|---|---|
| Pi 5 kit + USB audio | ~$140 |
| Enttec DMX USB Pro | ~$165 |
| 8–9× OPPSK 36W RGB PARs (2× 4-pack + spare) | ~$130–170 |
| DMX cables/adapter/terminator | ~$150 |
| Power/weatherproofing | ~$75 |
| **Total** | **~$680** |

(Betopper tier ≈ +$100–200; IP65 budget pars ≈ +$150; SHEHDS ≈ +$500.)
