import * as THREE from './vendor/three/three.module.min.js';

// Visual envelopes above the outlet, in meters. These are preview assumptions.
export const FLAME_HEIGHT = 2;
export const CO2_HEIGHT = 9;

const noiseGLSL = `
  float hash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
  float noise3(vec3 p) {
    vec3 i = floor(p), f = fract(p); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash(i), hash(i+vec3(1,0,0)), f.x),
                   mix(hash(i+vec3(0,1,0)), hash(i+vec3(1,1,0)), f.x), f.y),
               mix(mix(hash(i+vec3(0,0,1)), hash(i+vec3(1,0,1)), f.x),
                   mix(hash(i+vec3(0,1,1)), hash(i+vec3(1,1,1)), f.x), f.y), f.z);
  }
  float turbulence(vec3 p) {
    return 0.57 * noise3(p) + 0.28 * noise3(p*2.1) + 0.15 * noise3(p*4.2);
  }
`;

class OutputEffect extends THREE.Group {
  constructor(height) {
    super();
    this.height = height;
    this.level = 0;
    this.visible = false;
    this.startedAt = null;
  }

  setLevel(level) {
    const next = Number.isFinite(level) ? THREE.MathUtils.clamp(level, 0, 1) : 0;
    if (next > 0 && this.level === 0) this.startedAt = null;
    this.level = next;
    this.visible = next > 0;
    this.material.uniforms.uLevel.value = next;
  }
}

export class FlameEffect extends OutputEffect {
  constructor() {
    super(FLAME_HEIGHT);
    this.name = 'FlameJet';
    this.material = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uLevel: { value: 0 }, uCore: { value: 0 } },
      transparent: true, depthWrite: false, side: THREE.DoubleSide,
      blending: THREE.AdditiveBlending, toneMapped: false,
      vertexShader: `
        uniform float uTime;
        varying vec3 vLocal;
        varying float vHeight;
        ${noiseGLSL}
        void main() {
          float h = uv.y;
          float angle = atan(position.z, position.x);
          float n = turbulence(vec3(cos(angle)*2.0, h*8.0-uTime*5.0, sin(angle)*2.0));
          float radius = (0.016 + 0.19*sin(3.14159*pow(h, 0.68))) * (0.58+n*0.72);
          radius *= 1.0 - 0.7*pow(h, 5.0);
          vec3 p = vec3(cos(angle)*radius, h*2.0, sin(angle)*radius);
          p.x += sin(h*13.0-uTime*8.0)*0.065*h;
          p.z += sin(h*17.0-uTime*6.0)*0.05*h;
          p.y *= 0.965 + 0.035*sin(uTime*23.0+angle*2.0);
          vLocal = p; vHeight = h;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
        }
      `,
      fragmentShader: `
        uniform float uTime, uLevel, uCore;
        varying vec3 vLocal;
        varying float vHeight;
        ${noiseGLSL}
        void main() {
          float n = turbulence(vLocal*vec3(16.0, 5.0, 16.0)-vec3(0,uTime*7.0,0));
          float tongues = smoothstep(0.13+vHeight*0.42, 0.72, n);
          float alpha = (0.30+tongues*0.70)*(1.0-smoothstep(0.65,1.0,vHeight));
          alpha *= mix(0.65, 1.0, uLevel);
          vec3 hot = vec3(1.0, 0.94, 0.53);
          vec3 orange = vec3(1.0, 0.20, 0.015);
          vec3 color = mix(hot, orange, clamp(vHeight*1.6-n*0.3-uCore*0.5, 0.0, 1.0));
          gl_FragColor = vec4(color, alpha * 0.75);
        }
      `,
    });
    const geometry = new THREE.CylinderGeometry(1, 1, 2, 32, 48, true);
    const outer = new THREE.Mesh(geometry, this.material);
    outer.frustumCulled = false; // Shader displaces beyond the primitive's bounds.
    this.add(outer);
    this.coreMaterial = this.material.clone();
    this.coreMaterial.uniforms.uCore.value = 1;
    const core = new THREE.Mesh(geometry, this.coreMaterial);
    core.scale.set(0.48, 0.78, 0.48);
    core.frustumCulled = false;
    this.add(core);
  }

  update(time, reducedMotion) {
    const t = reducedMotion ? 0 : time;
    this.material.uniforms.uTime.value = t;
    this.coreMaterial.uniforms.uTime.value = t;
    this.coreMaterial.uniforms.uLevel.value = this.level;
  }
}

export class CO2Effect extends OutputEffect {
  constructor() {
    super(CO2_HEIGHT);
    this.name = 'CO2Jet';
    this.reach = 0;
    this.material = new THREE.ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uLevel: { value: 0 }, uReach: { value: 0 } },
      transparent: true, depthWrite: false, toneMapped: false,
      vertexShader: `
        attribute float seed;
        uniform float uTime, uReach;
        varying vec2 vUV;
        varying float vAge, vSeed;
        void main() {
          float age = fract(seed + uTime*1.35);
          float rise = age*uReach;
          float spread = 0.02 + 0.43*pow(rise, 1.5);
          vec3 center = vec3(sin(seed*173.0)*spread, rise*8.35,
                             cos(seed*137.0)*spread);
          center.x += sin(uTime*1.2+rise*8.0)*rise*0.12;
          float size = 0.11 + 1.35*pow(rise, 1.2);
          vec4 viewCenter = modelViewMatrix * vec4(center, 1.0);
          // Each soft puff faces the camera; its center remains in the 3D jet.
          viewCenter.xy += position.xy * vec2(size, size*1.35);
          gl_Position = projectionMatrix * viewCenter;
          vUV = uv; vAge = age; vSeed = seed;
        }
      `,
      fragmentShader: `
        uniform float uTime, uLevel;
        varying vec2 vUV;
        varying float vAge, vSeed;
        ${noiseGLSL}
        void main() {
          float r = length(vUV-0.5)*2.0;
          float n = turbulence(vec3(vUV*6.0, vSeed*53.0+uTime*0.4));
          float edge = 1.0-smoothstep(0.35+n*0.17, 0.98, r);
          float fade = 1.0-smoothstep(0.76,1.0,vAge);
          float alpha = edge*fade*(0.48+n*0.30)*uLevel;
          vec3 color = mix(vec3(0.63,0.72,0.79), vec3(0.97,0.99,1.0), n*0.65+(1.0-r)*0.35);
          gl_FragColor = vec4(color, alpha);
        }
      `,
    });
    const plane = new THREE.PlaneGeometry(1, 1);
    const geometry = new THREE.InstancedBufferGeometry();
    geometry.index = plane.index;
    geometry.attributes.position = plane.attributes.position;
    geometry.attributes.uv = plane.attributes.uv;
    const count = 144;
    geometry.setAttribute('seed', new THREE.InstancedBufferAttribute(
      Float32Array.from({ length: count }, (_, i) => i / count), 1));
    geometry.instanceCount = count;
    const cloud = new THREE.Mesh(geometry, this.material);
    cloud.frustumCulled = false;
    this.add(cloud);
  }

  update(time, reducedMotion) {
    if (!this.visible) return;
    this.startedAt ??= time;
    this.reach = reducedMotion ? 1 : Math.min(1, 0.03 + (time - this.startedAt) / 0.45);
    this.material.uniforms.uReach.value = this.reach;
    this.material.uniforms.uTime.value = reducedMotion ? 0 : time - this.startedAt;
  }
}
