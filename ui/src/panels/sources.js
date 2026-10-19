// Sources from the last web search: title + URL. Clicking opens the default browser
// (Electron only lets http/https through).
import { el } from './dom.js';

export class SourcesPanel {
  /** @param {HTMLElement} container @param {(url: string) => void} open */
  constructor(container, open) {
    this.container = container;
    this.open = open;
    this.container.replaceChildren(el('p', { className: 'empty', text: 'Web search sources appear here.' }));
  }

  set(event) {
    const items = event.items.map((item) =>
      el('li', {}, [
        el('a', {
          text: item.title,
          attrs: { href: item.url, title: item.url, rel: 'noreferrer' },
          on: {
            click: (e) => {
              e.preventDefault();
              this.open(item.url);
            },
          },
        }),
        el('span', { className: 'url', text: item.url }),
      ]),
    );
    this.container.replaceChildren(el('ol', { className: 'sources' }, items));
  }
}
