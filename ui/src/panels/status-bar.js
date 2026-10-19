// The tiny status bar: state, microphone, model in use, spending, connection.

const STATE_LABEL = {
  idle: 'Idle',
  listening: 'Listening',
  thinking: 'Thinking',
  speaking: 'Speaking',
  error: 'Error',
  muted: 'Muted',
  connecting: 'Connecting',
  offline: 'Offline',
};

const MODEL_LABEL = { 'claude-haiku-5-5': 'Haiku 5.5', 'claude-sonnet-5-5': 'Sonnet 5.5' };

export class StatusBar {
  constructor(root) {
    this.state = root.querySelector('[data-status=state]');
    this.mic = root.querySelector('[data-status=mic]');
    this.model = root.querySelector('[data-status=model]');
    this.cost = root.querySelector('[data-status=cost]');
  }

  setState(state) {
    this.state.textContent = STATE_LABEL[state] ?? state;
    this.state.dataset.value = state;
  }

  setStatus(status) {
    let text = 'Mic on';
    if (!status.voice) text = 'No voice (type)';
    else if (status.muted) text = 'Mic off';
    this.mic.textContent = text;
    this.mic.dataset.value = status.voice && !status.muted ? 'on' : 'off';
  }

  setUsage(usage) {
    this.model.textContent = MODEL_LABEL[usage.model] ?? usage.model;
    const cap = usage.cap_usd === null ? '' : ` / $${usage.cap_usd.toFixed(2)}`;
    this.cost.textContent = `$${usage.session_cost_usd.toFixed(2)}${cap}`;
    const nearCap = usage.cap_usd !== null && usage.session_cost_usd >= usage.cap_usd * 0.8;
    this.cost.dataset.value = nearCap ? 'warn' : 'ok';
  }
}
