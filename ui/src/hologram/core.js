// Three.js scene for the holographic lens HUD: concentric ring layers at different depths
// (true parallax), each rotating at its own speed and some tilted a few degrees, a glowing
// iris core, lens-flare hot spots, and an outer layer of spokes, blocks, unreadable readouts,
// particles and line fragments drifting outward. Then bloom and a final pass (chromatic
// softness, tone curve, error flicker, vignette). All geometry is generated in geometry.js;
// all tunables live in states.js.
//
// Inputs (unchanged from the arc reactor it replaces): setState(name) with the visual state,
// setLevel('mic' | 'tts', level), setFit(fraction), setReducedMotion(bool).
//
// Performance: one draw call per layer, additive blending with no depth buffer work, the
// pixel ratio capped at 2, calm states rendered at ~30 fps on a timer (a live rAF loop keeps
// Chromium's whole compositor ticking at 60 Hz), nothing at all while the window is hidden,
// fewer layers and particles in the compact window, and everything disposed on rebuild.

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

import { GENERATORS, flares as flareQuads, fragments, particles, random, scaledSpec } from './geometry.js';
import commonGlsl from './shaders/common.glsl?raw';
import driftFrag from './shaders/drift.frag?raw';
import driftVert from './shaders/drift.vert?raw';
import finalFrag from './shaders/final.frag?raw';
import glowFrag from './shaders/glow.frag?raw';
import glowVert from './shaders/glow.vert?raw';
import hudFrag from './shaders/hud.frag?raw';
import hudVert from './shaders/hud.vert?raw';
import paletteGlsl from './shaders/palette.glsl?raw';
import quadVert from './shaders/quad.vert?raw';
import { LAYERS, LevelEnvelope, MOTION, PALETTES, STATE_PARAMS, THEME, easeAmount, easeParams, fadeAmount } from './states.js';

const CALM_STATES = new Set(['idle', 'muted', 'offline']);
const FRAG_PREFIX = `${commonGlsl}\n${paletteGlsl}\n`;
const DEG = Math.PI / 180;

function paletteUniform(palette) {
  return { value: [palette.core, palette.mid, palette.edge].map((c) => new THREE.Vector3(...c)) };
}

function toGeometry(data) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
  if (data.edge) geometry.setAttribute('aEdge', new THREE.BufferAttribute(data.edge, 1));
  if (data.uv) geometry.setAttribute('aUv', new THREE.BufferAttribute(data.uv, 2));
  if (data.seed) {
    geometry.setAttribute('aSeed', new THREE.BufferAttribute(data.seed, 1));
    geometry.setAttribute('aOffset', new THREE.BufferAttribute(data.offset, 1));
  }
  return geometry;
}

export class Hologram {
  /** @param {HTMLCanvasElement} canvas */
  constructor(canvas, { theme = THEME, layers = LAYERS, reducedMotion = false } = {}) {
    this.canvas = canvas;
    this.theme = theme;
    this.layerSpecs = layers;
    this.motion = reducedMotion ? MOTION.reduced : MOTION.normal;
    this.stateName = 'offline';
    this.params = { ...STATE_PARAMS.offline };
    this.levels = { mic: 0, tts: 0 };
    this.envelope = new LevelEnvelope();
    this.mouse = new THREE.Vector2();
    this.look = new THREE.Vector2();
    this.fit = 0.6;
    this.compact = null;
    this.timer = new THREE.Timer();
    this.time = 0;
    this.phases = { chase: 0, drift: 0, wave: 0 };
    this.rafHandle = null;
    this.timeoutHandle = null;
    this.running = false;
    this.layers = [];

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power' });
    this.renderer.setClearColor(new THREE.Color(theme.background), 1);
    this.renderer.toneMapping = THREE.NoToneMapping;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(theme.camera.fovDeg, 1, 0.05, 100);
    this.root = new THREE.Group();
    this.scene.add(this.root);

    // Shared by every HUD material (same objects, so one update reaches all of them).
    this.shared = {
      uBlue: paletteUniform(PALETTES.blue),
      uOrange: paletteUniform(PALETTES.orange),
      uRed: paletteUniform(PALETTES.red),
      uOrangeMix: { value: 0 },
      uTwoTone: { value: 0 },
      uAccentAngle: { value: theme.accentAngle },
      uChase: { value: 0 },
      uChaseSegments: { value: theme.chaseSegments },
      uChasePhase: { value: 0 },
      uError: { value: 0 },
      uSaturation: { value: 1 },
      uGradientRadius: { value: theme.gradientRadius },
      uGradientMid: { value: theme.gradientMid },
      uIntensity: { value: 0 },
      uLevel: { value: 0 },
      uWave: { value: 0 },
    };
    this.driftShared = {
      uDriftPhase: { value: 0 },
      uPixelRatio: { value: 1 },
    };

    this.buildPost();
    this.onPointerMove = (event) => {
      this.mouse.set((event.clientX / window.innerWidth) * 2 - 1, -(event.clientY / window.innerHeight) * 2 + 1);
    };
    window.addEventListener('pointermove', this.onPointerMove);
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(canvas.parentElement ?? canvas);
    this.onVisibility = () => (document.hidden ? this.stop() : this.start());
    document.addEventListener('visibilitychange', this.onVisibility);
    this.resize();
  }

  // -- scene ----------------------------------------------------------------------------

  material(vertexShader, fragmentShader, uniforms) {
    return new THREE.ShaderMaterial({
      vertexShader,
      fragmentShader: FRAG_PREFIX + fragmentShader,
      uniforms: { ...this.shared, ...uniforms },
      transparent: true,
      depthWrite: false,
      depthTest: false,
      blending: THREE.AdditiveBlending,
    });
  }

  /** (Re)build every layer for the current detail level. Disposes what was there. */
  build(compact) {
    this.disposeScene();
    this.compact = compact;
    const theme = this.theme;
    const counts = compact ? theme.compactCounts : 1;
    const rand = random(7);

    this.layers = [];
    for (const spec of this.layerSpecs) {
      if (compact && spec.compact === false) continue;
      const scaled = scaledSpec(spec, counts);
      const data = GENERATORS[spec.kind](scaled, random(Math.floor(rand() * 1e9)));
      const geometry = toGeometry(data);
      let object;
      if (spec.kind === 'flares') {
        object = new THREE.Mesh(
          geometry,
          this.material(glowVert, glowFrag, { uAlpha: { value: spec.alpha }, uFlare: { value: 1 }, uScale: { value: 1 } }),
        );
      } else {
        const uniforms = {
          uAlpha: { value: spec.alpha },
          uOutline: { value: spec.kind === 'arcs' ? 1 : 0 },
          uScale: { value: 1 },
        };
        const mat = this.material(hudVert, hudFrag, uniforms);
        object = data.mode === 'lines' ? new THREE.LineSegments(geometry, mat) : new THREE.Mesh(geometry, mat);
      }
      // Outer group: depth and tilt (fixed). Inner object: rotation about the disc axis.
      const holder = new THREE.Group();
      holder.position.z = spec.z;
      const [tx, ty] = spec.tilt ?? [0, 0];
      holder.rotation.set(tx * DEG, ty * DEG, 0);
      object.rotation.z = rand() * Math.PI * 2;
      holder.add(object);
      this.root.add(holder);
      this.layers.push({ spec, object, uScale: object.material.uniforms.uScale });
    }

    // The bright central iris glow.
    const core = new THREE.Mesh(
      toGeometry(flareQuads({ radius: 0, count: 1, size: theme.core.radius * 5 })),
      this.material(glowVert, glowFrag, {
        uAlpha: { value: theme.core.intensity },
        uFlare: { value: 0 },
        uScale: { value: 1 },
      }),
    );
    core.position.z = 0.32;
    this.root.add(core);
    this.core = core;

    // Particles and fragments drifting outward through the depth of the scene.
    const particleCount = Math.round(theme.particles.count * counts * this.motion.particles);
    const fragmentCount = Math.round(theme.fragments.count * counts * this.motion.particles);
    const dots = particles({ ...theme.particles, count: Math.max(1, particleCount) }, random(11));
    const bits = fragments({ ...theme.fragments, count: Math.max(1, fragmentCount) }, random(12));
    const driftUniforms = (spec, points) => ({
      ...this.driftShared,
      uInner: { value: spec.inner },
      uOuter: { value: spec.outer },
      uSize: { value: theme.particles.size },
      uAlpha: { value: points ? 0.9 : 0.55 },
      uPoints: { value: points ? 1 : 0 },
    });
    this.root.add(new THREE.Points(toGeometry(dots), this.material(driftVert, driftFrag, driftUniforms(dots, true))));
    this.root.add(new THREE.LineSegments(toGeometry(bits), this.material(driftVert, driftFrag, driftUniforms(bits, false))));
  }

  disposeScene() {
    this.root.traverse((object) => {
      object.geometry?.dispose();
      object.material?.dispose();
    });
    this.root.clear();
    this.layers = [];
  }

  buildPost() {
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    const { radius, threshold } = this.theme.bloom;
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 1, radius, threshold);
    this.composer.addPass(this.bloom);
    this.finalPass = new ShaderPass({
      uniforms: {
        tDiffuse: { value: null },
        uTime: { value: 0 },
        uFlicker: { value: 0 },
        uChroma: { value: this.theme.chroma },
        uResolution: { value: new THREE.Vector2(1, 1) },
        uVignette: { value: this.theme.vignette },
      },
      vertexShader: quadVert,
      fragmentShader: `${commonGlsl}\n${finalFrag}`,
    });
    // ShaderPass clones the uniforms it is given: keep a handle on its own copy.
    this.finalUniforms = this.finalPass.uniforms;
    this.composer.addPass(this.finalPass);
  }

  // -- public API --------------------------------------------------------------------

  /** One of the STATE_PARAMS keys; the look cross-fades toward it. */
  setState(name) {
    this.stateName = STATE_PARAMS[name] ? name : 'idle';
  }

  setLevel(source, level) {
    if (source === 'mic' || source === 'tts') this.levels[source] = level;
  }

  /** Fraction of the smaller window dimension the main dial's diameter should fill. */
  setFit(fit) {
    this.fit = fit;
    this.resize();
  }

  setReducedMotion(reduced) {
    this.motion = reduced ? MOTION.reduced : MOTION.normal;
    this.build(this.compact ?? false); // particle counts depend on it
  }

  start() {
    if (this.running || document.hidden) return;
    this.running = true;
    this.timer.reset();
    this.schedule();
  }

  stop() {
    this.running = false;
    cancelAnimationFrame(this.rafHandle);
    clearTimeout(this.timeoutHandle);
  }

  schedule() {
    if (!this.running) return;
    const frame = () => {
      this.rafHandle = requestAnimationFrame((timestamp) => this.tick(timestamp));
    };
    if (CALM_STATES.has(this.stateName)) {
      this.timeoutHandle = setTimeout(frame, this.theme.calmFrameMs);
    } else {
      frame();
    }
  }

  dispose() {
    this.stop();
    window.removeEventListener('pointermove', this.onPointerMove);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.resizeObserver.disconnect();
    this.disposeScene();
    this.bloom.dispose();
    this.finalPass.material?.dispose();
    this.composer.dispose?.();
    this.renderer.dispose();
  }

  // -- internals ------------------------------------------------------------------------

  resize() {
    const host = this.canvas.parentElement ?? this.canvas;
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    const compact = Math.min(width, height) < this.theme.compactBelowPx;
    if (compact !== this.compact) this.build(compact);
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    this.renderer.setPixelRatio(pixelRatio);
    this.renderer.setSize(width, height, false);
    this.composer.setPixelRatio(pixelRatio);
    this.composer.setSize(width, height);
    this.camera.aspect = width / height;
    // Distance at which the main dial (diameter 2) fills `fit` of the smaller dimension.
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(this.theme.camera.fovDeg / 2));
    const limiting = Math.min(1, this.camera.aspect);
    this.distance = 2 / (this.fit * 2 * tanHalf * limiting);
    this.camera.updateProjectionMatrix();
    this.driftShared.uPixelRatio.value = pixelRatio;
    this.finalUniforms.uResolution.value.set(width * pixelRatio, height * pixelRatio);
    this.placeCamera();
  }

  placeCamera() {
    const camera = this.theme.camera;
    const drift = camera.drift * this.motion.drift;
    const ax = this.look.x * camera.parallax * this.motion.parallax + Math.sin(this.time * camera.driftSpeed) * drift;
    const ay = this.look.y * camera.parallax * this.motion.parallax + Math.sin(this.time * camera.driftSpeed * 0.73 + 1.3) * drift * 0.7;
    const d = this.distance ?? 6;
    this.camera.position.set(Math.sin(ax) * d, Math.sin(ay) * d, Math.cos(ax) * Math.cos(ay) * d);
    this.camera.lookAt(0, 0, 0);
  }

  tick(timestamp) {
    if (!this.running) return;
    this.timer.update(timestamp);
    const dt = Math.min(this.timer.getDelta(), 0.1);
    this.time += dt;
    this.update(dt);
    this.composer.render(dt);
    this.schedule();
  }

  update(dt) {
    this.params = easeParams(this.params, STATE_PARAMS[this.stateName], fadeAmount(dt, this.theme.fadeMs));
    const p = this.params;
    const motion = this.motion;

    const source = this.stateName === 'listening' ? 'mic' : this.stateName === 'speaking' ? 'tts' : null;
    const level = this.envelope.update(source ? this.levels[source] : 0, dt) * p.levelGain;
    const pulse = level * p.pulse * motion.pulse;

    const breathe = 1 - p.breathe * 0.22 * (0.5 + 0.5 * Math.sin(this.time * 1.2 * motion.speed));
    const s = this.shared;
    s.uIntensity.value = p.intensity * breathe * (1 + level * 0.9 * motion.pulse);
    s.uOrangeMix.value = p.orange;
    s.uTwoTone.value = p.twoTone;
    s.uChase.value = p.chase;
    s.uError.value = p.error;
    s.uSaturation.value = p.saturation;
    s.uLevel.value = pulse;

    const spin = p.spin * motion.speed;
    this.phases.chase += dt * this.theme.chaseSpeed * spin;
    this.phases.drift += dt * this.theme.particles.speed * p.drift * motion.speed;
    this.phases.wave = (this.phases.wave + dt * (0.5 + pulse * 0.8)) % 1;
    s.uChasePhase.value = this.phases.chase;
    s.uWave.value = this.phases.wave;
    this.driftShared.uDriftPhase.value = this.phases.drift;

    for (const layer of this.layers) {
      layer.object.rotation.z += dt * layer.spec.speed * spin;
      // Rings breathe outward with the voice; inner ones (the iris) the most.
      const radius = layer.spec.radius ?? layer.spec.inner ?? 1;
      layer.uScale.value = 1 + pulse * 0.06 * (1.2 - Math.min(radius, 1));
    }
    this.core.material.uniforms.uScale.value = p.coreSize * (1 + pulse * 0.55);

    this.look.lerp(this.mouse, easeAmount(0.9, dt));
    this.placeCamera();

    this.bloom.strength = p.bloomStrength * (1 + level * 0.5);
    this.finalUniforms.uTime.value = this.time;
    this.finalUniforms.uFlicker.value = p.flicker * motion.flicker;
  }
}
