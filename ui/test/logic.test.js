// Pure logic of the HUD: visual states, easing, transcript merging, shortcuts.
import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  ERROR_HOLD_MS,
  LevelEnvelope,
  STATE_PARAMS,
  easeParams,
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

test('brief: white aura on mic while listening, cyan aura on TTS while speaking', () => {
  assert.deepEqual(STATE_PARAMS.listening.auraColor, [1, 1, 1]);
  assert.ok(STATE_PARAMS.listening.auraIntensity > 0 && STATE_PARAMS.listening.levelGain > 0);
  const [r, g, b] = STATE_PARAMS.speaking.auraColor;
  assert.ok(g > r && b > r, 'speaking aura is cyan');
  assert.equal(STATE_PARAMS.thinking.chase, 1);
  assert.equal(STATE_PARAMS.thinking.auraIntensity, 0, 'no aura ripple while thinking');
  assert.ok(STATE_PARAMS.muted.saturation < 0.3, 'muted is desaturated');
});

test('easing moves part of the way and never jumps', () => {
  const next = easeParams(STATE_PARAMS.idle, STATE_PARAMS.speaking, 0.5);
  const mid = (STATE_PARAMS.idle.coreIntensity + STATE_PARAMS.speaking.coreIntensity) / 2;
  assert.ok(Math.abs(next.coreIntensity - mid) < 1e-9);
  assert.equal(next.auraColor.length, 3);
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
