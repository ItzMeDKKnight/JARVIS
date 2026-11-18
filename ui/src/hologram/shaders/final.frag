// Last pass over the bloomed image: soft chromatic fringing towards the edges, a soft
// exponential tone curve (bright light rolls off instead of clipping to flat white), the
// error flicker, and a gentle vignette.

uniform sampler2D tDiffuse;
uniform float uTime;
uniform float uFlicker;
uniform float uChroma;      // colour fringe at the edges, in device pixels
uniform vec2 uResolution;
uniform float uVignette;

varying vec2 vUv;

void main() {
  vec2 centered = vUv - 0.5;
  float r = length(centered);
  vec2 shift = normalize(centered + 1e-5) * uChroma * smoothstep(0.15, 0.7, r) / uResolution;
  vec3 color = vec3(
    texture2D(tDiffuse, vUv + shift).r,
    texture2D(tDiffuse, vUv).g,
    texture2D(tDiffuse, vUv - shift).b
  );
  // Hue-preserving tone curve: compress brightness, keep the colour saturated; only the very
  // brightest spots (core, flares) burn towards white.
  float peak = max(max(color.r, color.g), max(color.b, 1e-4));
  float mapped = 1.0 - exp(-peak * 1.4);
  color = color * (mapped / peak);
  color = mix(color, vec3(mapped), smoothstep(1.4, 4.0, peak) * 0.55);
  float flicker = 1.0 - uFlicker * 0.4 * step(0.68, noise(vec2(uTime * 24.0, 0.0)));
  color *= flicker;
  color *= 1.0 - dot(centered, centered) * uVignette;
  gl_FragColor = vec4(color, 1.0);
}
