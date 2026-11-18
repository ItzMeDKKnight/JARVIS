// Renderer entry point: connects to the backend and wires events to the reactor and panels.
// Runs sandboxed: the only way out is window.jarvis (electron/preload.cjs) and the backend
// WebSocket on 127.0.0.1.

import '@fontsource/rajdhani/500.css';
import '@fontsource/rajdhani/600.css';
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/noto-sans-devanagari/400.css';
import './styles.css';

import schema from '../../protocol/events.schema.json';
import { Hologram } from './hologram/core.js';
import { ERROR_HOLD_MS, visualState } from './hologram/states.js';
import { CodePanel } from './panels/code-panel.js';
import { MemoryPanel } from './panels/memory-panel.js';
import { SourcesPanel } from './panels/sources.js';
import { StatusBar } from './panels/status-bar.js';
import { TranscriptPanel } from './panels/transcript.js';
import { createProtocol } from './protocol.js';
import { installShortcuts } from './shortcuts.js';
import { StopControl, describeStop } from './stop.js';
import { ConnectionState, JarvisSocket } from './ws-client.js';

const bridge = window.jarvis;
const protocol = createProtocol(schema);
const $ = (id) => document.getElementById(id);

const ui = {
  connection: $('connection'),
  banner: $('banner'),
  mutedBadge: $('muted-badge'),
  compactLine: $('compact-line'),
  drawer: $('drawer'),
  conversation: $('conversation'),
  input: $('text-input'),
};

const view = {
  connected: false,
  state: 'idle',
  errorSince: null,
  status: null,
};

// -- reactor ----------------------------------------------------------------------------------

const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
const hologram = new Hologram($('reactor'), { reducedMotion: reducedMotion.matches });
reducedMotion.addEventListener('change', (e) => hologram.setReducedMotion(e.matches));
hologram.start();

function refreshReactor() {
  hologram.setState(visualState({ ...view, now: performance.now() }));
}

// -- panels -----------------------------------------------------------------------------------

const transcript = new TranscriptPanel($('transcript'));
const statusBar = new StatusBar(document.querySelector('.statusbar'));
const codePanel = new CodePanel($('code-panel'), (text) => bridge.copyText(text));
const sourcesPanel = new SourcesPanel($('sources-panel'), (url) => bridge.openExternal(url));
const memoryPanel = new MemoryPanel($('memory-panel'), {
  list: () => send('memory_list'),
  remove: (ids) => send('memory_delete', { ids }),
  forgetAll: () => send('memory_forget_all'),
});

function selectTab(name) {
  for (const tab of ui.drawer.querySelectorAll('[role=tab]')) {
    tab.setAttribute('aria-selected', String(tab.dataset.tab === name));
  }
  for (const panel of ui.drawer.querySelectorAll('.tab-panel')) {
    panel.hidden = panel.dataset.panel !== name;
  }
  ui.drawer.dataset.open = 'true';
  if (name === 'memory') memoryPanel.refresh();
}

for (const tab of ui.drawer.querySelectorAll('[role=tab]')) {
  tab.addEventListener('click', () => selectTab(tab.dataset.tab));
}
$('drawer-toggle').addEventListener('click', () => {
  ui.drawer.dataset.open = ui.drawer.dataset.open === 'true' ? 'false' : 'true';
});
$('conversation-toggle').addEventListener('click', () => {
  ui.conversation.dataset.open = ui.conversation.dataset.open === 'true' ? 'false' : 'true';
});

function showBanner(text, kind = 'error') {
  ui.banner.textContent = text;
  ui.banner.dataset.kind = kind;
  ui.banner.hidden = !text;
}

// -- backend events -----------------------------------------------------------------------

const handlers = {
  state_changed(event) {
    view.state = event.state;
    statusBar.setState(event.state);
    stopControl.setState(event.state);
    if (event.state === 'error') {
      view.errorSince = performance.now();
      setTimeout(refreshReactor, ERROR_HOLD_MS + 20);
    }
    refreshReactor();
  },
  audio_level(event) {
    hologram.setLevel(event.source, event.level);
  },
  transcript(event) {
    transcript.applyTranscript(event);
    if (event.role === 'assistant') ui.compactLine.textContent = transcript.lastAssistantText();
  },
  tool_started(event) {
    if (event.summary) transcript.addSystem(`▸ ${event.summary}`);
  },
  tool_finished(event) {
    transcript.addSystem(`${event.ok ? '✓' : '✗'} ${event.summary}`, event.ok ? 'info' : 'error');
  },
  code_block(event) {
    codePanel.add(event);
    selectTab('code');
  },
  sources(event) {
    sourcesPanel.set(event);
    selectTab('sources');
  },
  error(event) {
    if (event.code === 'voice_unavailable') {
      showBanner(`Voice unavailable: ${event.message}. You can still type.`, 'info');
    } else {
      transcript.addSystem(`Error: ${event.code}`, 'error');
    }
  },
  status(event) {
    view.status = event;
    statusBar.setStatus(event);
    ui.mutedBadge.hidden = !event.muted;
    if (!event.voice) showBanner('Voice is unavailable. You can still type to Jarvis.', 'info');
    else if (ui.banner.dataset.kind === 'info') showBanner('');
    bridge.reportStatus(event);
  },
  usage(event) {
    statusBar.setUsage(event);
  },
  memory(event) {
    memoryPanel.render(event);
  },
  stopped(event) {
    const line = describeStop(event.cancelled);
    if (line) transcript.addSystem(line);
  },
};

const socket = new JarvisSocket({
  getBackend: () => bridge.getBackend(),
  protocol,
  onEvent: (event) => handlers[event.type]?.(event),
  onConnection: (state, detail) => {
    view.connected = state === ConnectionState.CONNECTED;
    if (!view.connected) statusBar.setState(state === ConnectionState.CONNECTING ? 'connecting' : 'offline');
    ui.connection.dataset.value = state;
    ui.connection.textContent = {
      connecting: 'Connecting',
      connected: 'Online',
      offline: 'Backend offline',
      failed: 'Backend stopped',
    }[state];
    if (state === ConnectionState.FAILED) {
      showBanner(`Jarvis's backend could not start: ${detail ?? 'unknown error'}`);
    } else if (state === ConnectionState.OFFLINE) {
      showBanner('Backend offline. Reconnecting…');
    } else if (state === ConnectionState.CONNECTED) {
      showBanner('');
    }
    refreshReactor();
  },
});

bridge.onBackend((info) => {
  if (info.status === 'ready') socket.retryNow();
  if (info.status === 'failed') socket.connect();
});
socket.start();

function send(type, fields) {
  const sent = socket.send(type, fields);
  if (!sent) transcript.addSystem('Not connected to Jarvis yet.', 'error');
  return sent;
}

// One stop path for the Stop button and Esc.
const stopControl = new StopControl($('btn-stop'), { send: () => send('interrupt') });

// -- input, shortcuts, tray, window ---------------------------------------------------------

$('input-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const text = ui.input.value.trim();
  if (!text) return;
  send('text_input', { text });
  ui.input.value = '';
});

installShortcuts({
  isTyping: () => document.activeElement === ui.input,
  getVoice: () => Boolean(view.status?.voice && !view.status?.muted),
  onAction: (action) => {
    if (action === 'blur' || action === 'interrupt') {
      if (action === 'blur') ui.input.blur();
      stopControl.stop();
    } else {
      send(action);
    }
  },
});

bridge.onTrayCommand((command) => send(command.type));

function applyCompact(compact) {
  document.body.classList.toggle('compact', compact);
  hologram.setFit(compact ? 0.72 : 0.6);
}
bridge.onWindow((state) => applyCompact(state.compact));
bridge.getSettings().then((settings) => applyCompact(settings.compact));
$('btn-compact').addEventListener('click', () => bridge.setCompact(!document.body.classList.contains('compact')));
$('btn-minimize').addEventListener('click', () => bridge.minimize());
$('btn-close').addEventListener('click', () => bridge.hide());
