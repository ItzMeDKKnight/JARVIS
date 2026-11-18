// A small arc-reactor icon drawn in code, so the app ships no image files.
// Returns raw BGRA pixels (the layout nativeImage.createFromBitmap expects on Windows).

const RING = [255, 236, 120]; // BGR: pale cyan
const CORE = [255, 255, 235]; // BGR: near-white
const VISION = [42, 42, 255]; // BGR: red (#FF2A2A), the screen vision dot

function smoothstep(edge0, edge1, x) {
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

/** @param {number} size @param {{muted?: boolean, vision?: boolean}} [options] */
export function reactorIconPixels(size, { muted = false, vision = false } = {}) {
  const pixels = Buffer.alloc(size * size * 4);
  const center = (size - 1) / 2;
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const r = Math.hypot(x - center, y - center) / (size / 2); // 0 at centre, 1 at edge
      const ring = smoothstep(0.98, 0.9, r) * smoothstep(0.62, 0.72, r);
      const core = smoothstep(0.42, 0.3, r);
      const alpha = Math.min(1, ring + core);
      const color = core > ring ? CORE : RING;
      const gray = muted ? 150 : null;
      const offset = (y * size + x) * 4;
      // Screen vision on: a solid red dot in the bottom-right corner, impossible to miss.
      const dot = vision ? smoothstep(0.24, 0.18, Math.hypot(x - size * 0.78, y - size * 0.78) / size) : 0;
      if (dot > 0) {
        pixels[offset] = VISION[0];
        pixels[offset + 1] = VISION[1];
        pixels[offset + 2] = VISION[2];
        pixels[offset + 3] = Math.round(Math.max(alpha, dot) * 255);
        continue;
      }
      pixels[offset] = gray ?? color[0];
      pixels[offset + 1] = gray ?? color[1];
      pixels[offset + 2] = gray ?? color[2];
      pixels[offset + 3] = Math.round(alpha * 255);
    }
  }
  return pixels;
}
