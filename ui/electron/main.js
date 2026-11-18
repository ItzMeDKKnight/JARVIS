// Electron main process: window, security settings, backend lifecycle, tray, compact mode.
//
// Hardening (see docs/security-audit.md):
// * renderer: contextIsolation, sandbox, no nodeIntegration, no spellcheck service;
// * content comes only from ui/dist over the privileged app://jarvis scheme, with a
//   strict Content-Security-Policy header on every response;
// * every network request from the renderer is cancelled except the backend
//   WebSocket on 127.0.0.1:<port>;
// * all permission requests (camera, mic, notifications, ...) are denied: the
//   microphone is used by the Python backend, never by the renderer;
// * new windows and navigation are blocked; http(s) links open in the default browser;
// * IPC handlers accept calls only from our own page and validate their arguments.

import {
  BrowserWindow,
  Menu,
  Tray,
  app,
  clipboard,
  ipcMain,
  nativeImage,
  protocol,
  session,
  shell,
} from 'electron';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { Backend } from './backend.js';
import { reactorIconPixels } from './icon.js';
import {
  APP_HOST,
  APP_ORIGIN,
  APP_SCHEME,
  CONTENT_SECURITY_POLICY,
  isAllowedRequest,
  isSafeExternalUrl,
  resolveOutputFile,
  isTrustedSender,
  resolveAppFile,
} from './security.js';

const UI_ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const REPO_ROOT = path.dirname(UI_ROOT);
const DIST = path.join(UI_ROOT, 'dist');
const NORMAL_SIZE = { width: 1280, height: 820 };
const COMPACT_SIZE = { width: 380, height: 460 };
const MAX_CLIPBOARD_CHARS = 1_000_000;
// Only these file types are served; anything else in dist/ is a 404.
const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
};

// Keep the HUD's own files (settings, Chromium cache) with the rest of Jarvis's data.
const DATA_ROOT = path.join(process.env.LOCALAPPDATA ?? app.getPath('appData'), 'Jarvis', 'hud');
app.setPath('userData', DATA_ROOT);
const SETTINGS_FILE = path.join(DATA_ROOT, 'settings.json');

// Developer aids (documented in README): screenshot and exit, and a backend override for tests.
const captureArg = process.argv.find((a) => a.startsWith('--capture='));
const CAPTURE_PATH = captureArg ? path.resolve(captureArg.slice('--capture='.length)) : null;
const CAPTURE_DELAY_MS = Number(process.env.JARVIS_HUD_CAPTURE_DELAY_MS ?? 4000);
const BACKEND_ARGS = process.env.JARVIS_BACKEND_ARGS ? JSON.parse(process.env.JARVIS_BACKEND_ARGS) : undefined;

protocol.registerSchemesAsPrivileged([
  { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } },
]);

let mainWindow = null;
let tray = null;
let quitting = false;
let hudMuted = false;
let hudVision = false; // screen vision mode, as last reported by the HUD (off at start)
let settings = loadSettings();
const backend = new Backend({ repoRoot: REPO_ROOT, python: process.env.JARVIS_PYTHON, args: BACKEND_ARGS });

function loadSettings() {
  try {
    const raw = JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8'));
    return { compact: raw.compact === true };
  } catch {
    return { compact: false };
  }
}

function saveSettings() {
  fs.mkdirSync(DATA_ROOT, { recursive: true });
  fs.writeFileSync(SETTINGS_FILE, JSON.stringify(settings, null, 2));
}

function icon(muted = false, vision = false) {
  const size = 32;
  return nativeImage.createFromBitmap(reactorIconPixels(size, { muted, vision }), { width: size, height: size });
}

// -- content and network ------------------------------------------------------------------

function registerAppProtocol() {
  // Files are read straight from ui/dist (no file:// requests), so the network guard
  // below can refuse every scheme except app: and the backend WebSocket.
  protocol.handle(APP_SCHEME, async (request) => {
    const file = resolveAppFile(request.url, DIST);
    const type = file && MIME_TYPES[path.extname(file).toLowerCase()];
    if (!file || !type || !fs.existsSync(file)) return new Response('Not found', { status: 404 });
    const body = await fs.promises.readFile(file);
    return new Response(body, {
      status: 200,
      headers: {
        'Content-Type': type,
        'Content-Security-Policy': CONTENT_SECURITY_POLICY,
        'X-Content-Type-Options': 'nosniff',
      },
    });
  });
}

function lockDownSession() {
  const ses = session.defaultSession;
  ses.setPermissionRequestHandler((_wc, _permission, callback) => callback(false));
  ses.setPermissionCheckHandler(() => false);
  ses.webRequest.onBeforeRequest((details, callback) => {
    const port = backend.info.status === 'ready' ? backend.info.port : null;
    callback({ cancel: !isAllowedRequest(details.url, port) });
  });
}

// -- window ----------------------------------------------------------------------------------

function createWindow() {
  const size = settings.compact ? COMPACT_SIZE : NORMAL_SIZE;
  mainWindow = new BrowserWindow({
    ...size,
    minWidth: COMPACT_SIZE.width,
    minHeight: COMPACT_SIZE.height,
    frame: false,
    backgroundColor: '#02040a',
    alwaysOnTop: settings.compact,
    show: false,
    icon: icon(),
    title: 'J.A.R.V.I.S.',
    webPreferences: {
      preload: path.join(UI_ROOT, 'electron', 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
      backgroundThrottling: true,
      devTools: process.env.JARVIS_HUD_DEVTOOLS === '1',
    },
  });
  const contents = mainWindow.webContents;
  contents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalUrl(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  contents.on('will-navigate', (event, url) => {
    if (!url.startsWith(`${APP_ORIGIN}/`)) event.preventDefault();
  });
  contents.on('will-attach-webview', (event) => event.preventDefault());
  // Renderer warnings and errors go to this process's stderr, so they're never silent.
  contents.on('console-message', ({ level, message }) => {
    if (level === 'warning' || level === 'error') console.error(`[renderer ${level}] ${message}`);
  });
  mainWindow.once('ready-to-show', () => {
    if (!CAPTURE_PATH || process.env.JARVIS_HUD_CAPTURE_SHOW === '1') mainWindow.show();
  });
  mainWindow.on('close', (event) => {
    if (!quitting) {
      event.preventDefault(); // closing hides to the tray; quit from the tray menu
      mainWindow.hide();
    }
  });
  mainWindow.loadURL(`${APP_SCHEME}://${APP_HOST}/index.html`);
  if (CAPTURE_PATH) scheduleCapture();
}

function setCompact(compact) {
  settings = { ...settings, compact };
  saveSettings();
  const size = compact ? COMPACT_SIZE : NORMAL_SIZE;
  mainWindow.setAlwaysOnTop(compact, 'floating');
  mainWindow.setSize(size.width, size.height);
  mainWindow.webContents.send('window', { compact });
  refreshTray();
}

function showWindow() {
  mainWindow.show();
  mainWindow.focus();
}

function scheduleCapture() {
  setTimeout(async () => {
    const image = await mainWindow.webContents.capturePage();
    fs.mkdirSync(path.dirname(CAPTURE_PATH), { recursive: true });
    fs.writeFileSync(CAPTURE_PATH, image.toPNG());
    quit();
  }, CAPTURE_DELAY_MS);
}

// -- tray -----------------------------------------------------------------------------------

function sendTrayCommand(type, fields = {}) {
  mainWindow?.webContents.send('tray-command', { type, fields });
}

function refreshTray() {
  if (!tray) return;
  const login = app.getLoginItemSettings(loginItemOptions());
  tray.setImage(icon(hudMuted, hudVision));
  const tip = ['J.A.R.V.I.S.', hudVision ? 'SCREEN VISION ON' : null, hudMuted ? 'muted' : null];
  tray.setToolTip(tip.filter(Boolean).join(' - '));
  tray.setContextMenu(
    Menu.buildFromTemplate([
      { label: 'Show J.A.R.V.I.S.', click: showWindow },
      { label: hudMuted ? 'Unmute microphone' : 'Mute microphone', click: () => sendTrayCommand('mute_toggle') },
      {
        label: hudVision ? '● Screen vision is ON (click to turn off)' : 'Screen vision (off)',
        type: 'checkbox',
        checked: hudVision,
        click: (item) => sendTrayCommand('vision_set', { enabled: item.checked }),
      },
      { label: 'Compact always-on-top', type: 'checkbox', checked: settings.compact, click: (item) => setCompact(item.checked) },
      {
        label: 'Start on login',
        type: 'checkbox',
        checked: login.openAtLogin,
        click: (item) => {
          app.setLoginItemSettings({ ...loginItemOptions(), openAtLogin: item.checked });
          refreshTray();
        },
      },
      { type: 'separator' },
      { label: 'Quit', click: quit },
    ]),
  );
}

function loginItemOptions() {
  // In development Electron runs as electron.exe <ui folder>; the folder must be passed along.
  return app.isPackaged ? {} : { path: process.execPath, args: [UI_ROOT] };
}

function createTray() {
  tray = new Tray(icon());
  tray.on('click', showWindow);
  refreshTray();
}

// -- IPC --------------------------------------------------------------------------------------

function handle(channel, fn) {
  ipcMain.handle(channel, (event, ...args) => {
    if (!isTrustedSender(event.senderFrame?.url)) throw new Error('untrusted sender');
    return fn(...args);
  });
}

function registerIpc() {
  handle('backend:get', () => backend.info);
  handle('settings:get', () => ({ ...settings }));
  handle('clipboard:write', (text) => {
    clipboard.writeText(String(text).slice(0, MAX_CLIPBOARD_CHARS));
  });
  handle('shell:open', (url) => {
    if (isSafeExternalUrl(url)) shell.openExternal(String(url));
  });
  handle('file:open', async (candidate) => {
    // Only PDFs Jarvis saved in the project's Outputs folders, and never through a link.
    const file = resolveOutputFile(candidate, REPO_ROOT);
    if (!file) return false;
    try {
      const stat = fs.lstatSync(file);
      if (!stat.isFile() || stat.isSymbolicLink()) return false;
    } catch {
      return false;
    }
    return (await shell.openPath(file)) === '';
  });
  handle('window:minimize', () => mainWindow.minimize());
  handle('window:hide', () => mainWindow.hide());
  handle('window:compact', (compact) => setCompact(compact === true));
  handle('hud:status', (status) => {
    hudMuted = status?.muted === true;
    const vision = status?.vision === true;
    if (vision !== hudVision) {
      hudVision = vision;
      // Keep the HUD itself out of screenshots while the mode is on (WDA_EXCLUDEFROMCAPTURE).
      mainWindow?.setContentProtection(hudVision);
    }
    refreshTray();
  });
}

backend.on('info', (info) => mainWindow?.webContents.send('backend', info));

// -- lifecycle ------------------------------------------------------------------------------

async function quit() {
  if (quitting) return;
  quitting = true;
  await backend.stop();
  app.quit();
}

if (!app.requestSingleInstanceLock()) {
  app.quit(); // one HUD and one backend at a time
} else {
  app.on('second-instance', () => mainWindow && showWindow());
  app.whenReady().then(() => {
    Menu.setApplicationMenu(null);
    registerAppProtocol();
    lockDownSession();
    registerIpc();
    backend.start();
    createWindow();
    if (!CAPTURE_PATH) createTray();
  });
  app.on('before-quit', (event) => {
    if (!quitting) {
      event.preventDefault();
      quit();
    }
  });
  app.on('window-all-closed', () => {
    // Stay in the tray.
  });
}
