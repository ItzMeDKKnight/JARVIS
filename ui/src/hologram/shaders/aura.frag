// The aura radiating outward from the reactor: WHITE while listening (driven by the mic
// level), CYAN while speaking (driven by the TTS level). Drawn additively behind the reactor.

uniform float uTime;
uniform float uSpeed;
uniform float uRipple;      // 0 in reduced-motion mode
uniform float uPlaneSize;
uniform float uIntensity;
uniform float uLevel;
uniform vec3 uColor;
uniform float uStartRadius;

varying vec2 vUv;

void main() {
  vec2 p = (vUv - 0.5) * uPlaneSize;
  float r = length(p);
  float a = atan(p.y, p.x);
  float outside = smoothstep(uStartRadius - 0.05, uStartRadius + 0.02, r);
  float reach = 0.35 + uLevel * 0.9; // louder = the aura reaches further
  float falloff = exp(-(r - uStartRadius) / reach * 2.2);
  float ripples = 0.5 + 0.5 * sin(r * 16.0 - uTime * uSpeed * 5.0 - uLevel * 6.0);
  ripples = mix(1.0, 0.55 + 0.45 * ripples, uRipple * (0.4 + uLevel));
  float wobble = 0.9 + 0.1 * noise(vec2(a * 4.0, uTime * 0.6 * uSpeed));
  float glow = outside * falloff * ripples * wobble;
  float strength = uIntensity * (0.35 + uLevel * 1.4);
  gl_FragColor = vec4(uColor * glow * strength, 1.0);
}
