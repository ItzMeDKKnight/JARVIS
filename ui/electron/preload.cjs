// The only bridge between the sandboxed page and Electron. Deliberately tiny:
// no Node APIs, no generic ipcRenderer access, just these named calls.
// (CommonJS because sandboxed preload scripts cannot be ES modules.)

const { contextBridge, ipcRenderer } = require('electron');

function subscribe(channel, callback) {
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

contextBridge.exposeInMainWorld('jarvis', {
  /** {status: 'starting'|'ready'|'offline'|'failed'|'stopped', port?, token?, message?} */
  getBackend: () => ipcRenderer.invoke('backend:get'),
  onBackend: (callback) => subscribe('backend', callback),
  onTrayCommand: (callback) => subscribe('tray-command', callback),
  onWindow: (callback) => subscribe('window', callback),
  getSettings: () => ipcRenderer.invoke('settings:get'),
  reportStatus: (status) => ipcRenderer.invoke('hud:status', { muted: Boolean(status && status.muted) }),
  copyText: (text) => ipcRenderer.invoke('clipboard:write', String(text)),
  openExternal: (url) => ipcRenderer.invoke('shell:open', String(url)),
  openFile: (filePath) => ipcRenderer.invoke('file:open', String(filePath)),
  minimize: () => ipcRenderer.invoke('window:minimize'),
  hide: () => ipcRenderer.invoke('window:hide'),
  setCompact: (compact) => ipcRenderer.invoke('window:compact', Boolean(compact)),
});
