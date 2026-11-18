// Electron-side helpers that don't need Electron: security rules, backend supervision, icon.
import assert from 'node:assert/strict';
import path from 'node:path';
import { test } from 'node:test';

import { Backend, parseReadyLine } from '../electron/backend.js';
import { reactorIconPixels } from '../electron/icon.js';
import {
  CONTENT_SECURITY_POLICY,
  isAllowedRequest,
  isSafeExternalUrl,
  isTrustedSender,
  resolveAppFile,
} from '../electron/security.js';

const ROOT = path.resolve('dist-test-root');

test('app:// URLs map inside dist only', () => {
  assert.equal(resolveAppFile('app://jarvis/', ROOT), path.join(ROOT, 'index.html'));
  assert.equal(resolveAppFile('app://jarvis/assets/a.js', ROOT), path.join(ROOT, 'assets', 'a.js'));
  assert.equal(resolveAppFile('app://jarvis/../secret.txt', ROOT), path.join(ROOT, 'secret.txt'));
  assert.equal(resolveAppFile('app://jarvis/%2e%2e%2f%2e%2e%2fwindows/win.ini', ROOT), null);
  assert.equal(resolveAppFile('app://jarvis/..%5C..%5Cwin.ini', ROOT), null);
  assert.equal(resolveAppFile('app://evil/index.html', ROOT), null);
  assert.equal(resolveAppFile('file:///C:/Windows/win.ini', ROOT), null);
});

test('only the backend WebSocket on loopback is reachable', () => {
  assert.ok(isAllowedRequest('ws://127.0.0.1:5000/', 5000));
  assert.ok(isAllowedRequest('app://jarvis/index.html', 5000));
  assert.ok(!isAllowedRequest('ws://127.0.0.1:5001/', 5000));
  assert.ok(!isAllowedRequest('ws://127.0.0.1:5000/', null));
  assert.ok(!isAllowedRequest('ws://localhost:5000/', 5000));
  assert.ok(!isAllowedRequest('https://fonts.googleapis.com/css', 5000));
  assert.ok(!isAllowedRequest('http://127.0.0.1:5000/', 5000));
  assert.ok(!isAllowedRequest('file:///C:/secret', 5000));
});

test('external links: http(s) only, no embedded credentials', () => {
  assert.ok(isSafeExternalUrl('https://example.com/a'));
  assert.ok(!isSafeExternalUrl('javascript:alert(1)'));
  assert.ok(!isSafeExternalUrl('file:///C:/x'));
  assert.ok(!isSafeExternalUrl('https://user:pw@example.com'));
});

test('IPC trusts only our page; CSP is strict', () => {
  assert.ok(isTrustedSender('app://jarvis/index.html'));
  assert.ok(!isTrustedSender('https://evil.example/'));
  assert.ok(!isTrustedSender(undefined));
  assert.match(CONTENT_SECURITY_POLICY, /default-src 'none'/);
  assert.match(CONTENT_SECURITY_POLICY, /connect-src ws:\/\/127\.0\.0\.1:\*/);
  assert.doesNotMatch(CONTENT_SECURITY_POLICY, /unsafe-(inline|eval)/);
});

test('ready line parsing', () => {
  assert.deepEqual(parseReadyLine('JARVIS_READY {"port": 51234, "protocol": 2}'), { port: 51234, protocol: 2 });
  assert.equal(parseReadyLine('Loading speech models...'), null);
  assert.equal(parseReadyLine('JARVIS_READY {"port": 99999}'), null);
  assert.equal(parseReadyLine('JARVIS_READY garbage'), null);
});

test('backend: ready with a fresh token, config problems are not retried', async () => {
  const script = [
    'import os, sys',
    'assert len(os.environ["JARVIS_HUD_TOKEN"]) == 64',
    'print("JARVIS_READY {\\"port\\": 4321, \\"protocol\\": 2}", flush=True)',
    'sys.stdin.read()',
  ].join('\n');
  const backend = new Backend({ repoRoot: process.cwd(), python: 'python', args: ['-c', script] });
  const ready = new Promise((resolve) => backend.on('info', (i) => i.status === 'ready' && resolve(i)));
  backend.start();
  const info = await ready;
  assert.equal(info.port, 4321);
  assert.equal(info.token.length, 64);
  await backend.stop(); // closing stdin ends the script cleanly
  assert.equal(backend.info.status, 'stopped');

  const failing = new Backend({
    repoRoot: process.cwd(),
    python: 'python',
    args: ['-c', 'import sys; print("Configuration problem: no key", file=sys.stderr); sys.exit(2)'],
  });
  const failed = new Promise((resolve) => failing.on('info', (i) => i.status === 'failed' && resolve(i)));
  failing.start();
  assert.equal((await failed).message, 'Configuration problem: no key');
});

test('tray icon is drawn in code: opaque ring, transparent corners', () => {
  const size = 16;
  const pixels = reactorIconPixels(size);
  assert.equal(pixels.length, size * size * 4);
  assert.equal(pixels[3], 0, 'corner is transparent');
  const centre = ((size / 2) * size + size / 2) * 4;
  assert.ok(pixels[centre + 3] > 200, 'core is opaque');
});

test('the HUD opens only PDFs inside the project Outputs folders', async () => {
  const { resolveOutputFile } = await import('../electron/security.js');
  const repo = path.resolve('repo-root-test');
  const ok = path.join(repo, 'Outputs', 'solar-system.pdf');
  assert.equal(resolveOutputFile(ok, repo), ok);
  assert.ok(resolveOutputFile(path.join(repo, 'Outputs_Jarvis', 'a.pdf'), repo));
  assert.ok(resolveOutputFile(path.join(repo, 'Outputs_Jarvis_2', 'a.pdf'), repo));
  for (const bad of [
    path.join(repo, 'Outputs', 'evil.exe'),
    path.join(repo, 'Outputs', 'doc.pdf.lnk'),
    path.join(repo, 'Outputs', '..', 'secret.pdf'),
    path.join(repo, 'Outputs', 'sub', 'a.pdf'),
    path.join(repo, 'Other', 'a.pdf'),
    path.win32.join('C:', 'Windows', 'System32', 'x.pdf'),
    path.join(repo, 'Outputs', 'a.pdf') + '\0.exe',
    42,
  ]) {
    assert.equal(resolveOutputFile(bad, repo), null, String(bad));
  }
});

test('the tray icon gets a red dot while screen vision is on', () => {
  const size = 32;
  const plain = reactorIconPixels(size);
  const vision = reactorIconPixels(size, { vision: true });
  const at = (pixels, x, y) => Array.from(pixels.subarray((y * size + x) * 4, (y * size + x) * 4 + 4));
  const corner = [Math.round(size * 0.78), Math.round(size * 0.78)];
  assert.deepEqual(at(vision, ...corner).slice(0, 3), [42, 42, 255]); // BGR red
  assert.notDeepEqual(at(plain, ...corner), at(vision, ...corner));
});
