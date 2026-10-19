// Code panel: full code from show_code, syntax highlighted, with a copy button.
// Code is displayed only; nothing here can run it.
import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import c from 'highlight.js/lib/languages/c';
import cpp from 'highlight.js/lib/languages/cpp';
import csharp from 'highlight.js/lib/languages/csharp';
import css from 'highlight.js/lib/languages/css';
import go from 'highlight.js/lib/languages/go';
import java from 'highlight.js/lib/languages/java';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import kotlin from 'highlight.js/lib/languages/kotlin';
import php from 'highlight.js/lib/languages/php';
import python from 'highlight.js/lib/languages/python';
import ruby from 'highlight.js/lib/languages/ruby';
import rust from 'highlight.js/lib/languages/rust';
import sql from 'highlight.js/lib/languages/sql';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';

import { el } from './dom.js';

const LANGUAGES = { bash, c, cpp, csharp, css, go, java, javascript, json, kotlin, php, python, ruby, rust, sql, typescript, xml };
for (const [name, definition] of Object.entries(LANGUAGES)) hljs.registerLanguage(name, definition);
hljs.registerAliases(['html', 'svg'], { languageName: 'xml' });
hljs.registerAliases(['js', 'node'], { languageName: 'javascript' });
hljs.registerAliases(['ts'], { languageName: 'typescript' });
hljs.registerAliases(['py'], { languageName: 'python' });
hljs.registerAliases(['c++'], { languageName: 'cpp' });
hljs.registerAliases(['c#', 'cs'], { languageName: 'csharp' });
hljs.registerAliases(['shell', 'sh', 'powershell', 'ps1'], { languageName: 'bash' });

const MAX_BLOCKS = 10;
const COPIED_MS = 1500;

export class CodePanel {
  /** @param {HTMLElement} container @param {(text: string) => Promise<void>} copy */
  constructor(container, copy) {
    this.container = container;
    this.copy = copy;
    this.blocks = [];
    this.renderEmpty();
  }

  renderEmpty() {
    this.container.replaceChildren(
      el('p', { className: 'empty', text: 'Code from coding answers appears here.' }),
    );
  }

  add(event) {
    this.blocks.unshift(event);
    this.blocks.length = Math.min(this.blocks.length, MAX_BLOCKS);
    this.container.replaceChildren(...this.blocks.map((block, i) => this.renderBlock(block, i === 0)));
  }

  renderBlock(block, open) {
    const language = hljs.getLanguage(block.language) ? block.language : null;
    const highlighted = language
      ? hljs.highlight(block.code, { language, ignoreIllegals: true })
      : hljs.highlightAuto(block.code);
    const code = el('code', { className: `hljs language-${language ?? 'plaintext'}` });
    // highlight.js escapes the code itself; its output contains only <span class> markup.
    code.innerHTML = highlighted.value;
    const copyButton = el('button', {
      className: 'copy',
      text: 'Copy',
      attrs: { type: 'button', title: 'Copy to clipboard' },
      on: {
        click: async () => {
          await this.copy(block.code);
          copyButton.textContent = 'Copied';
          setTimeout(() => (copyButton.textContent = 'Copy'), COPIED_MS);
        },
      },
    });
    const details = el('details', { className: 'code-block' }, [
      el('summary', {}, [
        el('span', { className: 'title', text: block.title }),
        el('span', { className: 'lang', text: block.language }),
        copyButton,
      ]),
      el('pre', {}, [code]),
    ]);
    details.open = open;
    return details;
  }
}
