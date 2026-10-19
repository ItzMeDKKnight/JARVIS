// Keyboard shortcuts: Space (hold) = push-to-talk fallback, M = mute, Esc = interrupt.
// The decision logic is a pure function so it can be tested without a browser.

/**
 * Decide what a key event means.
 * @param {{type: 'keydown'|'keyup', code: string, repeat?: boolean, ctrlKey?: boolean,
 *          altKey?: boolean, metaKey?: boolean}} event
 * @param {{typing: boolean, pttHeld: boolean, voice: boolean}} context
 * @returns {null | 'ptt_start' | 'ptt_stop' | 'mute_toggle' | 'interrupt' | 'blur'}
 */
export function shortcutFor(event, { typing, pttHeld, voice }) {
  if (event.ctrlKey || event.altKey || event.metaKey) return null;
  if (event.code === 'Escape' && event.type === 'keydown') return typing ? 'blur' : 'interrupt';
  if (event.code === 'Space') {
    // Releasing Space always ends a hold, even if focus moved into the text box meanwhile.
    if (event.type === 'keyup') return pttHeld ? 'ptt_stop' : null;
    if (typing || event.repeat || pttHeld || !voice) return null;
    return 'ptt_start';
  }
  if (event.code === 'KeyM' && event.type === 'keydown' && !typing && !event.repeat) {
    return 'mute_toggle';
  }
  return null;
}

/** Wire shortcutFor to the window. `actions` receives the command names above. */
export function installShortcuts({ isTyping, getVoice, onAction }) {
  let pttHeld = false;
  const handler = (event) => {
    const action = shortcutFor(event, { typing: isTyping(), pttHeld, voice: getVoice() });
    if (!action) return;
    event.preventDefault();
    if (action === 'ptt_start') pttHeld = true;
    if (action === 'ptt_stop') pttHeld = false;
    onAction(action);
  };
  window.addEventListener('keydown', handler);
  window.addEventListener('keyup', handler);
  // Losing focus mid-hold must not leave the mic recording.
  window.addEventListener('blur', () => {
    if (pttHeld) {
      pttHeld = false;
      onAction('ptt_stop');
    }
  });
}
