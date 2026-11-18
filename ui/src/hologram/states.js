// Visual parameters of the holographic lens HUD: one theme object (palettes, the list of ring
// layers, bloom, timing) and one target parameter set per assistant state. The renderer eases
// from the current values toward the target every frame, so state changes are never abrupt.
//
// Tweak the look here; nothing else needs to change.

/** '#RRGGBB' -> [r, g, b] in 0..1 */
export function hex(value) {
  const n = parseInt(value.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}

// Each palette runs white-hot at the centre to saturated at the edge.
export const PALETTES = {
  blue: { core: hex('#E8FBFF'), mid: hex('#00E5FF'), edge: hex('#0A6CFF') },
  orange: { core: hex('#FFF1D6'), mid: hex('#FF9A2E'), edge: hex('#FF3D00') },
  red: { core: hex('#FFE1DC'), mid: hex('#FF2A2A'), edge: hex('#B00000') },
};

// Ring layers, centre outwards. Radii are in HUD units (the main dial's outer edge is ~1.0).
// kind:     ring (thin full band), arcs (thick segmented arcs), dashes (dashed ring),
//           ticks (dial marks), radials (thin spokes), blocks (small rectangles),
//           text (tiny unreadable readouts), flares (lens-flare hot spots)
// z:        depth (towards the camera is positive) - this is what gives true parallax
// speed:    rotation in radians/second at spin 1 (sign = direction)
// tilt:     [x, y] in degrees, so the disc reads as 3D
// alpha:    brightness multiplier
// compact:  false = left out of the small always-on-top window
export const LAYERS = [
  { kind: 'ring', radius: 0.055, width: 0.006, z: 0.3, speed: 0, alpha: 1.2 },
  { kind: 'ring', radius: 0.085, width: 0.004, z: 0.28, speed: 0, alpha: 1.0 },
  { kind: 'dashes', radius: 0.11, width: 0.006, count: 24, duty: 0.55, z: 0.26, speed: 0.6, alpha: 1.0 },
  { kind: 'ring', radius: 0.135, width: 0.003, z: 0.24, speed: 0, alpha: 0.9 },
  { kind: 'arcs', radius: 0.165, width: 0.018, count: 3, gap: 0.12, z: 0.22, speed: -0.45, alpha: 0.9 },
  { kind: 'ring', radius: 0.2, width: 0.007, z: 0.2, speed: 0, alpha: 1.3 },
  { kind: 'ticks', radius: 0.225, length: 0.018, count: 120, major: 10, z: 0.18, speed: 0.12, alpha: 0.8 },
  { kind: 'arcs', radius: 0.29, width: 0.045, count: 6, gap: 0.18, z: 0.12, speed: 0.22, tilt: [4, 0], alpha: 0.75 },
  { kind: 'flares', radius: 0.29, count: 2, z: 0.13, speed: 0.22, tilt: [4, 0], alpha: 0.9 },
  { kind: 'ring', radius: 0.355, width: 0.003, z: 0.09, speed: 0, alpha: 0.8 },
  { kind: 'dashes', radius: 0.395, width: 0.008, count: 72, duty: 0.4, z: 0.07, speed: -0.18, alpha: 0.7 },
  { kind: 'arcs', radius: 0.48, width: 0.06, count: 3, gap: 0.35, z: 0.02, speed: -0.1, tilt: [0, -5], alpha: 0.55 },
  { kind: 'flares', radius: 0.48, count: 1, z: 0.03, speed: -0.1, tilt: [0, -5], alpha: 1.0 },
  { kind: 'ticks', radius: 0.575, length: 0.035, count: 180, major: 15, z: 0.0, speed: 0.05, alpha: 0.75 },
  { kind: 'ring', radius: 0.625, width: 0.003, z: -0.02, speed: 0, alpha: 0.7 },
  { kind: 'ring', radius: 0.645, width: 0.002, z: -0.03, speed: 0, alpha: 0.5, compact: false },
  { kind: 'arcs', radius: 0.735, width: 0.035, count: 12, gap: 0.3, z: -0.08, speed: 0.07, tilt: [-3, 2], alpha: 0.6 },
  { kind: 'dashes', radius: 0.81, width: 0.005, count: 140, duty: 0.5, z: -0.11, speed: -0.04, alpha: 0.55, compact: false },
  { kind: 'ticks', radius: 0.86, length: 0.022, count: 90, major: 6, z: -0.13, speed: 0.03, alpha: 0.6 },
  { kind: 'ring', radius: 0.94, width: 0.003, z: -0.16, speed: 0, alpha: 0.6 },
  { kind: 'arcs', radius: 0.985, width: 0.025, count: 2, gap: 0.62, z: -0.18, speed: -0.03, tilt: [2, -3], alpha: 0.7 },
  { kind: 'flares', radius: 0.985, count: 1, z: -0.17, speed: -0.03, tilt: [2, -3], alpha: 0.8, compact: false },
  // Outer layer: spokes, blocks and readouts floating at different depths.
  { kind: 'radials', inner: 1.03, outer: 1.32, count: 70, z: -0.25, speed: 0.012, alpha: 0.4 },
  { kind: 'blocks', inner: 1.05, outer: 1.35, count: 34, z: -0.05, speed: -0.015, alpha: 0.6 },
  { kind: 'text', inner: 1.12, outer: 1.5, count: 14, z: 0.1, speed: 0.008, alpha: 0.55 },
  { kind: 'text', inner: 1.1, outer: 1.45, count: 10, z: -0.35, speed: -0.006, alpha: 0.4, compact: false },
];

export const THEME = {
  background: '#02040A',
  // The outermost radius that still uses the gradient's edge colour.
  gradientRadius: 1.0,
  // Fraction of that radius where the gradient has reached its mid colour (white-hot inside).
  gradientMid: 0.2,
  // The central glow (a procedural sprite, not a texture).
  core: { radius: 0.11, intensity: 0.8 },
  // Particles and short line fragments drifting outward in depth.
  particles: { count: 420, inner: 0.95, outer: 1.85, size: 3.2, speed: 0.05, depth: 1.2 },
  fragments: { count: 90, inner: 1.0, outer: 1.75, length: 0.05, speed: 0.04, depth: 0.9 },
  // Which side the orange accents sit on while idle (radians, 0 = right).
  accentAngle: -0.25,
  // Number of blue/orange segments chasing around the rings while thinking.
  chaseSegments: 6,
  chaseSpeed: 1.6,
  bloom: { radius: 0.5, threshold: 0.2 },
  // Final pass: soft colour fringing towards the edges (device pixels) and a gentle vignette.
  chroma: 1.6,
  vignette: 0.85,
  // Camera: slow drift (radians of orbit) and how far the mouse tilts the view.
  camera: { fovDeg: 32, drift: 0.05, driftSpeed: 0.07, parallax: 0.12 },
  // Cross-fade between states (colours, motion, intensity), in milliseconds.
  fadeMs: 400,
  // Below this many CSS pixels (the compact always-on-top window) detail is reduced.
  compactBelowPx: 440,
  compactCounts: 0.45, // particles, fragments, spokes, blocks and readouts in compact mode
  // Frame pacing: calm states render at this interval to save power (ms).
  calmFrameMs: 33,
};

/**
 * Every state sets every parameter, so easing between any two states is well-defined.
 *
 * intensity   overall brightness          orange   0 = blue palette, 1 = orange palette
 * twoTone     idle's orange accent side   chase    blue/orange segments racing round
 * error       red palette mix             saturation  1 = full colour, 0 = grey
 * coreSize    central glow scale          spin     rotation speed multiplier
 * breathe     slow brightness breathing   flicker  brief brightness flicker
 * levelGain   how strongly the audio level drives glow, core size and ring pulse
 * pulse       ring pulse amplitude (with levelGain) drift  particle drift multiplier
 * bloomStrength
 */
export const STATE_PARAMS = {
  idle: {
    intensity: 0.75,
    orange: 0,
    twoTone: 1,
    chase: 0,
    error: 0,
    saturation: 1,
    coreSize: 0.85,
    spin: 0.35,
    breathe: 1,
    flicker: 0,
    levelGain: 0,
    pulse: 0,
    drift: 0.5,
    bloomStrength: 0.9,
  },
  listening: {
    intensity: 1.0,
    orange: 0,
    twoTone: 0,
    chase: 0,
    error: 0,
    saturation: 1,
    coreSize: 1,
    spin: 0.8,
    breathe: 0,
    flicker: 0,
    levelGain: 1,
    pulse: 1,
    drift: 1,
    bloomStrength: 0.9,
  },
  thinking: {
    intensity: 0.9,
    orange: 0,
    twoTone: 0,
    chase: 1,
    error: 0,
    saturation: 1,
    coreSize: 0.9,
    spin: 2.2,
    breathe: 0,
    flicker: 0,
    levelGain: 0,
    pulse: 0,
    drift: 1.4,
    bloomStrength: 0.7,
  },
  speaking: {
    intensity: 1.0,
    orange: 1,
    twoTone: 0,
    chase: 0,
    error: 0,
    saturation: 1,
    coreSize: 1,
    spin: 0.8,
    breathe: 0,
    flicker: 0,
    levelGain: 1,
    pulse: 1,
    drift: 1,
    bloomStrength: 0.9,
  },
  error: {
    intensity: 1.0,
    orange: 0,
    twoTone: 0,
    chase: 0,
    error: 1,
    saturation: 1,
    coreSize: 0.9,
    spin: 0.25,
    breathe: 0,
    flicker: 1,
    levelGain: 0,
    pulse: 0,
    drift: 0.4,
    bloomStrength: 0.9,
  },
  muted: {
    intensity: 0.5,
    orange: 0,
    twoTone: 0,
    chase: 0,
    error: 0,
    saturation: 0.08,
    coreSize: 0.7,
    spin: 0.15,
    breathe: 0.4,
    flicker: 0,
    levelGain: 0,
    pulse: 0,
    drift: 0.25,
    bloomStrength: 0.45,
  },
  // Not an assistant state: the backend is unreachable.
  offline: {
    intensity: 0.12,
    orange: 0,
    twoTone: 0,
    chase: 0,
    error: 0,
    saturation: 0.3,
    coreSize: 0.5,
    spin: 0.03,
    breathe: 0.3,
    flicker: 0,
    levelGain: 0,
    pulse: 0,
    drift: 0.05,
    bloomStrength: 0.3,
  },
};

// The error look is held at least this long, so a quick ERROR -> SPEAKING still flickers.
export const ERROR_HOLD_MS = 1200;

// Motion scaling for prefers-reduced-motion: slower rotation, no camera drift, gentler pulses.
export const MOTION = {
  normal: { speed: 1, drift: 1, pulse: 1, flicker: 1, particles: 1, parallax: 1 },
  reduced: { speed: 0.25, drift: 0, pulse: 0.35, flicker: 0, particles: 0.3, parallax: 0.3 },
};

/** Ease every numeric (and numeric-array) parameter of `current` toward `target`. */
export function easeParams(current, target, amount) {
  const next = {};
  for (const key of Object.keys(target)) {
    const from = current[key];
    const to = target[key];
    if (Array.isArray(to)) {
      next[key] = to.map((value, i) => lerp(from?.[i] ?? value, value, amount));
    } else if (typeof to === 'number') {
      next[key] = lerp(typeof from === 'number' ? from : to, to, amount);
    } else {
      next[key] = to;
    }
  }
  return next;
}

export function lerp(a, b, t) {
  return a + (b - a) * t;
}

/** Frame-rate independent easing factor: `rate` is the fraction covered per second. */
export function easeAmount(rate, dtSeconds) {
  return 1 - Math.pow(1 - rate, dtSeconds);
}

/** Easing factor that completes ~95% of a cross-fade in `fadeMs` (three time constants). */
export function fadeAmount(dtSeconds, fadeMs = THEME.fadeMs) {
  const tau = fadeMs / 3000;
  return 1 - Math.exp(-dtSeconds / tau);
}

/**
 * Smooths a jumpy 0..1 audio level: fast attack (the glow reacts at once), slow release
 * (it doesn't strobe between syllables).
 */
export class LevelEnvelope {
  constructor({ attack = 0.6, release = 0.08 } = {}) {
    this.attack = attack;
    this.release = release;
    this.value = 0;
  }

  update(input, dtSeconds) {
    const rate = input > this.value ? this.attack : this.release;
    const k = 1 - Math.pow(1 - rate, dtSeconds * 60);
    this.value += (Math.max(0, Math.min(1, input)) - this.value) * k;
    return this.value;
  }
}

/** Which state to draw: holds ERROR for ERROR_HOLD_MS; OFFLINE wins when disconnected. */
export function visualState({ connected, state, errorSince, now }) {
  if (!connected) return 'offline';
  if (errorSince !== null && now - errorSince < ERROR_HOLD_MS) return 'error';
  return STATE_PARAMS[state] ? state : 'idle';
}
