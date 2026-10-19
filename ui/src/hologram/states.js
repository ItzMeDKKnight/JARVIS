// Visual parameters of the arc reactor: one theme object for proportions and colours, and
// one target parameter set per assistant state. The renderer eases from the current values
// toward the target every frame, so state changes are never abrupt.
//
// Tweak the look here; nothing else needs to change.

export const THEME = {
  // 'circle' (default) or 'triangle' (the Mark II style centre).
  coreShape: 'circle',
  coilCount: 10,
  // Radii in reactor units (the outer housing edge is 1.0).
  proportions: {
    housingOuter: 1.0,
    housingInner: 0.8,
    coilOuter: 0.77,
    coilInner: 0.53,
    ringRadius: 0.47,
    ringWidth: 0.012,
    coreRadius: 0.2,
  },
  colors: {
    ring: [0.72, 0.9, 1.0], // pale blue-white
    coreIdle: [0.82, 0.93, 1.0],
    white: [1.0, 1.0, 1.0],
    cyan: [0.3, 0.95, 1.0],
    thinking: [0.45, 0.68, 1.0],
    error: [1.0, 0.45, 0.15],
  },
  bloom: { radius: 0.45, threshold: 0.55 },
};

const c = THEME.colors;

/** Every state sets every parameter, so easing between any two states is well-defined. */
export const STATE_PARAMS = {
  idle: {
    coreIntensity: 0.55,
    ringIntensity: 0.55,
    coilLight: 0.1,
    breathe: 1,
    chase: 0,
    ringPulse: 0,
    auraIntensity: 0,
    auraColor: c.white,
    coreTint: c.coreIdle,
    ringColor: c.ring,
    levelGain: 0,
    saturation: 1,
    flicker: 0,
    errorMix: 0,
    bloomStrength: 0.6,
  },
  listening: {
    coreIntensity: 0.95,
    ringIntensity: 0.85,
    coilLight: 0.25,
    breathe: 0,
    chase: 0,
    ringPulse: 0,
    auraIntensity: 0.75,
    auraColor: c.white,
    coreTint: c.white,
    ringColor: c.ring,
    levelGain: 1,
    saturation: 1,
    flicker: 0,
    errorMix: 0,
    bloomStrength: 0.85,
  },
  thinking: {
    coreIntensity: 0.75,
    ringIntensity: 0.8,
    coilLight: 0.08,
    breathe: 0,
    chase: 1,
    ringPulse: 1,
    auraIntensity: 0,
    auraColor: c.thinking,
    coreTint: c.thinking,
    ringColor: c.thinking,
    levelGain: 0,
    saturation: 1,
    flicker: 0,
    errorMix: 0,
    bloomStrength: 0.75,
  },
  speaking: {
    coreIntensity: 0.9,
    ringIntensity: 0.9,
    coilLight: 0.3,
    breathe: 0,
    chase: 0,
    ringPulse: 0,
    auraIntensity: 0.8,
    auraColor: c.cyan,
    coreTint: c.cyan,
    ringColor: c.cyan,
    levelGain: 1,
    saturation: 1,
    flicker: 0,
    errorMix: 0,
    bloomStrength: 0.9,
  },
  error: {
    coreIntensity: 0.7,
    ringIntensity: 0.7,
    coilLight: 0.2,
    breathe: 0,
    chase: 0,
    ringPulse: 0,
    auraIntensity: 0.15,
    auraColor: c.error,
    coreTint: c.error,
    ringColor: c.error,
    levelGain: 0,
    saturation: 1,
    flicker: 1,
    errorMix: 0.6,
    bloomStrength: 0.7,
  },
  muted: {
    coreIntensity: 0.35,
    ringIntensity: 0.35,
    coilLight: 0.05,
    breathe: 0.4,
    chase: 0,
    ringPulse: 0,
    auraIntensity: 0,
    auraColor: c.white,
    coreTint: c.coreIdle,
    ringColor: c.ring,
    levelGain: 0,
    saturation: 0.12,
    flicker: 0,
    errorMix: 0,
    bloomStrength: 0.4,
  },
  // Not an assistant state: the backend is unreachable.
  offline: {
    coreIntensity: 0.08,
    ringIntensity: 0.1,
    coilLight: 0.02,
    breathe: 0.3,
    chase: 0,
    ringPulse: 0,
    auraIntensity: 0,
    auraColor: c.white,
    coreTint: c.coreIdle,
    ringColor: c.ring,
    levelGain: 0,
    saturation: 0.25,
    flicker: 0.35,
    errorMix: 0,
    bloomStrength: 0.3,
  },
};

// The error look is held at least this long, so a quick ERROR -> SPEAKING still flickers.
export const ERROR_HOLD_MS = 1200;

// Motion scaling for prefers-reduced-motion: slower sweeps, no ripples, no flicker.
export const MOTION = {
  normal: { speed: 1, ripple: 1, flicker: 1, particles: 1, parallax: 1 },
  reduced: { speed: 0.25, ripple: 0, flicker: 0, particles: 0.3, parallax: 0.3 },
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

/**
 * Smooths a jumpy 0..1 audio level: fast attack (the aura reacts at once), slow release
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
