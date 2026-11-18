// The Stop button and Esc share one stop path; feedback is immediate and honest.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';

import { createProtocol } from '../src/protocol.js';
import { shortcutFor } from '../src/shortcuts.js';
import { FLASH_MS, StopControl, describeStop, isStoppable } from '../src/stop.js';

const schema = JSON.parse(fs.readFileSync(new URL('../../protocol/events.schema.json', import.meta.url), 'utf8'));

class FakeButton {
  constructor() {
    this.dataset = {};
    this.listeners = {};
    this.labelNode = { textContent: 'Stop' };
  }
  querySelector(selector) {
    return selector === '.label' ? this.labelNode : null;
  }
  addEventListener(type, fn) {
    this.listeners[type] = fn;
  }
  click() {
    this.listeners.click();
  }
}

function makeControl({ connected = true } = {}) {
  const sent = [];
  const timers = [];
  const button = new FakeButton();
  const control = new StopControl(button, {
    send: () => {
      if (connected) sent.push('interrupt');
      return connected;
    },
    setTimer: (fn, ms) => {
      timers.push({ fn, ms });
      return timers.length;
    },
  });
  return { control, button, sent, timers };
}

test('the stop command and acknowledgement event exist in the shared protocol', () => {
  const protocol = createProtocol(schema);
  assert.ok(protocol.commandTypes.has('interrupt'));
  assert.ok(protocol.eventTypes.has('stopped'));
});

test('clicking Stop sends interrupt and flashes "Stopped" at once', () => {
  const { button, sent, timers } = makeControl();
  button.click();
  assert.deepEqual(sent, ['interrupt']);
  assert.equal(button.dataset.flash, 'true');
  assert.equal(button.labelNode.textContent, 'Stopped');
  assert.equal(timers[0].ms, FLASH_MS);
  timers[0].fn();
  assert.equal(button.dataset.flash, 'false');
  assert.equal(button.labelNode.textContent, 'Stop');
});

test('Esc takes the same path as the button', () => {
  // main.js routes both 'interrupt' and 'blur' (Esc while typing) to StopControl.stop().
  const ctx = { typing: false, pttHeld: false, voice: true };
  assert.equal(shortcutFor({ type: 'keydown', code: 'Escape' }, ctx), 'interrupt');
  assert.equal(shortcutFor({ type: 'keydown', code: 'Escape' }, { ...ctx, typing: true }), 'blur');
  const { control, sent } = makeControl();
  control.stop();
  control.stop();
  assert.deepEqual(sent, ['interrupt', 'interrupt']);
});

test('no "Stopped" flash when the command could not be sent', () => {
  const { button, sent } = makeControl({ connected: false });
  button.click();
  assert.deepEqual(sent, []);
  assert.equal(button.dataset.flash, undefined);
});

test('the button is highlighted only while there is something to stop', () => {
  const { control, button } = makeControl();
  for (const state of ['listening', 'thinking', 'speaking']) {
    control.setState(state);
    assert.equal(button.dataset.active, 'true', state);
    assert.ok(isStoppable(state));
  }
  for (const state of ['idle', 'muted']) {
    control.setState(state);
    assert.equal(button.dataset.active, 'false', state);
  }
});

test('the acknowledgement says what was stopped, and nothing when idle', () => {
  assert.equal(describeStop(['speech', 'reply', 'tools']), '■ Stopped: speech, reply, actions');
  assert.equal(describeStop(['transcription']), '■ Stopped: transcription');
  assert.equal(describeStop([]), null);
});
