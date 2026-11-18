// Rings, arcs, ticks and line work. Local polar coordinates drive the colour gradient and
// the thinking chase; the world angle keeps the idle orange side fixed on screen.
attribute float aEdge;
uniform float uScale;

varying float vEdge;
varying float vRadius;
varying float vAngle;
varying float vWorldAngle;

void main() {
  vEdge = aEdge;
  vRadius = length(position.xy);
  vAngle = atan(position.y, position.x);
  vec4 world = modelMatrix * vec4(position * vec3(uScale, uScale, 1.0), 1.0);
  vWorldAngle = atan(world.y, world.x);
  gl_Position = projectionMatrix * viewMatrix * world;
}
