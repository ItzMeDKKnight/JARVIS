// Live transcript at the bottom of the HUD.
import { el, langOf } from './dom.js';
import { TranscriptModel } from './transcript-model.js';

const SPEAKER = { user: 'You', assistant: 'Jarvis', system: '' };

export class TranscriptPanel {
  /** @param {HTMLElement} container */
  constructor(container) {
    this.container = container;
    this.model = new TranscriptModel();
    this.nodes = new Map(); // entry -> element
  }

  applyTranscript(event) {
    this.render(this.model.applyTranscript(event));
  }

  addSystem(text, kind) {
    this.render(this.model.addSystem(text, kind));
  }

  render(entry) {
    let node = this.nodes.get(entry);
    if (!node) {
      node = el('div', { className: `line ${entry.role} ${entry.kind ?? ''}` }, [
        SPEAKER[entry.role] ? el('span', { className: 'who', text: SPEAKER[entry.role] }) : null,
        el('span', { className: 'text' }),
      ]);
      this.nodes.set(entry, node);
      this.container.append(node);
      this.prune();
    }
    const text = node.querySelector('.text');
    text.textContent = entry.text;
    text.lang = langOf(entry.text);
    node.classList.toggle('partial', !entry.final);
    this.container.scrollTop = this.container.scrollHeight;
  }

  prune() {
    const keep = new Set(this.model.entries);
    for (const [entry, node] of this.nodes) {
      if (!keep.has(entry)) {
        node.remove();
        this.nodes.delete(entry);
      }
    }
  }

  lastAssistantText() {
    return this.model.lastAssistantText();
  }
}
