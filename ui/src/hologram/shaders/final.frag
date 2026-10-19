// Last pass over the bloomed image: heat shimmer around the glow, faint scanlines and
// flicker, state desaturation (MUTED / offline), the ERROR amber tint, and a vignette.

uniform sampler2D tDiffuse;
uniform float uTime;
uniform vec2 uResolution;
uniform vec2 uCenter;        // reactor centre in UV space
uniform float uReactorRadius; // in pixels
uniform float uShimmer;
uniform float uScanlines;
uniform float uFlicker;
uniform float uSaturation;
uniform float uErrorMix;
uniform vec3 uErrorColor;

varying vec2 vUv;

void main() {
  vec2 uv = vUv;
  vec2 pixel = (uv - uCenter) * uResolution;
  float rNorm = length(pixel) / max(uReactorRadius, 1.0);

  // Heat shimmer: a tiny wobble in a band just outside the light.
  float shimmerBand = smoothstep(0.35, 0.55, rNorm) * (1.0 - smoothstep(0.75, 1.15, rNorm));
  vec2 wobble = vec2(
    noise(uv * 38.0 + vec2(0.0, uTime * 1.7)),
    noise(uv * 38.0 + vec2(uTime * 1.3, 7.0))
  ) - 0.5;
  uv += wobble * 0.0022 * uShimmer * shimmerBand;

  // Soft exponential tone curve: bright light rolls off instead of clipping to flat white.
  vec3 color = vec3(1.0) - exp(-texture2D(tDiffuse, uv).rgb * 1.35);
  color = desaturate(color, uSaturation);
  color = mix(color, uErrorColor * dot(color, vec3(0.4, 0.45, 0.15)) * 1.6, uErrorMix);

  float flicker = 1.0 - uFlicker * 0.18 * step(0.82, noise(vec2(uTime * 18.0, 0.0)));
  color *= flicker;
  float scan = 0.5 + 0.5 * sin(gl_FragCoord.y * 1.4);
  color *= 1.0 - uScanlines * 0.05 * scan;

  vec2 centered = vUv - 0.5;
  float vignette = 1.0 - dot(centered, centered) * 0.9;
  gl_FragColor = vec4(color * vignette, 1.0);
}
