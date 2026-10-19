import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';

import { createProtocol } from '../src/protocol.js';

const schema = JSON.parse(fs.readFileSync(new URL('../../protocol/events.schema.json', import.meta.url), 'utf8'));
const protocol = createProtocol(schema);

test('version and message types come from the shared schema', () => {
  assert.equal(protocol.version, 2);
  for (const type of ['state_changed', 'audio_level', 'transcript', 'status', 'usage', 'memory']) {
    assert.ok(protocol.eventTypes.has(type), type);
  }
  assert.ok(protocol.commandTypes.has('auth'));
  assert.ok(protocol.commandTypes.has('memory_delete'));
});

test('parseEvent accepts known events of this version only', () => {
  assert.deepEqual(protocol.parseEvent('{"v":2,"type":"state_changed","state":"idle"}'), {
    v: 2,
    type: 'state_changed',
    state: 'idle',
  });
  assert.equal(protocol.parseEvent('{"v":1,"type":"state_changed","state":"idle"}'), null);
  assert.equal(protocol.parseEvent('{"v":2,"type":"run_code"}'), null);
  assert.equal(protocol.parseEvent('not json'), null);
});

test('command adds the version and rejects unknown types', () => {
  assert.equal(protocol.command('text_input', { text: 'hi' }), '{"v":2,"type":"text_input","text":"hi"}');
  assert.throws(() => protocol.command('run_shell'));
});
