import assert from 'node:assert/strict';
import fs from 'node:fs';
import { test } from 'node:test';

import { createProtocol } from '../src/protocol.js';
import { ConnectionState, JarvisSocket } from '../src/ws-client.js';

const schema = JSON.parse(fs.readFileSync(new URL('../../protocol/events.schema.json', import.meta.url), 'utf8'));
const protocol = createProtocol(schema);

class FakeWebSocket {
  static instances = [];
  constructor(url) {
    this.url = url;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }
  send(data) {
    this.sent.push(JSON.parse(data));
  }
  close() {
    this.onclose?.();
  }
}

function rig(backendInfos) {
  FakeWebSocket.instances = [];
  const timers = [];
  const events = [];
  const states = [];
  const infos = [...backendInfos];
  const socket = new JarvisSocket({
    getBackend: async () => infos.shift() ?? { status: 'offline' },
    protocol,
    onEvent: (e) => events.push(e),
    onConnection: (s, detail) => states.push([s, detail]),
    WebSocketImpl: FakeWebSocket,
    setTimer: (fn, ms) => timers.push({ fn, ms }),
    clearTimer: () => {},
  });
  return { socket, timers, events, states };
}

const tick = () => new Promise((resolve) => setImmediate(resolve));

test('authenticates first, then delivers events', async () => {
  const r = rig([{ status: 'ready', port: 5555, token: 'secret-token-123456' }]);
  r.socket.start();
  await tick();
  const ws = FakeWebSocket.instances[0];
  assert.equal(ws.url, 'ws://127.0.0.1:5555');
  ws.onopen();
  assert.deepEqual(ws.sent[0], { v: 2, type: 'auth', token: 'secret-token-123456' });
  assert.equal(r.socket.state, ConnectionState.CONNECTED);

  ws.onmessage({ data: '{"v":2,"type":"state_changed","state":"thinking"}' });
  ws.onmessage({ data: '{"v":9,"type":"state_changed","state":"idle"}' }); // wrong version: dropped
  assert.deepEqual(r.events.map((e) => e.state), ['thinking']);

  assert.equal(r.socket.send('interrupt'), true);
  assert.deepEqual(ws.sent[1], { v: 2, type: 'interrupt' });
});

test('retries with backoff while the backend is starting, using fresh port and token', async () => {
  const r = rig([{ status: 'starting' }, { status: 'ready', port: 6000, token: 'new-token-abcdefgh' }]);
  r.socket.start();
  await tick();
  assert.equal(r.socket.state, ConnectionState.OFFLINE);
  assert.equal(r.timers[0].ms, 250);
  r.timers[0].fn();
  await tick();
  assert.equal(FakeWebSocket.instances[0].url, 'ws://127.0.0.1:6000');
});

test('a dropped connection reconnects; sending while offline reports false', async () => {
  const r = rig([
    { status: 'ready', port: 5555, token: 't'.repeat(20) },
    { status: 'ready', port: 5556, token: 'u'.repeat(20) },
  ]);
  r.socket.start();
  await tick();
  FakeWebSocket.instances[0].onopen();
  FakeWebSocket.instances[0].onclose();
  assert.equal(r.socket.state, ConnectionState.OFFLINE);
  assert.equal(r.socket.send('interrupt'), false);
  r.timers.at(-1).fn();
  await tick();
  assert.equal(FakeWebSocket.instances[1].url, 'ws://127.0.0.1:5556');
});

test('a failed backend is reported without retry loops', async () => {
  const r = rig([{ status: 'failed', message: 'ANTHROPIC_API_KEY is not set' }]);
  r.socket.start();
  await tick();
  assert.deepEqual(r.states.at(-1), [ConnectionState.FAILED, 'ANTHROPIC_API_KEY is not set']);
  assert.equal(r.timers.length, 0);
});
