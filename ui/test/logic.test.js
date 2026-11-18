// Pure logic of the HUD: visual states, easing, transcript merging, shortcuts.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ERROR_HOLD_MS,
  LevelEnvelope,
  PALETTES,
  STATE_PARAMS,
  THEME,
  easeParams,
  fadeAmount,
  visualState,
} from '../src/hologram/states.js';
import { TranscriptModel } from '../src/panels/transcript-model.js';
import { shortcutFor } from '../src/shortcuts.js';

test('every visual state defines the same parameters (so easing is always defined)', () => {
  const keys = Object.keys(STATE_PARAMS.idle).sort();
  for (const [name, params] of Object.entries(STATE_PARAMS)) {
    assert.deepEqual(Object.keys(params).sort(), keys, name);
  }
});

test('blue while listening, orange while speaking, both driven by the voice level', () => {
  assert.equal(STATE_PARAMS.listening.orange, 0);
  assert.equal(STATE_PARAMS.speaking.orange, 1);
  for (const state of ['listening', 'speaking']) {
    assert.ok(STATE_PARAMS[state].levelGain > 0 && STATE_PARAMS[state].pulse > 0, state);
  }
  assert.equal(STATE_PARAMS.thinking.chase, 1);
  assert.equal(STATE_PARAMS.thinking.levelGain, 0, 'no audio reaction while thinking');
  assert.ok(STATE_PARAMS.thinking.spin > STATE_PARAMS.listening.spin, 'thinking rotates faster');
  assert.ok(STATE_PARAMS.idle.twoTone > 0 && STATE_PARAMS.idle.intensity < STATE_PARAMS.listening.intensity);
  assert.ok(STATE_PARAMS.muted.saturation < 0.3, 'muted is desaturated');
  assert.ok(STATE_PARAMS.offline.intensity < 0.2 && STATE_PARAMS.offline.spin < 0.1, 'offline is dim, near static');
});

test('error is red and flickers, clearly unlike orange speaking', () => {
  assert.equal(STATE_PARAMS.error.error, 1);
  assert.equal(STATE_PARAMS.error.orange, 0);
  assert.ok(STATE_PARAMS.error.flicker > 0 && STATE_PARAMS.speaking.flicker === 0);
  assert.deepEqual(PALETTES.red.mid.map((v) => Math.round(v * 255)), [255, 42, 42]);
});

test('palettes match the design: white-hot core to saturated edge', () => {
  const rgb = (c) => c.map((v) => Math.round(v * 255));
  assert.deepEqual(rgb(PALETTES.blue.core), [0xe8, 0xfb, 0xff]);
  assert.deepEqual(rgb(PALETTES.blue.mid), [0x00, 0xe5, 0xff]);
  assert.deepEqual(rgb(PALETTES.blue.edge), [0x0a, 0x6c, 0xff]);
  assert.deepEqual(rgb(PALETTES.orange.core), [0xff, 0xf1, 0xd6]);
  assert.deepEqual(rgb(PALETTES.orange.mid), [0xff, 0x9a, 0x2e]);
  assert.deepEqual(rgb(PALETTES.orange.edge), [0xff, 0x3d, 0x00]);
});

test('state cross-fades take about 400 ms and never snap', () => {
  assert.equal(THEME.fadeMs, 400);
  const first = fadeAmount(1 / 60);
  assert.ok(first > 0 && first < 0.2, 'one frame moves only part of the way');
  let progress = 0;
  for (let t = 0; t < 0.4; t += 1 / 60) progress += (1 - progress) * fadeAmount(1 / 60);
  assert.ok(progress > 0.93 && progress < 0.99, String(progress));
});

test('easing moves part of the way and never jumps', () => {
  const next = easeParams(STATE_PARAMS.idle, STATE_PARAMS.speaking, 0.5);
  const mid = (STATE_PARAMS.idle.intensity + STATE_PARAMS.speaking.intensity) / 2;
  assert.ok(Math.abs(next.intensity - mid) < 1e-9);
});

test('visual state: offline when disconnected, error held briefly, else the backend state', () => {
  assert.equal(visualState({ connected: false, state: 'speaking', errorSince: null, now: 0 }), 'offline');
  assert.equal(visualState({ connected: true, state: 'speaking', errorSince: 0, now: 100 }), 'error');
  assert.equal(visualState({ connected: true, state: 'idle', errorSince: 0, now: ERROR_HOLD_MS + 1 }), 'idle');
  assert.equal(visualState({ connected: true, state: 'nonsense', errorSince: null, now: 0 }), 'idle');
});

test('level envelope: fast attack, slow release', () => {
  const env = new LevelEnvelope();
  const up = env.update(1, 1 / 60);
  const down = env.update(0, 1 / 60);
  assert.ok(up > 0.5);
  assert.ok(down > up * 0.8, 'releases slowly');
});

test('transcript: partial updates replace, reused ids after final start a new line', () => {
  const model = new TranscriptModel({ limit: 3 });
  model.applyTranscript({ turn_id: 'a', role: 'assistant', text: 'Hel', language: 'en', final: false });
  model.applyTranscript({ turn_id: 'a', role: 'assistant', text: 'Hello.', language: 'en', final: true });
  assert.equal(model.entries.length, 1);
  assert.equal(model.entries[0].text, 'Hello.');
  model.applyTranscript({ turn_id: 'answer', role: 'user', text: 'yes', language: 'en', final: true });
  model.applyTranscript({ turn_id: 'answer', role: 'user', text: 'no', language: 'en', final: true });
  assert.equal(model.entries.length, 3);
  model.addSystem('▸ Opening VS Code.');
  assert.equal(model.entries.length, 3, 'limit keeps the newest');
  assert.equal(model.lastAssistantText(), '');
});

test('shortcuts: Space hold to talk, M mutes, Esc interrupts; typing is respected', () => {
  const ctx = { typing: false, pttHeld: false, voice: true };
  assert.equal(shortcutFor({ type: 'keydown', code: 'Space' }, ctx), 'ptt_start');
  assert.equal(shortcutFor({ type: 'keydown', code: 'Space', repeat: true }, ctx), null);
  assert.equal(shortcutFor({ type: 'keyup', code: 'Space' }, { ...ctx, pttHeld: true }), 'ptt_stop');
  assert.equal(shortcutFor({ type: 'keydown', code: 'Space' }, { ...ctx, typing: true }), null);
  assert.equal(shortcutFor({ type: 'keydown', code: 'Space' }, { ...ctx, voice: false }), null);
  assert.equal(shortcutFor({ type: 'keydown', code: 'KeyM' }, ctx), 'mute_toggle');
  assert.equal(shortcutFor({ type: 'keydown', code: 'KeyM' }, { ...ctx, typing: true }), null);
  assert.equal(shortcutFor({ type: 'keydown', code: 'Escape' }, ctx), 'interrupt');
  assert.equal(shortcutFor({ type: 'keydown', code: 'Escape' }, { ...ctx, typing: true }), 'blur');
  assert.equal(shortcutFor({ type: 'keydown', code: 'KeyM', ctrlKey: true }, ctx), null);
});
