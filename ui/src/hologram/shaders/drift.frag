uniform float uAlpha;
uniform float uPoints; // 1 for points (round soft dots), 0 for line fragments

varying float vFade;
varying float vRadius;
varying float vWorldAngle;

void main() {
  float shape = 1.0;
  if (uPoints > 0.5) {
    float d = length(gl_PointCoord - 0.5) * 2.0;
    shape = pow(1.0 - smoothstep(0.0, 1.0, d), 2.0);
  }
  vec3 color = hudColor(vRadius, 0.0, vWorldAngle);
  gl_FragColor = vec4(color * shape * vFade * uAlpha * uIntensity, 1.0);
}
