uniform vec3 uColor;
uniform float uIntensity;
varying float vAlpha;

void main() {
  float d = length(gl_PointCoord - 0.5);
  float soft = 1.0 - smoothstep(0.1, 0.5, d);
  gl_FragColor = vec4(uColor * soft * vAlpha * uIntensity, 1.0);
}
