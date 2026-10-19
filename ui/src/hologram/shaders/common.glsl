// Shared helpers, prepended to the fragment shaders.
#define PI 3.14159265359
#define TAU 6.28318530718

float hash21(vec2 p) {
  p = fract(p * vec2(123.34, 456.21));
  p += dot(p, p + 45.32);
  return fract(p.x * p.y);
}

float noise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  return mix(
    mix(hash21(i), hash21(i + vec2(1.0, 0.0)), u.x),
    mix(hash21(i + vec2(0.0, 1.0)), hash21(i + vec2(1.0, 1.0)), u.x),
    u.y
  );
}

float fbm(vec2 p) {
  float value = 0.0;
  float amplitude = 0.5;
  for (int i = 0; i < 4; i++) {
    value += amplitude * noise(p);
    p *= 2.03;
    amplitude *= 0.5;
  }
  return value;
}

// Anti-aliased step that stays crisp at any resolution.
float aastep(float threshold, float value) {
  float width = fwidth(value) * 0.75;
  return smoothstep(threshold - width, threshold + width, value);
}

// Same, with a pixel width computed by the caller. Needed inside loops and branches,
// where derivatives (fwidth) are undefined.
float aastepPx(float threshold, float value, float px) {
  return smoothstep(threshold - px, threshold + px, value);
}

// 1 inside the band [inner, outer], soft anti-aliased edges.
float band(float r, float inner, float outer) {
  return aastep(inner, r) * (1.0 - aastep(outer, r));
}

vec3 desaturate(vec3 color, float saturation) {
  float luma = dot(color, vec3(0.299, 0.587, 0.114));
  return mix(vec3(luma), color, saturation);
}

// Signed distance to an equilateral triangle (Inigo Quilez), pointing up.
float sdTriangle(vec2 p, float r) {
  const float k = 1.7320508;
  p.x = abs(p.x) - r;
  p.y = p.y + r / k;
  if (p.x + k * p.y > 0.0) p = vec2(p.x - k * p.y, -k * p.x - p.y) / 2.0;
  p.x -= clamp(p.x, -2.0 * r, 0.0);
  return -length(p) * sign(p.y);
}
