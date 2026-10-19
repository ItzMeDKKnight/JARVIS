// Faint holographic HUD arcs and tick rings around the reactor. Kept subtle on purpose:
// the reactor is the hero. Drawn additively.

uniform float uTime;
uniform float uSpeed;
uniform float uPlaneSize;
uniform float uIntensity;
uniform vec3 uColor;

varying vec2 vUv;

float arcs(float r, float a, float radius, float width, float count, float fill, float spin) {
  float ring = exp(-pow((r - radius) / width, 2.0));
  float turn = fract((a + spin) / TAU * count);
  float on = step(turn, fill);
  return ring * on;
}

void main() {
  vec2 p = (vUv - 0.5) * uPlaneSize;
  float r = length(p);
  float a = atan(p.y, p.x);
  float t = uTime * uSpeed;

  float value = 0.0;
  value += arcs(r, a, 1.18, 0.004, 3.0, 0.62, t * 0.05) * 0.9;
  value += arcs(r, a, 1.27, 0.0025, 24.0, 0.55, -t * 0.03) * 0.6;
  value += arcs(r, a, 1.52, 0.003, 2.0, 0.28, t * 0.025) * 0.8;
  value += arcs(r, a, 1.9, 0.002, 5.0, 0.18, -t * 0.015) * 0.5;

  // A fine tick ring.
  float tickRing = exp(-pow((r - 1.38) / 0.018, 2.0));
  float ticks = step(0.86, fract(a / TAU * 180.0));
  value += tickRing * ticks * 0.55;
  // A thin full circle for structure.
  value += exp(-pow((r - 1.38) / 0.0015, 2.0)) * 0.35;

  float edgeFade = 1.0 - smoothstep(1.7, 2.3, r);
  gl_FragColor = vec4(uColor * value * uIntensity * edgeFade, 1.0);
}
