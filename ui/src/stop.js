// The Stop button. It shares one path with Esc: both call StopControl.stop(), which sends the
// `interrupt` command (the backend's single stop path) and shows feedback straight away, before
// the backend's `stopped` acknowledgement arrives. The text helpers are pure, for tests.

/** Shown on the button while Jarvis is doing something it can stop. */
export const ACTIVE_STATES = new Set(['listening', 'thinking', 'speaking', 'error']);

/** How long the button keeps its "Stopped" flash. */
export const FLASH_MS = 1200;

const LABELS = {
  speech: 'speech',
  listening: 'listening',
  transcription: 'transcription',
  reply: 'reply',
  tools: 'actions',
};

/**
 * Transcript line for a backend `stopped` event, or null when nothing was running.
 * @param {string[]} cancelled
 */
export function describeStop(cancelled) {
  if (!cancelled || cancelled.length === 0) return null;
  return `■ Stopped: ${cancelled.map((item) => LABELS[item] ?? item).join(', ')}`;
}

/** @param {string} state the backend state */
export function isStoppable(state) {
  return ACTIVE_STATES.has(state);
}

export class StopControl {
  /**
   * @param {HTMLButtonElement} button
   * @param {{send: () => boolean, setTimer?: typeof setTimeout}} deps
   */
  constructor(button, { send, setTimer = setTimeout }) {
    this.button = button;
    this.send = send;
    this.setTimer = setTimer;
    this.label = button.querySelector('.label') ?? button;
    this.timer = null;
    button.addEventListener('click', () => this.stop());
  }

  /** The one stop path for the button and Esc. */
  stop() {
    if (!this.send()) return;
    this.button.dataset.flash = 'true';
    this.label.textContent = 'Stopped';
    if (this.timer) clearTimeout(this.timer);
    this.timer = this.setTimer(() => {
      this.button.dataset.flash = 'false';
      this.label.textContent = 'Stop';
      this.timer = null;
    }, FLASH_MS);
  }

  /** Highlight the button while there is something to stop. */
  setState(state) {
    this.button.dataset.active = String(isStoppable(state));
  }
}
