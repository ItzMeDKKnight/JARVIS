// The central lens glow and the lens-flare hot spots: quads with a procedural falloff.
attribute vec2 aUv;
uniform float uScale;

varying vec2 vUv;
varying float vWorldAngle;
varying float vRadius;

void main() {
  vUv = aUv;
  vRadius = length(position.xy);
  vec4 world = modelMatrix * vec4(position * vec3(uScale, uScale, 1.0), 1.0);
  vWorldAngle = atan(world.y, world.x);
  gl_Position = projectionMatrix * viewMatrix * world;
}
