// Procedural geometry for the lens HUD: rings, segmented arcs, dashes, ticks, spokes, blocks,
// unreadable "data readouts", lens-flare quads, particles and drifting line fragments.
//
// Pure functions returning plain typed arrays (no Three.js), so they are unit-tested in node.
// Bands (rings, arcs, dashes) are thin triangle strips rather than GL lines: GL lines are always
// one device pixel wide, while a band keeps the same thickness at any pixel ratio and can have
// soft, luminous edges. `aEdge` runs 0 (inner edge) -> 1 (outer edge) across each band.

const TAU = Math.PI * 2;

/** Small, fast seeded PRNG (mulberry32): the same layout on every start and in screenshots. */
export function random(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function bandSegments(spans, inner, outer, stepRad) {
  const positions = [];
  const edge = [];
  for (const [start, end] of spans) {
    const steps = Math.max(2, Math.ceil((end - start) / stepRad));
    for (let i = 0; i < steps; i += 1) {
      const a0 = start + ((end - start) * i) / steps;
      const a1 = start + ((end - start) * (i + 1)) / steps;
      const p = (a, r) => [Math.cos(a) * r, Math.sin(a) * r, 0];
      // two triangles: (in0, out0, out1) and (in0, out1, in1)
      positions.push(...p(a0, inner), ...p(a0, outer), ...p(a1, outer));
      positions.push(...p(a0, inner), ...p(a1, outer), ...p(a1, inner));
      edge.push(0, 1, 1, 0, 1, 0);
    }
  }
  return { positions: new Float32Array(positions), edge: new Float32Array(edge), mode: 'triangles' };
}

const STEP = TAU / 360;

/** A full thin ring. */
export function ring({ radius, width }) {
  return bandSegments([[0, TAU]], radius - width / 2, radius + width / 2, STEP);
}

/** `count` thick arcs, evenly spaced, with `gap` (fraction of each slot) left empty. */
export function arcs({ radius, width, count, gap }, rand = random(1)) {
  const slot = TAU / count;
  const spans = [];
  for (let i = 0; i < count; i += 1) {
    // Slightly uneven lengths so it reads as machinery, not a pie chart.
    const length = slot * (1 - gap) * (0.75 + rand() * 0.25);
    const start = i * slot + rand() * (slot * (1 - gap) - length);
    spans.push([start, start + length]);
  }
  return bandSegments(spans, radius - width / 2, radius + width / 2, STEP);
}

/** A dashed ring: `count` dashes, each `duty` of its slot. */
export function dashes({ radius, width, count, duty }) {
  const slot = TAU / count;
  const spans = Array.from({ length: count }, (_, i) => [i * slot, i * slot + slot * duty]);
  return bandSegments(spans, radius - width / 2, radius + width / 2, STEP);
}

function lines(points) {
  const positions = new Float32Array(points);
  return { positions, edge: new Float32Array(positions.length / 3).fill(0.5), mode: 'lines' };
}

/** Dial ticks: every `major`th tick is twice as long. */
export function ticks({ radius, length, count, major }) {
  const points = [];
  for (let i = 0; i < count; i += 1) {
    const a = (i / count) * TAU;
    const len = major && i % major === 0 ? length * 2 : length;
    const c = Math.cos(a);
    const s = Math.sin(a);
    points.push(c * radius, s * radius, 0, c * (radius + len), s * (radius + len), 0);
  }
  return lines(points);
}

/** Thin spokes of random length in the outer annulus. */
export function radials({ inner, outer, count }, rand = random(2)) {
  const points = [];
  for (let i = 0; i < count; i += 1) {
    const a = rand() * TAU;
    const r0 = inner + rand() * (outer - inner) * 0.6;
    const r1 = Math.min(outer, r0 + 0.03 + rand() * (outer - inner) * 0.5);
    points.push(Math.cos(a) * r0, Math.sin(a) * r0, 0, Math.cos(a) * r1, Math.sin(a) * r1, 0);
  }
  return lines(points);
}

function rect(points, cx, cy, w, h, angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  const corner = (x, y) => [cx + x * c - y * s, cy + x * s + y * c, 0];
  const p = [corner(-w / 2, -h / 2), corner(w / 2, -h / 2), corner(w / 2, h / 2), corner(-w / 2, h / 2)];
  for (let i = 0; i < 4; i += 1) points.push(...p[i], ...p[(i + 1) % 4]);
}

/** Small rectangle outlines, aligned with the ring they sit on. */
export function blocks({ inner, outer, count }, rand = random(3)) {
  const points = [];
  for (let i = 0; i < count; i += 1) {
    const a = rand() * TAU;
    const r = inner + rand() * (outer - inner);
    rect(points, Math.cos(a) * r, Math.sin(a) * r, 0.015 + rand() * 0.045, 0.006 + rand() * 0.014, a + Math.PI / 2);
  }
  return lines(points);
}

/**
 * Tiny unreadable "data readouts": clusters of a few rows of glyph-like strokes, upright on
 * screen like real HUD text. Purely decorative: there is nothing to read.
 */
export function text({ inner, outer, count }, rand = random(4)) {
  const points = [];
  const glyphW = 0.0075;
  const glyphH = 0.011;
  for (let i = 0; i < count; i += 1) {
    const a = rand() * TAU;
    const r = inner + rand() * (outer - inner);
    const x0 = Math.cos(a) * r;
    const y0 = Math.sin(a) * r;
    const rows = 2 + Math.floor(rand() * 4);
    for (let row = 0; row < rows; row += 1) {
      const glyphs = 3 + Math.floor(rand() * 9);
      const y = y0 - row * glyphH * 1.7;
      for (let g = 0; g < glyphs; g += 1) {
        const x = x0 + g * glyphW * 1.45;
        const strokes = 1 + Math.floor(rand() * 3);
        for (let k = 0; k < strokes; k += 1) {
          if (rand() < 0.5) {
            const yy = y + Math.floor(rand() * 3) * (glyphH / 2);
            points.push(x, yy, 0, x + glyphW, yy, 0);
          } else {
            const xx = x + Math.floor(rand() * 2) * glyphW;
            points.push(xx, y, 0, xx, y + glyphH * (rand() < 0.5 ? 0.5 : 1), 0);
          }
        }
      }
    }
  }
  return lines(points);
}

/** Lens-flare hot spots: one quad per flare, with `aUv` in -1..1 for a procedural glow. */
export function flares({ radius, count, size = 0.16 }, rand = random(5)) {
  const positions = [];
  const uv = [];
  for (let i = 0; i < count; i += 1) {
    const a = rand() * TAU;
    const cx = Math.cos(a) * radius;
    const cy = Math.sin(a) * radius;
    const h = size / 2;
    const corners = [
      [-1, -1],
      [1, -1],
      [1, 1],
      [-1, -1],
      [1, 1],
      [-1, 1],
    ];
    for (const [u, v] of corners) {
      positions.push(cx + u * h, cy + v * h, 0);
      uv.push(u, v);
    }
  }
  return { positions: new Float32Array(positions), uv: new Float32Array(uv), mode: 'triangles' };
}

/** Points scattered through an annulus and a slab of depth; `aSeed` drives their drift. */
export function particles({ count, inner, outer, depth }, rand = random(6)) {
  const positions = new Float32Array(count * 3);
  const seed = new Float32Array(count);
  const offset = new Float32Array(count);
  for (let i = 0; i < count; i += 1) {
    const a = rand() * TAU;
    positions.set([Math.cos(a), Math.sin(a), (rand() - 0.5) * depth], i * 3);
    seed[i] = rand();
  }
  return { positions, seed, offset, inner, outer, mode: 'points' };
}

/** Short radial line fragments; both ends share a seed so they drift outward together. */
export function fragments({ count, inner, outer, length, depth }, rand = random(7)) {
  const positions = new Float32Array(count * 6);
  const seed = new Float32Array(count * 2);
  const offset = new Float32Array(count * 2);
  for (let i = 0; i < count; i += 1) {
    const a = rand() * TAU;
    const z = (rand() - 0.5) * depth;
    const s = rand();
    const len = length * (0.4 + rand() * 0.9);
    positions.set([Math.cos(a), Math.sin(a), z, Math.cos(a), Math.sin(a), z], i * 6);
    seed.set([s, s], i * 2);
    offset.set([0, len], i * 2);
  }
  return { positions, seed, offset, inner, outer, mode: 'lines' };
}

export const GENERATORS = { ring, arcs, dashes, ticks, radials, blocks, text, flares };

/** Scale count-based layers for compact mode (never below 1). */
export function scaledSpec(spec, factor) {
  if (factor === 1 || !('count' in spec) || ['ring', 'arcs', 'dashes', 'ticks', 'flares'].includes(spec.kind)) {
    return spec;
  }
  return { ...spec, count: Math.max(1, Math.round(spec.count * factor)) };
}
