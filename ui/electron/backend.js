// Starts and supervises the Python backend (`python -m jarvis --serve`).
//
// * A fresh random token is generated for every launch and handed over in the
//   JARVIS_HUD_TOKEN environment variable (never on the command line, where other
//   processes could read it).
// * The backend binds 127.0.0.1 on a free port and prints `JARVIS_READY {"port": N}`.
// * Stopping closes the backend's stdin: it treats that as "shut down", saves its
//   last memory summary and cost log, and exits. It is killed only if it hangs.
// * Crashes restart it with backoff. Exit code 2 (configuration problem, such as a
//   missing API key) is not retried: the HUD shows the message instead.

import { spawn } from 'node:child_process';
import crypto from 'node:crypto';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import path from 'node:path';

export const READY_PREFIX = 'JARVIS_READY ';
const CONFIG_PROBLEM_EXIT = 2;
const STDERR_LINES_KEPT = 20;
const STOP_GRACE_MS = 15_000;
const RESTART_DELAYS_MS = [1_000, 2_000, 5_000, 10_000, 30_000];

export function defaultPython(repoRoot, platform = process.platform) {
  const venv = platform === 'win32'
    ? path.join(repoRoot, '.venv', 'Scripts', 'python.exe')
    : path.join(repoRoot, '.venv', 'bin', 'python');
  return fs.existsSync(venv) ? venv : 'python';
}

/** Parse one stdout line; returns {port, protocol} for the ready line, else null. */
export function parseReadyLine(line) {
  if (!line.startsWith(READY_PREFIX)) return null;
  try {
    const info = JSON.parse(line.slice(READY_PREFIX.length));
    if (Number.isInteger(info.port) && info.port > 0 && info.port < 65536) {
      return { port: info.port, protocol: info.protocol };
    }
  } catch {
    // fall through
  }
  return null;
}

export class Backend extends EventEmitter {
  constructor({ repoRoot, python, args, env = process.env }) {
    super();
    this.repoRoot = repoRoot;
    this.python = python ?? defaultPython(repoRoot);
    this.args = args ?? ['-m', 'jarvis', '--serve'];
    this.env = env;
    this.child = null;
    this.stopping = false;
    this.restarts = 0;
    this.info = { status: 'starting' };
    this.stderrTail = [];
  }

  start() {
    this.stopping = false;
    this._spawn();
  }

  _setInfo(info) {
    this.info = info;
    this.emit('info', info);
  }

  _spawn() {
    const token = crypto.randomBytes(32).toString('hex');
    this.stderrTail = [];
    this._setInfo({ status: 'starting' });
    const child = spawn(this.python, this.args, {
      cwd: this.repoRoot,
      env: {
        ...this.env,
        JARVIS_HUD_TOKEN: token,
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8',
      },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    });
    this.child = child;

    let buffered = '';
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (chunk) => {
      buffered += chunk;
      let newline;
      while ((newline = buffered.indexOf('\n')) >= 0) {
        const line = buffered.slice(0, newline).trim();
        buffered = buffered.slice(newline + 1);
        const ready = parseReadyLine(line);
        if (ready) {
          this.restarts = 0;
          this._setInfo({ status: 'ready', port: ready.port, token, protocol: ready.protocol });
        }
      }
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (chunk) => {
      for (const line of chunk.split(/\r?\n/)) {
        if (line.trim()) this.stderrTail.push(line.trim());
      }
      this.stderrTail = this.stderrTail.slice(-STDERR_LINES_KEPT);
    });
    child.on('error', (error) => {
      this._setInfo({ status: 'failed', message: `Could not start Python: ${error.message}` });
    });
    child.on('exit', (code) => this._onExit(child, code));
  }

  _onExit(child, code) {
    if (child !== this.child) return;
    this.child = null;
    if (this.stopping) {
      this._setInfo({ status: 'stopped' });
      this.emit('stopped');
      return;
    }
    const reason = this.stderrTail.at(-1) ?? `exit code ${code}`;
    if (code === CONFIG_PROBLEM_EXIT) {
      this._setInfo({ status: 'failed', message: reason });
      return;
    }
    const delay = RESTART_DELAYS_MS[Math.min(this.restarts, RESTART_DELAYS_MS.length - 1)];
    this.restarts += 1;
    this._setInfo({ status: 'offline', message: reason, retryInMs: delay });
    this.restartTimer = setTimeout(() => this._spawn(), delay);
  }

  /** Ask the backend to exit cleanly; resolves once it has (or was killed). */
  stop() {
    this.stopping = true;
    clearTimeout(this.restartTimer);
    const child = this.child;
    if (!child) return Promise.resolve();
    return new Promise((resolve) => {
      const killTimer = setTimeout(() => child.kill(), STOP_GRACE_MS);
      child.once('exit', () => {
        clearTimeout(killTimer);
        resolve();
      });
      child.stdin.end(); // the backend's stdin watcher starts a clean shutdown
    });
  }
}
