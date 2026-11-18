// Security helpers for the Electron shell. Pure functions, unit-tested in ui/test.

import path from 'node:path';

export const APP_SCHEME = 'app';
export const APP_HOST = 'jarvis';
export const APP_ORIGIN = `${APP_SCHEME}://${APP_HOST}`;

// The renderer may load only its own bundled files and talk only to the local backend.
export const CONTENT_SECURITY_POLICY = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "font-src 'self'",
  "img-src 'self' data:",
  'connect-src ws://127.0.0.1:*',
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
  "object-src 'none'",
].join('; ');

/**
 * Map an app://jarvis/... URL to a file inside `root`, or null if it would escape it.
 * Guards against `..`, encoded separators and other hosts.
 */
export function resolveAppFile(requestUrl, root) {
  let url;
  try {
    url = new URL(requestUrl);
  } catch {
    return null;
  }
  if (url.protocol !== `${APP_SCHEME}:` || url.host !== APP_HOST) return null;
  let relative;
  try {
    relative = decodeURIComponent(url.pathname);
  } catch {
    return null;
  }
  if (relative === '/' || relative === '') relative = '/index.html';
  const base = path.resolve(root);
  const file = path.resolve(base, `.${relative}`);
  if (file !== base && !file.startsWith(base + path.sep)) return null;
  return file;
}

/** Only http(s) links leave the app (to the default browser), e.g. search sources. */
export function isSafeExternalUrl(value) {
  try {
    const url = new URL(String(value));
    return (url.protocol === 'http:' || url.protocol === 'https:') && !url.username && !url.password;
  } catch {
    return false;
  }
}

/** Network requests the renderer may make: the backend WebSocket on loopback, nothing else. */
export function isAllowedRequest(requestUrl, backendPort) {
  try {
    const url = new URL(requestUrl);
    if (url.protocol === `${APP_SCHEME}:`) return url.host === APP_HOST;
    if (url.protocol === 'devtools:') return true;
    return (
      url.protocol === 'ws:' &&
      url.hostname === '127.0.0.1' &&
      backendPort !== null &&
      Number(url.port) === backendPort
    );
  } catch {
    return false;
  }
}

/** IPC calls are accepted only from our own page. */
export function isTrustedSender(frameUrl) {
  return typeof frameUrl === 'string' && frameUrl.startsWith(`${APP_ORIGIN}/`);
}

// Folders Jarvis writes PDFs into (see backend/jarvis/documents/pdf_writer.py).
const OUTPUT_DIR = /^Outputs(_Jarvis(_\d{1,3})?)?$/;

/**
 * Path of a PDF Jarvis saved that the HUD may open, or null. Only `.pdf` files directly inside
 * <repo>/Outputs or <repo>/Outputs_Jarvis[_N]; no other location, whatever the backend says.
 * The caller still checks the file exists and isn't a link (that needs the file system).
 */
export function resolveOutputFile(candidate, repoRoot) {
  if (typeof candidate !== 'string' || candidate.length > 1024 || candidate.includes('\0')) return null;
  const resolved = path.resolve(candidate);
  if (path.extname(resolved).toLowerCase() !== '.pdf') return null;
  const folder = path.dirname(resolved);
  if (path.dirname(folder).toLowerCase() !== path.resolve(repoRoot).toLowerCase()) return null;
  if (!OUTPUT_DIR.test(path.basename(folder))) return null;
  return resolved;
}
