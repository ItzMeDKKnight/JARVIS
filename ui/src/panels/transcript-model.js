// Transcript state, separate from the DOM so it can be unit-tested.
// Partial updates repeat a turn_id with the full text so far; they replace the entry.
// A turn_id that arrives again after its entry is final starts a new entry (the backend
// reuses a few fixed ids, e.g. for typed answers).

export class TranscriptModel {
  constructor({ limit = 200 } = {}) {
    this.limit = limit;
    this.entries = [];
  }

  /** @returns the entry that changed */
  applyTranscript(event) {
    const last = this.findOpen(event.turn_id);
    if (last) {
      last.text = event.text;
      last.final = event.final;
      last.language = event.language;
      return last;
    }
    return this.push({
      id: event.turn_id,
      role: event.role,
      text: event.text,
      language: event.language,
      final: event.final,
    });
  }

  /** Tool activity and errors show as small system lines between utterances. */
  addSystem(text, kind = 'info') {
    return this.push({ id: null, role: 'system', kind, text, language: 'en', final: true });
  }

  findOpen(turnId) {
    for (let i = this.entries.length - 1; i >= 0; i -= 1) {
      const entry = this.entries[i];
      if (entry.id === turnId) return entry.final ? null : entry;
    }
    return null;
  }

  push(entry) {
    this.entries.push(entry);
    if (this.entries.length > this.limit) this.entries.splice(0, this.entries.length - this.limit);
    return entry;
  }

  lastAssistantText() {
    for (let i = this.entries.length - 1; i >= 0; i -= 1) {
      if (this.entries[i].role === 'assistant') return this.entries[i].text;
    }
    return '';
  }
}
