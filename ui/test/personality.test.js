// The personality picker's pure helpers and the protocol it relies on.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';

import { MODES, personalityCommand, personalityLabel } from '../src/panels/personality.js';
import { createProtocol } from '../src/protocol.js';

const schema = JSON.parse(fs.readFileSync(new URL('../../protocol/events.schema.json', import.meta.url), 'utf8'));

test('the five modes, Normal first', () => {
  assert.deepEqual(MODES.map((m) => m.id), ['normal', 'witty', 'evil', 'joyful', 'lusty']);
});

test('status-bar label shows the intensity, except for Normal', () => {
  assert.equal(personalityLabel({ mode: 'evil', intensity: 80 }), 'Evil 80%');
  assert.equal(personalityLabel({ mode: 'normal', intensity: 80 }), 'Normal');
});

test('commands are clamped to 0-100 and match the protocol', () => {
  assert.deepEqual(personalityCommand('witty', 140), { mode: 'witty', intensity: 100 });
  assert.deepEqual(personalityCommand('joyful', -3), { mode: 'joyful', intensity: 0 });
  assert.deepEqual(personalityCommand('lusty', '42.6'), { mode: 'lusty', intensity: 43 });
  const protocol = createProtocol(schema);
  assert.ok(protocol.commandTypes.has('personality_set'));
  assert.ok(protocol.eventTypes.has('personality'));
  assert.ok(protocol.eventTypes.has('vision') && protocol.commandTypes.has('vision_set'));
});
