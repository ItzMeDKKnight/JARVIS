// The lens HUD's procedural geometry: shapes, counts, determinism and compact scaling.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { GENERATORS, arcs, fragments, particles, random, ring, scaledSpec, text, ticks } from '../src/hologram/geometry.js';
import { LAYERS, MOTION, STATE_PARAMS, THEME } from '../src/hologram/states.js';

const radiusOf = (positions, i) => Math.hypot(positions[i * 3], positions[i * 3 + 1]);

test('a ring is a band of the requested radius and width, edge 0 inside, 1 outside', () => {
  const g = ring({ radius: 0.5, width: 0.02 });
  assert.equal(g.positions.length % 9, 0, 'whole triangles');
  for (let i = 0; i < g.positions.length / 3; i += 1) {
    const r = radiusOf(g.positions, i);
    assert.ok(Math.abs(r - (g.edge[i] === 0 ? 0.49 : 0.51)) < 1e-5);
  }
});

test('segmented arcs leave gaps', () => {
  const g = arcs({ radius: 0.5, width: 0.05, count: 4, gap: 0.3 }, random(1));
  let covered = 0;
  for (let i = 0; i < g.positions.length; i += 18) {
    const a0 = Math.atan2(g.positions[i + 1], g.positions[i]);
    const a1 = Math.atan2(g.positions[i + 7], g.positions[i + 6]);
    covered += Math.abs(((a1 - a0 + 3 * Math.PI) % (2 * Math.PI)) - Math.PI);
  }
  assert.ok(covered < 2 * Math.PI * 0.75, `arcs cover ${covered}`);
});

test('ticks: one segment each, majors twice as long', () => {
  const g = ticks({ radius: 1, length: 0.1, count: 12, major: 3 });
  assert.equal(g.positions.length, 12 * 6);
  const len = (i) => radiusOf(g.positions, i * 2 + 1) - radiusOf(g.positions, i * 2);
  assert.ok(Math.abs(len(0) - 0.2) < 1e-6 && Math.abs(len(1) - 0.1) < 1e-6);
});

test('layouts are deterministic (same seed, same picture)', () => {
  const a = text({ inner: 1, outer: 1.4, count: 5 }, random(4));
  const b = text({ inner: 1, outer: 1.4, count: 5 }, random(4));
  assert.deepEqual(a.positions, b.positions);
});

test('particles and fragments carry drift seeds; fragment ends share one', () => {
  const p = particles({ count: 10, inner: 1, outer: 2, depth: 1 });
  assert.equal(p.seed.length, 10);
  const f = fragments({ count: 5, inner: 1, outer: 2, length: 0.05, depth: 1 });
  for (let i = 0; i < 5; i += 1) assert.equal(f.seed[i * 2], f.seed[i * 2 + 1]);
});

test('every layer in states.js has a generator, valid numbers and builds', () => {
  for (const spec of LAYERS) {
    assert.ok(GENERATORS[spec.kind], spec.kind);
    assert.equal(typeof spec.z, 'number');
    const data = GENERATORS[spec.kind](spec, random(9));
    assert.ok(data.positions.length > 0 && data.positions.every(Number.isFinite), spec.kind);
  }
});

test('compact mode drops detail: fewer layers and fewer decorative items', () => {
  const compactLayers = LAYERS.filter((spec) => spec.compact !== false);
  assert.ok(compactLayers.length < LAYERS.length);
  const radialsSpec = LAYERS.find((spec) => spec.kind === 'radials');
  assert.ok(scaledSpec(radialsSpec, THEME.compactCounts).count < radialsSpec.count);
  const ringSpec = LAYERS.find((spec) => spec.kind === 'ticks');
  assert.equal(scaledSpec(ringSpec, THEME.compactCounts).count, ringSpec.count, 'dial marks keep their spacing');
});

test('reduced motion: slower, no camera drift, gentler pulses', () => {
  assert.ok(MOTION.reduced.speed < MOTION.normal.speed);
  assert.equal(MOTION.reduced.drift, 0);
  assert.ok(MOTION.reduced.pulse < MOTION.normal.pulse);
  assert.ok(Object.keys(STATE_PARAMS).length === 7);
});
