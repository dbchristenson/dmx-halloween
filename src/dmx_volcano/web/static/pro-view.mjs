import * as THREE from './vendor/three/three.module.min.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';
import { GLTFLoader } from './vendor/three/GLTFLoader.js';
import { fixtureOutput } from './pro-lighting.mjs';
import { FlameEffect, CO2Effect, CO2_HEIGHT } from './pro-effects.mjs';

const MODEL_URL = new URL('./models/volcano.glb', import.meta.url);
const MACHINES_URL = new URL('./models/pyro-fixtures.glb', import.meta.url);
const HOME = new THREE.Vector3(6.8, 7.5, 8.6);
const TARGET = new THREE.Vector3(0, 2.0, 0);

export async function createVolcanoView(host, layout, onError) {
  const view = new VolcanoView(host, layout, onError);
  try {
    await view.load();
    return view;
  } catch (error) {
    view.dispose();
    throw error;
  }
}

class VolcanoView {
  constructor(host, layout, onError) {
    this.host = host;
    this.layout = layout;
    this.onError = onError;
    this.fixtures = [];
    this.preview = null;
    this.previewTimer = 0;
    this.latestFrame = null;
    this.active = false;
    this.inView = true;
    this.disposed = false;
    this.raf = 0;
    this.lastDraw = 0;
    this.reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
    this.abort = new AbortController();
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x14212c);
    this.scene.fog = new THREE.Fog(0x14212c, 35, 70);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 100);
    this.camera.position.copy(HOME);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'low-power' });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.75));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    const canvas = this.renderer.domElement;
    canvas.id = 'proCanvas';
    canvas.tabIndex = 0;
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', '3D volcano with live surface lighting. Drag or swipe to orbit. Arrow keys rotate; plus and minus zoom; Home resets.');
    canvas.setAttribute('aria-describedby', 'proInstructions');
    host.appendChild(canvas);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.target.copy(TARGET);
    this.controls.enablePan = false;
    this.controls.enableDamping = !this.reducedMotion.matches;
    this.controls.dampingFactor = 0.09;
    this.controls.rotateSpeed = 0.65;
    this.controls.minDistance = 1.0;
    this.controls.maxDistance = 45;
    this.controls.minPolarAngle = 0.12;
    this.controls.maxPolarAngle = Math.PI / 2 - 0.05;
    this.controls.update();
    this.controls.saveState();
    const signal = this.abort.signal;
    canvas.addEventListener('keydown', event => this.onKey(event), { signal });
    canvas.addEventListener('webglcontextlost', event => {
      event.preventDefault();
      this.setActive(false);
      onError('The 3D graphics connection was interrupted. Retry to reload the stage.');
    }, { signal });
    this.reducedMotion.addEventListener('change', () => {
      this.controls.enableDamping = !this.reducedMotion.matches;
    }, { signal });
    document.addEventListener('visibilitychange', () => this.schedule(), { signal });
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(host);
    this.intersectionObserver = new IntersectionObserver(entries => {
      this.inView = entries[0].isIntersecting;
      this.schedule();
    });
    this.intersectionObserver.observe(host);
  }

  async load() {
    // Abort stalled loads so the user always gets a retry path.
    const response = await fetch(MODEL_URL, { signal: AbortSignal.timeout(20000) });
    if (!response.ok) throw new Error(`Volcano model returned HTTP ${response.status}`);
    const gltf = await new GLTFLoader().parseAsync(await response.arrayBuffer(), MODEL_URL.href);
    this.model = gltf.scene;
    this.model.traverse(object => {
      if (!object.isMesh) return;
      object.castShadow = true;
      object.receiveShadow = true;
    });
    this.scene.add(this.model);
    const machinesResponse = await fetch(MACHINES_URL, { signal: AbortSignal.timeout(20000) });
    if (!machinesResponse.ok) throw new Error(`Effect models returned HTTP ${machinesResponse.status}`);
    const machines = await new GLTFLoader().parseAsync(await machinesResponse.arrayBuffer(), MACHINES_URL.href);
    this.machineTemplates = machines.scene;
    this.machineTemplates.visible = false;
    this.scene.add(this.machineTemplates); // Own template resources for cleanup, too.
    this.buildStage();
    for (const fixture of this.layout.fixtures) this.addFixture(fixture);
    this.resize();
  }

  buildStage() {
    // Neutral viewing light reveals the unlit prop; colored light is DMX only.
    this.scene.add(new THREE.HemisphereLight(0xb4cbdc, 0x393028, 0.65));
    const key = new THREE.DirectionalLight(0xe6eef5, 0.95);
    key.position.set(-3, 7, 4);
    this.scene.add(key);
    const base = new THREE.Mesh(
      new THREE.CylinderGeometry(3.65, 3.68, 0.12, 96),
      new THREE.MeshStandardMaterial({ color: 0x26323d, roughness: 0.98 }),
    );
    base.position.y = -0.12;
    base.receiveShadow = true;
    this.scene.add(base);
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(3.4, 0.009, 4, 128),
      new THREE.MeshBasicMaterial({ color: 0x627886, transparent: true, opacity: 0.4 }),
    );
    ring.rotation.x = Math.PI / 2;
    ring.position.y = -0.045;
    this.scene.add(ring);
    for (const [name, x, z] of [['N', 0, -3.48], ['S', 0, 3.48], ['E', 3.48, 0], ['W', -3.48, 0]]) {
      const label = this.label(name);
      label.position.set(x, 0.06, z);
      this.scene.add(label);
    }
  }

  label(text) {
    const canvas = document.createElement('canvas');
    canvas.width = 128;
    canvas.height = 64;
    const ctx = canvas.getContext('2d');
    ctx.font = '500 30px "Avenir Next", sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#b1c4ce';
    ctx.fillText(text, 64, 32);
    const map = new THREE.CanvasTexture(canvas);
    map.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map, depthWrite: false }));
    sprite.scale.set(0.52, 0.26, 1);
    return sprite;
  }

  addFixture(fixture) {
    const record = { fixture, output: fixtureOutput(fixture, null) };
    const [x = 0, north = 0] = fixture.position || [];
    const hasRGB = ['red', 'green', 'blue'].some(channel => fixture.channels[channel] !== undefined);
    const inside = Math.hypot(x, north) < 1.8;
    // Existing positions are meters in a north-up plan; glTF uses Y-up.
    const source = new THREE.Vector3(x, inside ? 2.72 : 0.18, -north);
    if (hasRGB) {
      const light = new THREE.SpotLight(0xffffff, 0, 12, Math.PI * 0.29, 0.6, 2);
      light.position.copy(source);
      light.target.position.set(0, inside ? 2.0 : 1.75, 0);
      light.castShadow = true;
      light.shadow.mapSize.set(512, 512);
      light.shadow.bias = -0.0004;
      light.shadow.normalBias = 0.025;
      light.shadow.autoUpdate = false;
      light.shadow.needsUpdate = true;
      this.scene.add(light, light.target);
      record.light = light;
      const housing = new THREE.Mesh(
        new THREE.CylinderGeometry(0.10, 0.12, 0.16, 12),
        new THREE.MeshStandardMaterial({ color: 0x12181f, roughness: 0.55, metalness: 0.4 }),
      );
      housing.position.copy(source);
      const direction = light.target.position.clone().sub(source).normalize();
      housing.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);
      this.scene.add(housing);
      const lens = new THREE.Mesh(new THREE.CircleGeometry(0.085, 16),
        new THREE.MeshBasicMaterial({ color: 0x232d36, toneMapped: false }));
      lens.position.copy(source).addScaledVector(direction, 0.085);
      lens.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), direction);
      this.scene.add(lens);
      record.lens = lens;
      const label = this.label(fixture.name.toUpperCase());
      label.scale.set(0.60, 0.30, 1);
      label.position.copy(source).add(new THREE.Vector3(0, 0.28, 0));
      this.scene.add(label);
      const chip = document.createElement('span');
      chip.className = 'pro-fixture';
      const dot = document.createElement('i');
      dot.setAttribute('aria-hidden', 'true');
      chip.append(dot, document.createTextNode(fixture.name.toUpperCase()));
      document.getElementById('proFixtures').appendChild(chip);
      record.chip = chip;
      record.dot = dot;
    }
    if (fixture.channels.fog !== undefined) {
      record.plume = this.makePlume();
      record.plume.position.set(x, 2.70, -north);
      this.scene.add(record.plume);
    }
    if (fixture.channels.jet !== undefined) {
      const outlet = this.addMachine(record, 'co2');
      record.jet = new CO2Effect();
      record.jet.position.copy(outlet);
      this.scene.add(record.jet);
    }
    if (fixture.channels.fire !== undefined) {
      const outlet = this.addMachine(record, 'flame');
      record.flame = new FlameEffect();
      record.flame.position.copy(outlet);
      record.flameLight = new THREE.PointLight(0xff912f, 0, 8, 2);
      record.flameLight.position.copy(outlet).add(new THREE.Vector3(0, 0.45, 0));
      this.scene.add(record.flame, record.flameLight);
    }
    this.fixtures.push(record);
  }

  addMachine(record, kind) {
    const template = this.machineTemplates.children.find(object => object.userData.fixture_kind === kind);
    if (!template) throw new Error(`Missing ${kind} machine model`);
    const hardware = template.clone(true);
    const [x = 0, north = 0] = record.fixture.position || [];
    // Position the visible housings on the crater deck; retain configured plan positions.
    hardware.position.set(x, Math.hypot(x, north) < 1.8 ? 3.0 : 0.04, -north);
    let outlet;
    hardware.traverse(object => {
      if (object.isMesh) object.castShadow = object.receiveShadow = true;
      if (object.userData.outlet) outlet = object;
    });
    if (!outlet) throw new Error(`Missing ${kind} nozzle marker`);
    this.scene.add(hardware);
    record.hardware = hardware;
    hardware.updateWorldMatrix(true, true);
    const origin = outlet.getWorldPosition(new THREE.Vector3());
    const label = this.label(kind === 'co2' ? 'CO₂' : 'FLAME');
    label.position.copy(origin).add(new THREE.Vector3(kind === 'co2' ? -0.22 : 0.22, 0.15, 0));
    this.scene.add(label);
    const chip = document.createElement('span');
    chip.className = 'pro-fixture pro-effect-status';
    record.effectLabel = kind === 'co2' ? 'CO₂' : 'Flame';
    chip.textContent = `${record.effectLabel} off`;
    document.getElementById('proFixtures').appendChild(chip);
    record.effectChip = chip;
    return origin;
  }

  makePlume() {
    const group = new THREE.Group();
    const geometry = new THREE.IcosahedronGeometry(1, 2);
    for (let i = 0; i < 16; i++) {
      const puff = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
        color: 0xbac9d1, transparent: true, opacity: 0, depthWrite: false,
      }));
      puff.userData.seed = i / 16;
      group.add(puff);
    }
    return group;
  }

  updateFrame(frame, connected = true) {
    this.connected = connected;
    this.latestFrame = frame;
    for (const record of this.fixtures) {
      const output = fixtureOutput(record.fixture, frame);
      record.output = output;
      if (record.light) {
        const [r, g, b] = output.rgb;
        record.light.color.setRGB(r / 255, g / 255, b / 255, THREE.SRGBColorSpace);
        record.light.intensity = output.dimmer * 30;
        record.lens.material.color.copy(record.light.color).multiplyScalar(output.dimmer);
        const swatch = output.rgb.map(value => Math.round(value * output.dimmer));
        record.dot.style.backgroundColor = `rgb(${swatch.join(',')})`;
        const level = Math.round(output.dimmer * Math.max(r, g, b) / 255 * 100);
        record.chip.title = `${record.fixture.name}: RGB ${r}, ${g}, ${b}; dimmer ${Math.round(output.dimmer * 100)}%`;
        record.chip.setAttribute('aria-label', `${record.fixture.name}: ${level}% output`);
      }
      if (record.flame) {
        const level = this.preview?.kind === 'flame' ? 1 : output.fire;
        record.flame.setLevel(level);
        record.flameLight.intensity = level * 38;
      }
      if (record.jet) record.jet.setLevel(this.preview?.kind === 'co2' ? 1 : output.jet);
      if (record.plume) record.plume.visible = output.fog > 0;
      if (record.effectChip) {
        const previewing = (record.flame && this.preview?.kind === 'flame') || (record.jet && this.preview?.kind === 'co2');
        const on = previewing || output.fire > 0 || output.jet > 0;
        record.effectChip.textContent = `${record.effectLabel} ${previewing ? 'preview' : on ? 'firing' : 'off'}`;
        record.effectChip.classList.toggle('firing', on);
      }
    }
    const status = document.getElementById('proConnection');
    status.textContent = this.preview ? 'Visual preview · no DMX output' : connected ? 'Live output' : 'Connection lost · lighting paused';
    status.classList.toggle('disconnected', !connected);
  }

  previewEffect(kind) {
    if (!this.active || !['flame', 'co2'].includes(kind)) return;
    if (!this.fixtures.some(record => kind === 'flame' ? record.flame : record.jet)) return;
    clearTimeout(this.previewTimer);
    this.preview = { kind };
    this.framePreset(kind === 'co2' ? 'effect' : 'stage');
    this.updateFrame(this.latestFrame, this.connected);
    this.previewTimer = setTimeout(() => this.stopPreview(), 2500);
  }

  stopPreview() {
    clearTimeout(this.previewTimer);
    this.previewTimer = 0;
    this.preview = null;
    this.updateFrame(this.latestFrame, this.connected);
  }

  animateEffects(time) {
    const t = this.reducedMotion.matches ? 0 : time * 0.001;
    for (const record of this.fixtures) {
      record.flame?.update(time * 0.001, this.reducedMotion.matches);
      record.jet?.update(time * 0.001, this.reducedMotion.matches);
      if (!record.plume?.visible) continue;
      const amount = record.output.fog;
      for (const puff of record.plume.children) {
        const age = (puff.userData.seed + t * 0.15) % 1;
        const size = 0.15 + age * 0.55;
        puff.position.set(Math.sin(puff.userData.seed * 29 + t * 0.3) * age * 0.5,
          age * 1.6, Math.cos(puff.userData.seed * 33) * age * 0.35);
        puff.scale.set(size, size * 0.8, size);
        puff.material.opacity = Math.sin(age * Math.PI) * amount * 0.075;
      }
    }
  }

  onKey(event) {
    const moves = { ArrowLeft: [-0.12, 0], ArrowRight: [0.12, 0], ArrowUp: [0, -0.10], ArrowDown: [0, 0.10] };
    if (event.key in moves) {
      event.preventDefault();
      const offset = this.camera.position.clone().sub(this.controls.target);
      const spherical = new THREE.Spherical().setFromVector3(offset);
      const [theta, phi] = moves[event.key];
      spherical.theta += theta;
      spherical.phi = THREE.MathUtils.clamp(spherical.phi + phi, this.controls.minPolarAngle, this.controls.maxPolarAngle);
      this.camera.position.copy(this.controls.target).add(new THREE.Vector3().setFromSpherical(spherical));
      this.controls.update();
    } else if (['+', '=', '-', 'Home'].includes(event.key)) {
      event.preventDefault();
      if (event.key === 'Home') this.reset();
      else this.zoom(event.key === '-' ? 1.15 : 1 / 1.15);
    }
  }

  zoom(factor) {
    const offset = this.camera.position.clone().sub(this.controls.target);
    offset.setLength(THREE.MathUtils.clamp(offset.length() * factor, this.controls.minDistance, this.controls.maxDistance));
    this.camera.position.copy(this.controls.target).add(offset);
    this.controls.update();
  }

  reset() {
    // Consume pending rotation before reset; OrbitControls.reset keeps inertia.
    const damping = this.controls.enableDamping;
    this.controls.enableDamping = false;
    this.controls.update();
    this.controls.reset();
    this.controls.enableDamping = damping;
    this.cameraPreset = 'stage';
  }

  framePreset(preset) {
    this.reset();
    this.cameraPreset = preset;
    if (preset === 'stage') {
      return;
    } else if (preset === 'crater') {
      this.controls.target.set(0, 3.1, 0);
      this.camera.position.set(1.1, 4.5, 1.9);
    } else if (preset === 'effect') {
      const bounds = new THREE.Box3(new THREE.Vector3(-3.7, -0.2, -3.7), new THREE.Vector3(3.7, 3.2, 3.7));
      for (const record of this.fixtures) {
        if (record.jet) bounds.expandByPoint(record.jet.position.clone().add(new THREE.Vector3(0, CO2_HEIGHT + 0.5, 0)));
        if (record.flame) bounds.expandByPoint(record.flame.position.clone().add(new THREE.Vector3(0, record.flame.height, 0)));
      }
      const center = bounds.getCenter(new THREE.Vector3());
      const size = bounds.getSize(new THREE.Vector3());
      const halfFov = THREE.MathUtils.degToRad(this.camera.fov / 2);
      const distance = Math.max(size.y, size.x / this.camera.aspect) / (2 * Math.tan(halfFov)) * 1.30 + size.z / 2;
      this.controls.target.copy(center);
      this.camera.position.copy(center).add(new THREE.Vector3(0.50, 0.16, 0.85).normalize().multiplyScalar(distance));
    }
    this.controls.update();
  }

  resize() {
    const width = this.host.clientWidth;
    const height = this.host.clientHeight;
    if (!width || !height || this.disposed) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
    if (this.cameraPreset === 'effect') this.framePreset('effect');
  }

  setActive(active) {
    this.active = active;
    if (!active && this.preview) this.stopPreview();
    this.controls.enabled = active;
    if (active) this.resize();
    this.schedule();
  }

  schedule() {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
    if (!this.active || !this.inView || document.hidden || this.disposed) return;
    this.raf = requestAnimationFrame(time => this.render(time));
  }

  render(time) {
    try {
      this.controls.update();
      if (time - this.lastDraw >= 1000 / 30) {
        this.animateEffects(time);
        this.renderer.render(this.scene, this.camera);
        this.lastDraw = time;
      }
      this.schedule();
    } catch (error) {
      this.setActive(false);
      this.onError('The 3D stage could not render. Retry or switch off Pro view.');
      console.error('Volcano preview:', error);
    }
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.raf);
    clearTimeout(this.previewTimer);
    this.abort.abort();
    this.resizeObserver.disconnect();
    this.intersectionObserver.disconnect();
    this.controls.dispose();
    const resources = new Set();
    this.scene.traverse(object => {
      if (object.geometry) resources.add(object.geometry);
      if (object.shadow) object.shadow.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      for (const material of materials) {
        if (!material) continue;
        resources.add(material);
        for (const value of Object.values(material)) if (value?.isTexture) resources.add(value);
      }
    });
    for (const resource of resources) resource.dispose();
    for (const record of this.fixtures) {
      record.chip?.remove();
      record.effectChip?.remove();
    }
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
