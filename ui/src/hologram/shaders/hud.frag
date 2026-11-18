// Luminous line work. Thin bands get soft edges; thick arcs (uOutline = 1) are mostly
// outline with a faint fill, so nothing reads as a solid filled shape.
uniform float uAlpha;
uniform float uOutline;

varying float vEdge;
varying float vRadius;
varying float vAngle;
varying float vWorldAngle;

void main() {
  float e = abs(vEdge * 2.0 - 1.0);
  float soft = 1.0 - smoothstep(0.35, 1.0, e);
  float outline = 0.14 + 0.86 * smoothstep(0.7, 0.97, e);
  float profile = mix(soft, outline, uOutline);
  vec3 color = hudColor(vRadius, vAngle, vWorldAngle);
  float brightness = uIntensity * uAlpha * profile * (1.0 + pulseBoost(vRadius));
  gl_FragColor = vec4(color * brightness, 1.0);
}
