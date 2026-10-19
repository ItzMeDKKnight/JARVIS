// Vite builds the renderer (ui/src) into ui/dist, which Electron serves over app://jarvis.
// Everything is bundled locally: no CDN, no remote fonts, no runtime downloads.
import { defineConfig } from 'vite';

export default defineConfig({
  root: 'src',
  base: './',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    target: 'esnext',
    // Keep fonts and images as files: the CSP allows 'self', not data: URIs, for fonts.
    assetsInlineLimit: 0,
    sourcemap: false,
    modulePreload: { polyfill: false },
    // Three.js alone is ~600 kB; it loads from disk, so one bundle is fine.
    chunkSizeWarningLimit: 1000,
  },
  server: {
    // The protocol schema lives in the repo's protocol/ folder, outside ui/src.
    fs: { allow: ['..', '../../protocol'] },
  },
});
