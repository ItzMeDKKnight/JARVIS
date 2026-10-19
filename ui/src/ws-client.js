// Reconnecting WebSocket client for the backend.
//
// Each attempt asks Electron for the current backend info, because a restarted backend
// has a new port and a new token. The first message on every connection is `auth`.
// Everything that touches the outside world (WebSocket, timers, backend info) is
// injected, so the reconnect logic is unit-tested in Node (ui/test).

export const ConnectionState = Object.freeze({
  CONNECTING: 'connecting',
  CONNECTED: 'connected',
  OFFLINE: 'offline', // backend not reachable right now; retrying
  FAILED: 'failed', // backend reported a problem it can't recover from (e.g. no API key)
});

export const DEFAULT_BACKOFF_MS = [250, 500, 1000, 2000, 5000];

export class JarvisSocket {
  /**
   * @param {object} options
   * @param {() => Promise<any>} options.getBackend   resolves {status, port, token, message}
   * @param {ReturnType<import('./protocol.js').createProtocol>} options.protocol
   * @param {(event: any) => void} options.onEvent
   * @param {(state: string, detail?: string) => void} options.onConnection
   * @param {typeof WebSocket} [options.WebSocketImpl]
   * @param {(fn: () => void, ms: number) => any} [options.setTimer]
   * @param {(handle: any) => void} [options.clearTimer]
   * @param {number[]} [options.backoffMs]
   */
  constructor({
    getBackend,
    protocol,
    onEvent,
    onConnection,
    WebSocketImpl = globalThis.WebSocket,
    setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = (handle) => clearTimeout(handle),
    backoffMs = DEFAULT_BACKOFF_MS,
  }) {
    this.getBackend = getBackend;
    this.protocol = protocol;
    this.onEvent = onEvent;
    this.onConnection = onConnection;
    this.WebSocketImpl = WebSocketImpl;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.backoffMs = backoffMs;
    this.socket = null;
    this.attempt = 0;
    this.timer = null;
    this.stopped = false;
    this.state = null;
  }

  start() {
    this.stopped = false;
    this.connect();
  }

  stop() {
    this.stopped = true;
    this.clearTimer(this.timer);
    this.socket?.close();
  }

  /** Reconnect now (e.g. Electron says the backend just became ready). */
  retryNow() {
    if (this.state === ConnectionState.CONNECTED || this.stopped) return;
    this.clearTimer(this.timer);
    this.attempt = 0;
    this.connect();
  }

  /** Send a command; returns false if not connected (the caller may show that). */
  send(type, fields) {
    if (this.state !== ConnectionState.CONNECTED || !this.socket) return false;
    this.socket.send(this.protocol.command(type, fields));
    return true;
  }

  setState(state, detail) {
    this.state = state;
    this.onConnection(state, detail);
  }

  async connect() {
    if (this.stopped) return;
    this.setState(ConnectionState.CONNECTING);
    let info;
    try {
      info = await this.getBackend();
    } catch {
      info = { status: 'offline' };
    }
    if (info.status === 'failed') {
      this.setState(ConnectionState.FAILED, info.message);
      return; // Electron sends a new status if the backend comes back
    }
    if (info.status !== 'ready') {
      this.scheduleRetry(info.message);
      return;
    }
    const socket = new this.WebSocketImpl(`ws://127.0.0.1:${info.port}`);
    this.socket = socket;
    socket.onopen = () => {
      socket.send(this.protocol.command('auth', { token: info.token }));
      this.attempt = 0;
      this.setState(ConnectionState.CONNECTED);
    };
    socket.onmessage = (message) => {
      const event = this.protocol.parseEvent(message.data);
      if (event) this.onEvent(event);
    };
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.scheduleRetry();
    };
    socket.onerror = () => {
      // onclose follows and handles the retry.
    };
  }

  scheduleRetry(detail) {
    if (this.stopped) return;
    this.setState(ConnectionState.OFFLINE, detail);
    const delay = this.backoffMs[Math.min(this.attempt, this.backoffMs.length - 1)];
    this.attempt += 1;
    this.timer = this.setTimer(() => this.connect(), delay);
  }
}
