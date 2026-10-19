// The arc reactor, drawn in four stacked layers (one plane each, at different depths, so the
// mouse-follow tilt gives real parallax):
//   0 housing: machined steel bezel, brushed finish, engraved ticks, bolts, notches
//   1 coils:   10 trapezoidal copper-wound segments with light leaking from the gaps
//   2 light:   dark inner plate, the thin blue-white glow ring, and the core with rays
//   3 glass:   a faint cover with a slow reflection sweep (drawn additively)
// Everything is procedural: no textures are loaded.

uniform int uLayer;
uniform float uTime;
uniform float uSpeed;        // motion scale (reduced-motion mode slows everything)
uniform float uPlaneSize;    // plane width in reactor units
uniform vec2 uLight;         // light direction, follows the mouse
uniform float uCoreIntensity;
uniform float uRingIntensity;
uniform float uCoilLight;
uniform float uBreathe;
uniform float uChase;
uniform float uRingPulse;
uniform float uLevel;        // smoothed audio level (mic while listening, TTS while speaking)
uniform vec3 uCoreTint;
uniform vec3 uRingColor;
uniform float uTriangle;     // 1 = Mark II triangle core
uniform float uCoilCount;
uniform float uHousingOuter;
uniform float uHousingInner;
uniform float uCoilOuter;
uniform float uCoilInner;
uniform float uRingRadius;
uniform float uRingWidth;
uniform float uCoreRadius;

varying vec2 vUv;

float glowLevel() {
  float breath = 1.0 + 0.18 * uBreathe * sin(uTime * uSpeed * 1.1);
  return breath * (1.0 + 0.45 * uLevel);
}

vec4 housing(vec2 p, float r, float a) {
  float mask = band(r, uHousingInner, uHousingOuter);
  if (mask <= 0.0) return vec4(0.0);

  // Brushed steel: fine concentric streaks plus a little large-scale variation.
  float streaks = noise(vec2(r * 900.0, a * 2.0)) * 0.6 + noise(vec2(r * 2400.0, a * 9.0)) * 0.4;
  vec3 steel = vec3(0.10, 0.115, 0.135) * (0.75 + 0.5 * streaks) * (0.85 + 0.3 * fbm(p * 3.0));

  // Anisotropic highlight: brushed metal lights up along the arc facing the light.
  float lightAngle = atan(uLight.y, uLight.x);
  float facing = max(cos(a - lightAngle), 0.0);
  steel += vec3(0.2, 0.22, 0.26) * pow(facing, 16.0) * (0.4 + 0.6 * streaks);

  // Bevels at both edges catch specular light.
  float bevelOuter = smoothstep(uHousingOuter - 0.035, uHousingOuter, r);
  float bevelInner = 1.0 - smoothstep(uHousingInner, uHousingInner + 0.03, r);
  vec2 radial = p / max(r, 1e-4);
  float specOuter = pow(max(dot(radial, uLight), 0.0), 6.0) * bevelOuter;
  float specInner = pow(max(dot(-radial, uLight), 0.0), 6.0) * bevelInner;
  steel += vec3(0.55, 0.6, 0.68) * (specOuter + specInner) * 0.4;

  // Engraved tick marks near the rim: 120 small, every 10th longer.
  float turn = a / TAU + 0.5;
  float tickPhase = fract(turn * 120.0);
  float isMajor = step(0.92, fract(turn * 12.0));
  float tickLen = mix(0.025, 0.05, isMajor);
  float tickBand = band(r, uHousingOuter - 0.035 - tickLen, uHousingOuter - 0.035);
  float tick = (1.0 - aastep(0.09, min(tickPhase, 1.0 - tickPhase))) * tickBand;
  steel *= 1.0 - 0.55 * tick;
  steel += vec3(0.25, 0.28, 0.32) * tick * 0.15 * isMajor;

  // Six bolts in the middle of the bezel, each with a highlight and a slot.
  float boltRadius = 0.022;
  float px = fwidth(r) * 0.75; // computed outside the loop: derivatives are undefined inside it
  float mid = (uHousingInner + uHousingOuter) * 0.5 - 0.01;
  for (int i = 0; i < 6; i++) {
    float angle = (float(i) + 0.5) * TAU / 6.0;
    vec2 center = vec2(cos(angle), sin(angle)) * mid;
    vec2 d = p - center;
    float dist = length(d);
    float head = 1.0 - aastepPx(boltRadius, dist, px);
    if (head > 0.0) {
      float dome = sqrt(max(0.0, 1.0 - pow(dist / boltRadius, 2.0)));
      vec3 boltColor = vec3(0.16, 0.17, 0.19) * (0.6 + 0.6 * dome);
      boltColor += vec3(0.6) * pow(max(dot(normalize(d + 1e-4), uLight), 0.0), 8.0) * dome * 0.5;
      vec2 slotDir = vec2(cos(angle + 0.8), sin(angle + 0.8));
      float slot = 1.0 - aastepPx(0.003, abs(dot(d, vec2(-slotDir.y, slotDir.x))), px);
      boltColor *= 1.0 - 0.7 * slot;
      steel = mix(steel, boltColor, head);
    }
    float socket = aastepPx(boltRadius, dist, px) * (1.0 - aastepPx(boltRadius + 0.006, dist, px));
    steel *= 1.0 - 0.6 * socket;
  }

  // Four notches cut into the inner edge.
  float notchAngle = abs(fract(turn * 4.0) - 0.5);
  float notch = (1.0 - aastep(0.035, notchAngle)) * band(r, uHousingInner, uHousingInner + 0.03);
  steel *= 1.0 - 0.75 * notch;

  // Inner shadow where the metal meets the light, plus a little glow spilling onto it.
  steel *= 0.55 + 0.45 * smoothstep(uHousingInner, uHousingInner + 0.06, r);
  steel += uRingColor * exp(-(r - uHousingInner) * 40.0) * 0.25 * uRingIntensity * glowLevel();

  return vec4(steel, mask);
}

vec4 coils(vec2 p, float r, float a) {
  float plate = band(r, uCoilInner - 0.012, uHousingInner + 0.004);
  if (plate <= 0.0) return vec4(0.0);
  vec3 color = vec3(0.012, 0.016, 0.022); // dark backplate in the gaps

  float count = uCoilCount;
  float segment = TAU / count;
  float shifted = a + PI + segment * 0.5;
  float index = floor(shifted / segment);
  float local = (fract(shifted / segment) - 0.5) * segment; // angle from the segment centre

  // Constant-width gaps make the segments trapezoids, wider at the outer edge.
  float gap = 0.016;
  float sideDistance = (segment * 0.5 - abs(local)) * r - gap * 0.5;
  float inSegment = aastep(0.0, sideDistance) * band(r, uCoilInner, uCoilOuter);

  // Chase: one bright segment sweeps around, with a fading tail (THINKING).
  float head = fract(uTime * uSpeed * 0.55) * count;
  float behind = mod(head - index + count, count);
  float chase = exp(-behind * 0.85) * uChase;
  float light = uCoilLight + chase * 1.4 + uLevel * 0.35;

  // Copper winding: fine parallel wires across each segment, rounded by a fake bulge.
  float wire = 0.5 + 0.5 * sin(local * r * 540.0);
  float across = (r - uCoilInner) / (uCoilOuter - uCoilInner);
  float bulge = sin(clamp(across, 0.0, 1.0) * PI);
  vec3 copper = vec3(0.62, 0.34, 0.16) * (0.35 + 0.65 * wire) * (0.45 + 0.55 * bulge);
  copper += vec3(0.9, 0.6, 0.35) * pow(wire, 8.0) * pow(max(dot(p / max(r, 1e-4), uLight), 0.0), 3.0) * 0.25;
  // Copper keeps its colour when lit; the state colour rides on the wire highlights.
  vec3 segmentColor = copper * (0.45 + light * 0.45) + uRingColor * light * 0.3 * pow(wire, 3.0);
  // The chased segment glows in the state colour (THINKING sweep).
  segmentColor += uRingColor * uRingColor * chase * (0.45 + 0.55 * wire) * 0.9;
  float rim = 1.0 - smoothstep(0.0, 0.006, sideDistance);
  segmentColor *= 1.0 - 0.5 * rim;
  color = mix(color, segmentColor, inSegment);

  // Light leaking out of the gaps between segments.
  float gapGlow = (1.0 - inSegment) * band(r, uCoilInner, uCoilOuter);
  float leak = exp(-max(sideDistance + gap * 0.5, 0.0) * 120.0);
  color += uRingColor * gapGlow * leak * (0.25 + 0.6 * light) * glowLevel() * uRingIntensity;

  return vec4(color, plate);
}

vec4 light(vec2 p, float r, float a) {
  float disc = 1.0 - aastep(uCoilInner, r);
  if (disc <= 0.0) return vec4(0.0);
  float level = glowLevel();

  // Dark plate behind the light, lit faintly from the centre.
  vec3 color = vec3(0.01, 0.014, 0.02) + uCoreTint * exp(-r * 5.0) * 0.08 * uCoreIntensity;

  // The thin, perfectly round inner glow ring with a soft halo.
  float pulse = 1.0 + 0.45 * uRingPulse * sin(uTime * uSpeed * 4.0);
  float ring = exp(-pow((r - uRingRadius) / uRingWidth, 2.0));
  float halo = exp(-abs(r - uRingRadius) * 28.0);
  vec3 ringColor = mix(uRingColor, vec3(1.0), 0.55);
  color += ringColor * (ring * 1.7 + halo * 0.3) * uRingIntensity * pulse * level;

  // Core: a very bright white centre tinted toward the state colour at its edge.
  float coreShape;
  if (uTriangle > 0.5) {
    float tri = sdTriangle(vec2(p.x, -p.y), uCoreRadius * 0.85);
    float outline = exp(-abs(tri) * 90.0);
    coreShape = max(outline * 0.9, 1.0 - aastep(uCoreRadius * 0.35, r));
  } else {
    coreShape = 1.0 - smoothstep(uCoreRadius * 0.55, uCoreRadius, r);
  }
  float hot = exp(-r / (uCoreRadius * 0.45));
  vec3 core = mix(uCoreTint, vec3(1.0), hot) * (coreShape * 1.0 + hot * 0.9);

  // Rays and lens glow radiating from the centre.
  float rays = pow(abs(sin(a * 9.0 + noise(vec2(a * 3.0, uTime * 0.2 * uSpeed)) * 2.0)), 24.0);
  float lens = exp(-r * 7.0);
  core += uCoreTint * (rays * exp(-r * 6.0) * 0.35 + lens * 0.35);

  color += core * uCoreIntensity * level;
  return vec4(color, disc);
}

vec4 glass(vec2 p, float r) {
  float cover = 1.0 - aastep(uHousingInner, r);
  if (cover <= 0.0) return vec4(0.0);
  // A slow diagonal reflection sweeping across the cover every few seconds.
  float sweepPos = mod(uTime * uSpeed * 0.18, 3.2) - 1.6;
  float along = dot(p, normalize(vec2(1.0, 0.65)));
  float sweep = exp(-pow((along - sweepPos) / 0.07, 2.0)) * 0.10;
  float rimHighlight = exp(-(uHousingInner - r) * 60.0) * 0.06;
  float curvature = (1.0 - r / uHousingInner) * 0.02; // faint dome sheen
  vec3 tint = vec3(0.75, 0.9, 1.0);
  return vec4(tint * (sweep + rimHighlight + curvature) * cover, 1.0);
}

void main() {
  vec2 p = (vUv - 0.5) * uPlaneSize;
  float r = length(p);
  float a = atan(p.y, p.x);
  vec4 color;
  if (uLayer == 0) color = housing(p, r, a);
  else if (uLayer == 1) color = coils(p, r, a);
  else if (uLayer == 2) color = light(p, r, a);
  else color = glass(p, r);
  if (color.a <= 0.0) discard;
  gl_FragColor = color;
}
