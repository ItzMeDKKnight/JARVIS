// Tiny DOM helper. Text always goes in through textContent, never innerHTML, so nothing
// from the backend (transcripts, web titles, code) can inject markup into the HUD.

export function el(tag, { className, text, attrs, on } = {}, children = []) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  for (const [key, value] of Object.entries(attrs ?? {})) node.setAttribute(key, value);
  for (const [name, handler] of Object.entries(on ?? {})) node.addEventListener(name, handler);
  for (const child of children) if (child) node.append(child);
  return node;
}

/** Marks Devanagari text so CSS can pick the Hindi font and line height. */
export function langOf(text) {
  return /[ऀ-ॿ]/.test(text) ? 'hi' : 'en';
}
