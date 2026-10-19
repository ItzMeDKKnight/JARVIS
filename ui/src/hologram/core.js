// Three.js scene for the arc reactor: layered shader planes (for parallax depth), aura,
// HUD rings and particles, then bloom and a final pass (shimmer, scanlines, flicker).
//
// Performance: renders at ~20 fps in calm states and 60 fps when active, stops entirely
// while the window is hidden, and caps the pixel ratio at 2. Calm states wake on a timer
// instead of requestAnimationFrame: a live rAF loop keeps Chromium's whole compositor
// pipeline (browser, GPU and renderer processes) ticking at 60 Hz even when nothing is drawn.

import * as THREE from 'three';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';

import auraFrag from './shaders/aura.frag?raw';
import commonGlsl from './shaders/common.glsl?raw';
import finalFrag from './shaders/final.frag?raw';
import hudFrag from './shaders/hud.frag?raw';
import particlesFrag from './shaders/particles.frag?raw';
import particlesVert from './shaders/particles.vert?raw';
import quadVert from './shaders/quad.vert?raw';
import reactorFrag from './shaders/reactor.frag?raw';
import {
  LevelEnvelope,
  MOTION,
  STATE_PARAMS,
  THEME,
  easeAmount,
  easeParams,
} from './states.js';

const FOV_DEG = 30;
const REACTOR_PLANE = 2.2; // reactor layers span [-1.1, 1.1] reactor units
const AURA_PLANE = 6.0;
const HUD_PLANE = 5.0;
const PARTICLE_COUNT = 260;
const CALM_STATES = new Set(['idle', 'muted', 'offline']);
const CALM_FRAME_MS = 50; // ~20 fps in calm states
const STATE_EASE_PER_SECOND = 0.985; // ~95% of the way in under a second
const PARALLAX_MAX_RAD = 0.14;

const LAYERS = [
  { layer: 0, z: 0.0 }, // housing
  { layer: 1, z: 0.035 }, // coils
  { layer: 2, z: 0.06 }, // light
  { layer: 3, z: 0.11, additive: true }, // glass
];

function vec3(rgb) {
  return new THREE.Vector3(rgb[0], rgb[1], rgb[2]);
}

function material(fragment, uniforms, { additive = false } = {}) {
  return new THREE.ShaderMaterial({
    vertexShader: quadVert,
    fragmentShader: `${commonGlsl}\n${fragment}`,
    uniforms,
    transparent: true,
    depthWrite: false,
    depthTest: false,
    blending: additive ? THREE.AdditiveBlending : THREE.NormalBlending,
  });
}

export class Hologram {
  /** @param {HTMLCanvasElement} canvas */
  constructor(canvas, { theme = THEME, reducedMotion = false } = {}) {
    this.canvas = canvas;
    this.theme = theme;
    this.motion = reducedMotion ? MOTION.reduced : MOTION.normal;
    this.stateName = 'offline';
    this.params = { ...STATE_PARAMS.offline };
    this.levels = { mic: 0, tts: 0 };
    this.envelope = new LevelEnvelope();
    this.mouse = new THREE.Vector2();
    this.fit = 0.6;
    this.timer = new THREE.Timer();
    this.time = 0;
    this.rafHandle = null;
    this.timeoutHandle = null;
    this.running = false;

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'low-power' });
    this.renderer.setClearColor(0x03070c, 1);
    this.renderer.toneMapping = THREE.NoToneMapping;
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(FOV_DEG, 1, 0.1, 100);
    this.group = new THREE.Group();
    this.scene.add(this.group);

    this.buildReactor();
    this.buildSurroundings();
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

  buildReactor() {
    const p = this.theme.proportions;
    this.reactorUniforms = {
      uLayer: { value: 0 },
      uTime: { value: 0 },
      uSpeed: { value: this.motion.speed },
      uPlaneSize: { value: REACTOR_PLANE },
      uLight: { value: new THREE.Vector2(0.55, 0.65).normalize() },
      uCoreIntensity: { value: 0 },
      uRingIntensity: { value: 0 },
      uCoilLight: { value: 0 },
      uBreathe: { value: 0 },
      uChase: { value: 0 },
      uRingPulse: { value: 0 },
      uLevel: { value: 0 },
      uCoreTint: { value: new THREE.Vector3(1, 1, 1) },
      uRingColor: { value: vec3(this.theme.colors.ring) },
      uTriangle: { value: this.theme.coreShape === 'triangle' ? 1 : 0 },
      uCoilCount: { value: this.theme.coilCount },
      uHousingOuter: { value: p.housingOuter },
      uHousingInner: { value: p.housingInner },
      uCoilOuter: { value: p.coilOuter },
      uCoilInner: { value: p.coilInner },
      uRingRadius: { value: p.ringRadius },
      uRingWidth: { value: p.ringWidth },
      uCoreRadius: { value: p.coreRadius },
    };
    const geometry = new THREE.PlaneGeometry(REACTOR_PLANE, REACTOR_PLANE);
    this.layerMaterials = LAYERS.map(({ layer, z, additive }) => {
      // Each layer shares the uniform objects (same values) but has its own uLayer.
      const uniforms = { ...this.reactorUniforms, uLayer: { value: layer } };
      const mat = material(reactorFrag, uniforms, { additive });
      const mesh = new THREE.Mesh(geometry, mat);
      mesh.position.z = z;
      mesh.renderOrder = 10 + layer;
      this.group.add(mesh);
      return mat;
    });
  }

  buildSurroundings() {
    this.auraUniforms = {
      uTime: { value: 0 },
      uSpeed: { value: this.motion.speed },
      uRipple: { value: this.motion.ripple },
      uPlaneSize: { value: AURA_PLANE },
      uIntensity: { value: 0 },
      uLevel: { value: 0 },
      uColor: { value: new THREE.Vector3(1, 1, 1) },
      uStartRadius: { value: this.theme.proportions.housingOuter },
    };
    const aura = new THREE.Mesh(
      new THREE.PlaneGeometry(AURA_PLANE, AURA_PLANE),
      material(auraFrag, this.auraUniforms, { additive: true }),
    );
    aura.position.z = -0.05;
    aura.renderOrder = 2;
    this.group.add(aura);
    this.aura = aura;

    this.hudUniforms = {
      uTime: { value: 0 },
      uSpeed: { value: this.motion.speed },
      uPlaneSize: { value: HUD_PLANE },
      uIntensity: { value: 0.35 },
      uColor: { value: vec3(this.theme.colors.ring) },
    };
    const hud = new THREE.Mesh(
      new THREE.PlaneGeometry(HUD_PLANE, HUD_PLANE),
      material(hudFrag, this.hudUniforms, { additive: true }),
    );
    hud.position.z = -0.12;
    hud.renderOrder = 1;
    this.group.add(hud);

    const count = Math.round(PARTICLE_COUNT * this.motion.particles);
    const positions = new Float32Array(count * 3);
    const seeds = new Float32Array(count);
    for (let i = 0; i < count; i += 1) {
      const angle = Math.random() * Math.PI * 2;
      const radius = 1.1 + Math.pow(Math.random(), 0.7) * 1.6;
      positions.set([Math.cos(angle) * radius, Math.sin(angle) * radius, (Math.random() - 0.5) * 0.6], i * 3);
      seeds[i] = Math.random();
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 1));
    this.particleUniforms = {
      uTime: { value: 0 },
      uSpeed: { value: this.motion.speed },
      uPixelRatio: { value: 1 },
      uColor: { value: vec3(this.theme.colors.ring) },
      uIntensity: { value: 0.5 },
    };
    const points = new THREE.Points(
      geometry,
      new THREE.ShaderMaterial({
        vertexShader: particlesVert,
        fragmentShader: particlesFrag,
        uniforms: this.particleUniforms,
        transparent: true,
        depthWrite: false,
        depthTest: false,
        blending: THREE.AdditiveBlending,
      }),
    );
    points.renderOrder = 3;
    this.group.add(points);
  }

  buildPost() {
    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 1, this.theme.bloom.radius, this.theme.bloom.threshold);
    this.composer.addPass(this.bloom);
    this.finalUniforms = {
      tDiffuse: { value: null },
      uTime: { value: 0 },
      uResolution: { value: new THREE.Vector2(1, 1) },
      uCenter: { value: new THREE.Vector2(0.5, 0.5) },
      uReactorRadius: { value: 100 },
      uShimmer: { value: 1 },
      uScanlines: { value: 1 },
      uFlicker: { value: 0 },
      uSaturation: { value: 1 },
      uErrorMix: { value: 0 },
      uErrorColor: { value: vec3(this.theme.colors.error) },
    };
    this.finalPass = new ShaderPass({
      uniforms: this.finalUniforms,
      vertexShader: quadVert,
      fragmentShader: `${commonGlsl}\n${finalFrag}`,
    });
    // ShaderPass clones the uniforms it is given: keep a handle on its own copy.
    this.finalUniforms = this.finalPass.uniforms;
    this.composer.addPass(this.finalPass);
  }

  // -- public API --------------------------------------------------------------------

  /** One of the STATE_PARAMS keys; the look eases toward it. */
  setState(name) {
    this.stateName = STATE_PARAMS[name] ? name : 'idle';
  }

  setLevel(source, level) {
    if (source === 'mic' || source === 'tts') this.levels[source] = level;
  }

  /** Fraction of the smaller window dimension the reactor's diameter should fill. */
  setFit(fit) {
    this.fit = fit;
    this.resize();
  }

  setReducedMotion(reduced) {
    this.motion = reduced ? MOTION.reduced : MOTION.normal;
    for (const uniforms of [this.reactorUniforms, this.auraUniforms, this.hudUniforms, this.particleUniforms]) {
      uniforms.uSpeed.value = this.motion.speed;
    }
    this.auraUniforms.uRipple.value = this.motion.ripple;
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
      this.timeoutHandle = setTimeout(frame, CALM_FRAME_MS);
    } else {
      frame();
    }
  }

  dispose() {
    this.stop();
    window.removeEventListener('pointermove', this.onPointerMove);
    document.removeEventListener('visibilitychange', this.onVisibility);
    this.resizeObserver.disconnect();
    this.renderer.dispose();
  }

  // -- internals ------------------------------------------------------------------------

  resize() {
    const host = this.canvas.parentElement ?? this.canvas;
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    this.renderer.setPixelRatio(pixelRatio);
    this.renderer.setSize(width, height, false);
    this.composer.setPixelRatio(pixelRatio);
    this.composer.setSize(width, height);
    this.camera.aspect = width / height;
    // Distance at which the reactor (diameter 2) fills `fit` of the smaller dimension.
    const tanHalf = Math.tan(THREE.MathUtils.degToRad(FOV_DEG / 2));
    const limiting = Math.min(1, this.camera.aspect);
    this.camera.position.z = 2 / (this.fit * 2 * tanHalf * limiting);
    this.camera.updateProjectionMatrix();
    const visibleHeight = 2 * this.camera.position.z * tanHalf;
    this.finalUniforms.uResolution.value.set(width * pixelRatio, height * pixelRatio);
    this.finalUniforms.uReactorRadius.value = (height * pixelRatio) / visibleHeight;
    this.particleUniforms.uPixelRatio.value = pixelRatio;
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
    const target = STATE_PARAMS[this.stateName];
    this.params = easeParams(this.params, target, easeAmount(STATE_EASE_PER_SECOND, dt));
    const p = this.params;

    const source = this.stateName === 'listening' ? 'mic' : this.stateName === 'speaking' ? 'tts' : null;
    const level = this.envelope.update(source ? this.levels[source] : 0, dt) * p.levelGain;

    const r = this.reactorUniforms;
    r.uTime.value = this.time;
    r.uCoreIntensity.value = p.coreIntensity;
    r.uRingIntensity.value = p.ringIntensity;
    r.uCoilLight.value = p.coilLight;
    r.uBreathe.value = p.breathe;
    r.uChase.value = p.chase;
    r.uRingPulse.value = p.ringPulse;
    r.uLevel.value = level;
    r.uCoreTint.value.set(...p.coreTint);
    r.uRingColor.value.set(...p.ringColor);

    // The light (specular highlights) and the tilt both follow the mouse.
    const parallax = PARALLAX_MAX_RAD * this.motion.parallax;
    const ease = easeAmount(0.9, dt);
    this.group.rotation.y += (this.mouse.x * parallax - this.group.rotation.y) * ease;
    this.group.rotation.x += (-this.mouse.y * parallax - this.group.rotation.x) * ease;
    r.uLight.value.set(0.55 + this.mouse.x * 0.6, 0.65 + this.mouse.y * 0.6).normalize();

    this.auraUniforms.uTime.value = this.time;
    this.auraUniforms.uIntensity.value = p.auraIntensity;
    // A full-screen shader with nothing to show is pure cost (idle, thinking, muted).
    this.aura.visible = p.auraIntensity > 0.002;
    this.auraUniforms.uLevel.value = level;
    this.auraUniforms.uColor.value.set(...p.auraColor);
    this.hudUniforms.uTime.value = this.time;
    this.hudUniforms.uColor.value.set(...p.ringColor);
    this.particleUniforms.uTime.value = this.time;
    this.particleUniforms.uColor.value.set(...p.ringColor);

    this.bloom.strength = p.bloomStrength * (1 + level * 0.25);
    const f = this.finalUniforms;
    f.uTime.value = this.time;
    f.uFlicker.value = p.flicker * this.motion.flicker;
    f.uShimmer.value = this.motion.ripple * (0.5 + p.coreIntensity);
    f.uSaturation.value = p.saturation;
    f.uErrorMix.value = p.errorMix;
  }
}
