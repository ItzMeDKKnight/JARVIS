uniform float uAlpha;
uniform float uFlare; // 0 = core glow, 1 = lens flare (adds a horizontal streak)

varying vec2 vUv;
varying float vWorldAngle;
varying float vRadius;

void main() {
  float d = length(vUv);
  float glow;
  if (uFlare > 0.5) {
    float spot = exp(-d * d * 30.0);
    float streak = exp(-abs(vUv.y) * 55.0) * exp(-abs(vUv.x) * 2.2) * 0.7;
    float cross = exp(-abs(vUv.x) * 70.0) * exp(-abs(vUv.y) * 4.0) * 0.25;
    glow = spot + streak + cross;
  } else {
    glow = exp(-d * d * 9.0) * 0.9 + exp(-d * 4.0) * 0.35;
  }
  glow *= 1.0 - smoothstep(0.85, 1.0, max(abs(vUv.x), abs(vUv.y)));
  // Hot spots are white-hot in the middle, tinted by the ring they sit on further out.
  vec3 color = mix(hudColor(vRadius + d * 0.5, 0.0, vWorldAngle), vec3(1.0), exp(-d * 6.0) * 0.6);
  gl_FragColor = vec4(color * glow * uAlpha * uIntensity * (1.0 + uLevel), 1.0);
}
