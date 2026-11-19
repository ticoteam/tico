/* Every flight number, in one place (motion.md 2, "The one place to turn flights down"). Changing a number here is the
   only edit needed to back flights down. Values are the mock's (tico/dot-states.js L162-177) unless marked. */

export type DropletSize = 'standard' | 'small' | 'tiny';

export const FLIGHT = {
  maxPerBurst: 3, // three droplets at most; the chat line counts the rest
  burstGapMs: 320, // launch i at T + i*320 (DS.js L268)
  burstWindowMs: 400, // Proposed: changes within 400ms of each other are one burst
  queueGapMs: 200, // Proposed: a waiting burst starts 200ms after the last landing
  queueKeep: 2, // Proposed: more than two bursts waiting: only the newest two keep droplets
  extraWashAfterMs: 120, // changes past the cap wash at lastLanding + 120 + j*70 (DS.js L269)
  extraWashGapMs: 70,
  lineAfterMs: 380, // the burst's chat line at lastLanding + 380
  offWashGapMs: 70, // Off: washes at once, 70ms apart (motion.md B11)
  sizes: { standard: [14, 9, 7, 5], small: [9, 6, 4], tiny: [6, 4] } as Record<DropletSize, number[]>,
  msPerPx: 0.9,
  minMs: 300,
  maxMs: 460,
  tinyFactor: 0.62,
  liftFactor: 0.26,
  liftMax: 64,
  dipFloor: 30, // near the top the arc dips instead of rising
  dipFactor: 0.7,
  edgePad: 16,
  trailLag: 0.16,
  headSwell: 0.3,
  aimLeft: 14, // a row or a title is aimed at left + 14px
  bloomMs: 200,
  bloomScale: 1.5,
  ripple: true, // one switch removes the landing ring (motion.md 9, R5)
  rippleMs: 460,
  rippleSize: { title: 1.2, row: 0.7, chip: 0.8 },
  washMs: 1100,
  washRise: 0.08,
  popMs: 420,
  popFace: 1.28,
  popStack: 1.22,
  lostFadeMs: 120, // Proposed: a flight whose target goes mid-air fades where it is
  edgeChip: true, // motion.md 9, R3: a change off screen lands on a chip at the nearest edge
  samples: 60,
  kinds: { open: 'standard', edit: 'standard', ask: 'small', answer: 'tiny' } as Record<string, DropletSize>,
  minLevel: { standard: 1, small: 2, tiny: 2, burstExtra: 2 } as Record<DropletSize | 'burstExtra', number>,
};

/* Flags beside it (motion.md 7). The README's call wins over motion.md D2: the nudge shows, at More only. */
export const MOTION = {
  think: 'shape' as 'shape' | 'droplet',
  callBars: 'A' as 'A' | 'B',
  flowNudge: true,
};
