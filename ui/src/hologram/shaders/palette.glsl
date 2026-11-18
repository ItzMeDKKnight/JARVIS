// Colour of the lens HUD at a point: the blue or orange gradient by radius (white-hot centre,
// saturated edge), the idle two-tone accent side, the thinking chase, the error red, and
// muted/offline desaturation. Shared by every HUD material; prepended after common.glsl.

uniform vec3 uBlue[3];   // core, mid, edge
uniform vec3 uOrange[3];
uniform vec3 uRed[3];
uniform float uOrangeMix;
uniform float uTwoTone;
uniform float uAccentAngle;
uniform float uChase;
uniform float uChaseSegments;
uniform float uChasePhase;
uniform float uError;
uniform float uSaturation;
uniform float uGradientRadius;
uniform float uGradientMid;  // where the gradient reaches its mid colour (fraction of radius)
uniform float uIntensity;
uniform float uLevel;   // audio level x levelGain x pulse
uniform float uWave;    // 0..1 phase of the outward ring pulse

vec3 gradient3(vec3 core, vec3 mid, vec3 edge, float t) {
  return t < uGradientMid
    ? mix(core, mid, smoothstep(0.0, 1.0, t / uGradientMid))
    : mix(mid, edge, (t - uGradientMid) / (1.0 - uGradientMid));
}

vec3 hudColor(float radius, float localAngle, float worldAngle) {
  float t = clamp(radius / uGradientRadius, 0.0, 1.0);
  vec3 blue = gradient3(uBlue[0], uBlue[1], uBlue[2], t);
  vec3 orange = gradient3(uOrange[0], uOrange[1], uOrange[2], t);
  vec3 red = gradient3(uRed[0], uRed[1], uRed[2], t);

  float warm = uOrangeMix;
  // Idle: orange accents on one side, blue on the other (the mixed two-tone look).
  float side = smoothstep(0.15, 0.95, cos(worldAngle - uAccentAngle));
  warm = max(warm, uTwoTone * side * 0.9);
  // Thinking: alternating blue / orange segments racing around each ring.
  float segment = step(0.5, fract(localAngle / TAU * uChaseSegments + uChasePhase));
  warm = mix(warm, segment, uChase);

  vec3 color = mix(blue, orange, clamp(warm, 0.0, 1.0));
  color = mix(color, red, uError);
  return desaturate(color, uSaturation);
}

// Extra brightness from the audio pulse: a bright band sweeping outward through the rings.
float pulseBoost(float radius) {
  float t = clamp(radius / uGradientRadius, 0.0, 1.4);
  float band = exp(-pow((t - uWave * 1.4) * 6.0, 2.0));
  return uLevel * (0.6 + 1.4 * band);
}
