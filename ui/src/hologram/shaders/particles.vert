// Tiny drifting motes around the reactor. Motion is computed here from a per-particle seed,
// so the CPU never touches the particle buffer after creation.
attribute float aSeed;
uniform float uTime;
uniform float uSpeed;
uniform float uPixelRatio;
varying float vAlpha;

void main() {
  vec3 p = position;
  float t = uTime * uSpeed * (0.04 + aSeed * 0.05);
  float angle = atan(p.y, p.x) + t * (aSeed > 0.5 ? 1.0 : -1.0) * 0.3;
  float radius = length(p.xy) + sin(t * 3.0 + aSeed * 40.0) * 0.06;
  p.xy = vec2(cos(angle), sin(angle)) * radius;
  p.z += sin(t * 2.0 + aSeed * 13.0) * 0.1;
  vAlpha = 0.25 + 0.5 * fract(aSeed * 91.7);
  vec4 mv = modelViewMatrix * vec4(p, 1.0);
  gl_PointSize = (1.5 + aSeed * 2.5) * uPixelRatio;
  gl_Position = projectionMatrix * mv;
}
