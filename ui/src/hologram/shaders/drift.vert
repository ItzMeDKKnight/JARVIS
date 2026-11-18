// Particles and line fragments drifting outward in depth. Each starts at a random point of
// its journey (aSeed) and fades in and out, so the stream is continuous without popping.
#define PI 3.14159265359
attribute float aSeed;
attribute float aOffset;
uniform float uDriftPhase;
uniform float uInner;
uniform float uOuter;
uniform float uSize;
uniform float uPixelRatio;

varying float vFade;
varying float vRadius;
varying float vWorldAngle;

void main() {
  float travel = fract(aSeed * 7.31 + uDriftPhase * (0.55 + aSeed * 0.9));
  float r = uInner + (uOuter - uInner) * travel + aOffset;
  vec4 world = modelMatrix * vec4(position.xy * r, position.z, 1.0);
  vFade = sin(travel * PI);
  vRadius = r;
  vWorldAngle = atan(world.y, world.x);
  vec4 view = viewMatrix * world;
  gl_Position = projectionMatrix * view;
  gl_PointSize = uSize * uPixelRatio * (0.45 + fract(aSeed * 13.7)) * (4.0 / max(-view.z, 0.5));
}
