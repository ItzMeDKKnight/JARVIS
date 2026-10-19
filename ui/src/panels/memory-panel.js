// Memory panel: everything Jarvis remembers, with a delete button per fact and a
// "forget everything" button that needs a second click to confirm.
import { el, langOf } from './dom.js';

const CONFIRM_WINDOW_MS = 4000;

export class MemoryPanel {
  /**
   * @param {HTMLElement} container
   * @param {{list: () => void, remove: (ids: number[]) => void, forgetAll: () => void}} actions
   */
  constructor(container, actions) {
    this.container = container;
    this.actions = actions;
    this.confirmTimer = null;
    this.container.replaceChildren(el('p', { className: 'empty', text: 'Connecting…' }));
  }

  /** Called when the panel is opened, so the list is fresh. */
  refresh() {
    this.actions.list();
  }

  render(snapshot) {
    if (!snapshot.enabled) {
      this.container.replaceChildren(
        el('p', { className: 'empty', text: 'Memory is turned off (memory.enabled in config.yaml).' }),
      );
      return;
    }
    const children = [];
    if (snapshot.summary) {
      children.push(
        el('section', { className: 'summary' }, [
          el('h3', { text: 'Conversation summary' }),
          el('p', { text: snapshot.summary, attrs: { lang: langOf(snapshot.summary) } }),
        ]),
      );
    }
    children.push(el('h3', { text: `Facts (${snapshot.facts.length})` }));
    if (snapshot.facts.length === 0) {
      children.push(el('p', { className: 'empty', text: 'Nothing remembered yet. Say "remember that…".' }));
    } else {
      children.push(el('ul', { className: 'facts' }, snapshot.facts.map((fact) => this.renderFact(fact))));
    }
    if (snapshot.facts.length > 0 || snapshot.summary) children.push(this.renderForgetAll());
    this.container.replaceChildren(...children);
  }

  renderFact(fact) {
    const remove = el('button', {
      className: 'delete',
      text: '×',
      attrs: { type: 'button', title: 'Forget this', 'aria-label': `Forget: ${fact.text}` },
      on: { click: () => this.actions.remove([fact.id]) },
    });
    return el('li', { className: `fact ${fact.source}` }, [
      el('span', { className: 'text', text: fact.text, attrs: { lang: langOf(fact.text) } }),
      el('span', {
        className: 'meta',
        text: `${fact.category} · ${fact.source === 'explicit' ? 'you asked' : 'learned'} · ${fact.created_at.slice(0, 10)}`,
      }),
      remove,
    ]);
  }

  renderForgetAll() {
    const button = el('button', { className: 'forget-all', text: 'Forget everything', attrs: { type: 'button' } });
    button.addEventListener('click', () => {
      if (button.classList.contains('armed')) {
        clearTimeout(this.confirmTimer);
        this.actions.forgetAll();
        return;
      }
      button.classList.add('armed');
      button.textContent = 'Click again to erase all memory';
      this.confirmTimer = setTimeout(() => {
        button.classList.remove('armed');
        button.textContent = 'Forget everything';
      }, CONFIRM_WINDOW_MS);
    });
    return button;
  }
}
