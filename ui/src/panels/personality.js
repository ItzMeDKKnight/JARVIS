// Personality picker: a button in the status bar showing the current mode and intensity, and a
// popover with the five modes and a 0-100 slider (disabled for Normal). The backend holds the
// real setting (saved in settings.json); this only sends `personality_set` and shows what the
// backend reports back in `personality` events. Pure helpers are exported for tests.
import { el } from './dom.js';

export const MODES = [
  { id: 'normal', label: 'Normal', hint: 'Calm, dry, competent' },
  { id: 'witty', label: 'Witty', hint: 'Wordplay to relentless roasting' },
  { id: 'evil', label: 'Evil', hint: 'Theatrical villain to supervillain' },
  { id: 'joyful', label: 'Joyful', hint: 'Upbeat to over-the-top' },
  { id: 'lusty', label: 'Lusty', hint: 'Flirty to shameless innuendo' },
];

/** Status-bar text for a personality event. */
export function personalityLabel({ mode, intensity }) {
  const entry = MODES.find((m) => m.id === mode);
  const name = entry ? entry.label : mode;
  return mode === 'normal' ? name : `${name} ${intensity}%`;
}

/** What to send: intensity is clamped to 0-100 and rounded. */
export function personalityCommand(mode, intensity) {
  const value = Math.max(0, Math.min(100, Math.round(Number(intensity) || 0)));
  return { mode, intensity: value };
}

export class PersonalityPicker {
  /**
   * @param {HTMLElement} button the status-bar button
   * @param {HTMLElement} panel the popover container
   * @param {(fields: {mode: string, intensity: number}) => void} send
   */
  constructor(button, panel, send, { debounceMs = 250 } = {}) {
    this.button = button;
    this.panel = panel;
    this.send = send;
    this.debounceMs = debounceMs;
    this.current = { mode: 'normal', intensity: 50 };
    this.timer = null;
    this.build();
    button.addEventListener('click', () => this.toggle());
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !this.panel.hidden) this.close();
    });
  }

  build() {
    this.radios = new Map();
    const list = el('div', { className: 'persona-modes', attrs: { role: 'radiogroup', 'aria-label': 'Personality' } });
    for (const mode of MODES) {
      const input = el('input', {
        attrs: { type: 'radio', name: 'persona-mode', value: mode.id, id: `persona-${mode.id}` },
        on: { change: () => this.choose(mode.id) },
      });
      this.radios.set(mode.id, input);
      list.append(
        el('label', { className: 'persona-mode', attrs: { for: `persona-${mode.id}` } }, [
          input,
          el('span', { className: 'name', text: mode.label }),
          el('span', { className: 'hint', text: mode.hint }),
        ]),
      );
    }
    this.slider = el('input', {
      attrs: { type: 'range', min: '0', max: '100', step: '1', 'aria-label': 'Intensity' },
      on: { input: () => this.slide() },
    });
    this.value = el('span', { className: 'persona-value' });
    this.panel.append(
      el('div', { className: 'persona-title', text: 'Personality' }),
      list,
      el('div', { className: 'persona-slider' }, [el('span', { text: 'Intensity' }), this.slider, this.value]),
      el('p', { className: 'persona-note', text: 'Changes only how Jarvis talks. From the next reply.' }),
    );
    this.render();
  }

  toggle() {
    if (this.panel.hidden) this.open();
    else this.close();
  }

  open() {
    this.panel.hidden = false;
    this.button.setAttribute('aria-expanded', 'true');
    this.radios.get(this.current.mode)?.focus();
  }

  close() {
    this.panel.hidden = true;
    this.button.setAttribute('aria-expanded', 'false');
  }

  choose(mode) {
    this.current = { ...this.current, mode };
    this.render();
    this.send(personalityCommand(mode, this.current.intensity));
  }

  slide() {
    this.current = { ...this.current, intensity: Number(this.slider.value) };
    this.value.textContent = `${this.current.intensity}%`;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.send(personalityCommand(this.current.mode, this.current.intensity)), this.debounceMs);
  }

  /** The backend's word is final (it also arrives on connect and after a voice change). */
  apply(event) {
    this.current = { mode: event.mode, intensity: event.intensity };
    this.render();
  }

  render() {
    const { mode, intensity } = this.current;
    for (const [id, input] of this.radios) input.checked = id === mode;
    this.slider.value = String(intensity);
    this.slider.disabled = mode === 'normal';
    this.value.textContent = mode === 'normal' ? '—' : `${intensity}%`;
    this.button.textContent = personalityLabel(this.current);
    this.button.dataset.mode = mode;
  }
}
