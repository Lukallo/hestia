// The hearth behind the showcase page: one fire, drawn for the stage of the
// day you have scrolled to. Only the fire moves on its own. Everything else
// changes because you scrolled, clicked or moved the pointer, and with
// prefers-reduced-motion the scene is drawn as still frames.
import * as THREE from 'three';

const STAGES = {
  tend:    { fire: 1.0,  light: 1.0,  top: '#120d0b', bottom: '#3b1f12', sun: 0, sunY: 9,   moon: 0,   stars: 0,    glow: 0.15, deep: 0 },
  rest:    { fire: 0.6,  light: 0.7,  top: '#2a2018', bottom: '#5f3f1d', sun: 1, sunY: 8,   moon: 0,   stars: 0,    glow: 0.1,  deep: 0 },
  bank:    { fire: 0.16, light: 0.32, top: '#0f0c0b', bottom: '#2a1912', sun: 0, sunY: 1,   moon: 0.3, stars: 0.35, glow: 1.0,  deep: 0.6 },
  keep:    { fire: 0.1,  light: 0.2,  top: '#060608', bottom: '#151118', sun: 0, sunY: 1,   moon: 1,   stars: 1,    glow: 0.6,  deep: 1 },
  relight: { fire: 0.8,  light: 0.85, top: '#191311', bottom: '#74391a', sun: 1, sunY: 0.4, moon: 0,   stars: 0.12, glow: 0.35, deep: 0 },
};

const canvas = document.getElementById('hearth');
const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
let renderer = null;
try {
  renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
} catch (err) {
  // No WebGL: the page keeps its static banner.
}
if (renderer) start();

function start() {
  document.documentElement.classList.add('gl');
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x2a1912, 6, 30);
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 120);
  const look = new THREE.Vector3(0, 1.05, 0);

  // State: what is on screen now, and what it is easing towards.
  const now = {}, target = {};
  for (const key of Object.keys(STAGES.tend)) {
    const v = STAGES.tend[key];
    now[key] = typeof v === 'string' ? new THREE.Color(v) : v;
    target[key] = typeof v === 'string' ? new THREE.Color(v) : v;
  }

  // Sky: a gradient dome, lit at the horizon.
  const sky = new THREE.Mesh(
    new THREE.SphereGeometry(70, 32, 16),
    new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false, fog: false,
      uniforms: { uTop: { value: now.top.clone() }, uBottom: { value: now.bottom.clone() } },
      vertexShader: /* glsl */`
        varying float vH;
        void main() { vH = normalize(position).y; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */`
        uniform vec3 uTop; uniform vec3 uBottom; varying float vH;
        void main() {
          gl_FragColor = vec4(mix(uBottom, uTop, smoothstep(-0.02, 0.45, vH)), 1.0);
          #include <tonemapping_fragment>
          #include <colorspace_fragment>
        }`,
    })
  );
  scene.add(sky);

  // Ground, the ash bed and the ring of stones.
  const ground = new THREE.Mesh(new THREE.CircleGeometry(40, 64), new THREE.MeshStandardMaterial({ color: 0x2a1e17, roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  scene.add(ground);
  const ash = new THREE.Mesh(new THREE.CircleGeometry(1.32, 48), new THREE.MeshStandardMaterial({ color: 0x2d2622, roughness: 1 }));
  ash.rotation.x = -Math.PI / 2; ash.position.y = 0.012;
  scene.add(ash);

  const rand = mulberry32(7);
  const stoneMat = new THREE.MeshStandardMaterial({ color: 0x4d4038, roughness: 0.95, flatShading: true });
  const stoneGeo = new THREE.DodecahedronGeometry(0.26, 0);
  for (let i = 0; i < 26; i++) {
    const a = (i / 26) * Math.PI * 2;
    const s = new THREE.Mesh(stoneGeo, stoneMat);
    s.position.set(Math.cos(a) * 1.58, 0.11, Math.sin(a) * 1.58);
    s.scale.set(0.9 + rand() * 0.5, 0.55 + rand() * 0.35, 0.8 + rand() * 0.4);
    s.rotation.set(rand() * 3, a + rand(), rand() * 3);
    scene.add(s);
  }

  // Logs, crossed, that glow from inside when the fire is banked.
  const logMat = new THREE.MeshStandardMaterial({ color: 0x3b261b, roughness: 1, emissive: 0xc2502a, emissiveIntensity: 0.1 });
  const logGeo = new THREE.CylinderGeometry(0.1, 0.13, 1.75, 10);
  for (let i = 0; i < 4; i++) {
    const log = new THREE.Mesh(logGeo, logMat);
    log.rotation.z = Math.PI / 2 - 0.12;
    log.rotation.y = (i * Math.PI) / 4 + 0.3;
    log.position.y = 0.14 + i * 0.07;
    scene.add(log);
  }

  // Coals in the ash.
  const coalMat = new THREE.MeshStandardMaterial({ color: 0x241612, roughness: 0.8, emissive: 0xe8743b, emissiveIntensity: 0.6 });
  const coals = new THREE.InstancedMesh(new THREE.IcosahedronGeometry(0.075, 0), coalMat, 46);
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), e = new THREE.Euler();
  for (let i = 0; i < 46; i++) {
    const a = rand() * Math.PI * 2, r = Math.sqrt(rand()) * 0.85, k = 0.6 + rand() * 0.9;
    q.setFromEuler(e.set(rand() * 3, rand() * 3, rand() * 3));
    m.compose(new THREE.Vector3(Math.cos(a) * r, 0.04, Math.sin(a) * r), q, new THREE.Vector3(k, k * 0.7, k));
    coals.setMatrixAt(i, m);
  }
  scene.add(coals);

  // The fire and the embers above it: GPU particles, one draw call each.
  const fireUniforms = { uTime: { value: 0 }, uFire: { value: 1 }, uDeep: { value: 0 }, uPixel: { value: renderer.getPixelRatio() } };
  const fire = particles(1600, fireUniforms, false);
  const embers = particles(140, fireUniforms, true);
  scene.add(fire, embers);

  // Light: the firelight, and a soft fill from the sky.
  const firelight = new THREE.PointLight(0xff8a3d, 30, 0, 2);
  firelight.position.set(0, 1.25, 0);
  scene.add(firelight);
  const hemi = new THREE.HemisphereLight(0x6b4a3a, 0x0b0807, 0.6);
  scene.add(hemi);
  const fill = new THREE.DirectionalLight(0xa86a48, 0.5);
  fill.position.set(-2, 3, 8);
  scene.add(fill);

  // Sun, moon and stars: they only fade and move when the stage changes.
  const sun = new THREE.Sprite(new THREE.SpriteMaterial({ map: discTexture(false), transparent: true, depthWrite: false, fog: false }));
  sun.scale.setScalar(5.5);
  const moon = new THREE.Sprite(new THREE.SpriteMaterial({ map: discTexture(true), transparent: true, depthWrite: false, fog: false }));
  moon.scale.setScalar(4.2);
  moon.position.set(7, 9, -30);
  const starGeo = new THREE.BufferGeometry();
  const starPos = [];
  for (let i = 0; i < 520; i++) {
    const th = rand() * Math.PI * 2, ph = 0.08 + rand() * 1.2;
    starPos.push(Math.cos(th) * Math.cos(ph) * 55, Math.sin(ph) * 55, Math.sin(th) * Math.cos(ph) * 55);
  }
  starGeo.setAttribute('position', new THREE.Float32BufferAttribute(starPos, 3));
  const stars = new THREE.Points(starGeo, new THREE.PointsMaterial({ color: 0xf6ede3, size: 1.6, sizeAttenuation: false, transparent: true, depthWrite: false, fog: false }));
  scene.add(sun, moon, stars);

  // Pointer parallax: a few degrees, only while the pointer moves.
  const tilt = { x: 0, y: 0, tx: 0, ty: 0 };
  window.addEventListener('pointermove', (ev) => {
    if (reduced.matches) return;
    tilt.tx = (ev.clientX / window.innerWidth - 0.5) * 0.35;
    tilt.ty = (ev.clientY / window.innerHeight - 0.5) * 0.18;
  }, { passive: true });

  function resize() {
    const w = window.innerWidth, h = window.innerHeight;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    // On wide screens the hearth sits to the right of the text column; on
    // narrow ones it sits low, under the text.
    if (w > 960) camera.setViewOffset(w, h, -w * 0.21, 0, w, h);
    else camera.setViewOffset(w, h, 0, -h * 0.3, w, h);
    camera.updateProjectionMatrix();
    fireUniforms.uPixel.value = renderer.getPixelRatio() * h / 900;
    if (reduced.matches) draw(0);
  }
  window.addEventListener('resize', resize);

  // The stage follows whichever part of the page is in the middle of the screen.
  const bar = document.querySelectorAll('[data-for]');
  function setStage(name) {
    const s = STAGES[name];
    if (!s) return;
    for (const key of Object.keys(s)) {
      if (typeof s[key] === 'string') target[key].set(s[key]); else target[key] = s[key];
    }
    bar.forEach((a) => a.toggleAttribute('aria-current', a.dataset.for === name));
    if (reduced.matches) { snap(); draw(0); }
  }
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) if (en.isIntersecting) setStage(en.target.dataset.stage);
  }, { rootMargin: '-48% 0px -48% 0px' });
  document.querySelectorAll('[data-stage]').forEach((el) => io.observe(el));

  function snap() {
    for (const key of Object.keys(target)) {
      if (target[key] instanceof THREE.Color) now[key].copy(target[key]); else now[key] = target[key];
    }
  }

  function ease(dt) {
    const k = 1 - Math.exp(-dt * 2.4);
    for (const key of Object.keys(target)) {
      if (target[key] instanceof THREE.Color) now[key].lerp(target[key], k);
      else now[key] += (target[key] - now[key]) * k;
    }
    tilt.x += (tilt.tx - tilt.x) * k; tilt.y += (tilt.ty - tilt.y) * k;
  }

  function draw(t) {
    sky.material.uniforms.uTop.value.copy(now.top);
    sky.material.uniforms.uBottom.value.copy(now.bottom);
    scene.fog.color.copy(now.bottom);
    hemi.color.copy(now.bottom).multiplyScalar(2.6);
    sun.material.opacity = now.sun;
    sun.position.set(6.5, now.sunY, -30);
    moon.material.opacity = now.moon;
    stars.material.opacity = now.stars;
    fireUniforms.uTime.value = t;
    fireUniforms.uFire.value = now.fire;
    fireUniforms.uDeep.value = now.deep;
    const flicker = reduced.matches ? 1 : 0.86 + 0.09 * Math.sin(t * 13.1) + 0.05 * Math.sin(t * 29.7 + 1.3);
    firelight.intensity = 20 * now.light * flicker;
    fill.intensity = 0.12 + 0.45 * now.light;
    firelight.color.setHSL(0.06 - now.deep * 0.02, 1, 0.55 - now.deep * 0.08);
    logMat.emissiveIntensity = 0.05 + now.glow * (0.22 + 0.1 * flicker);
    coalMat.emissiveIntensity = 0.2 + now.glow * 0.9 + now.fire * 0.5;
    camera.position.set(Math.sin(tilt.x) * 7.4, 1.95 + tilt.y * 2, Math.cos(tilt.x) * 7.4);
    camera.lookAt(look);
    renderer.render(scene, camera);
  }

  resize();
  setStage('tend');
  snap();
  if (reduced.matches) {
    draw(0);
  } else {
    const timer = new THREE.Timer();
    timer.connect(document);
    renderer.setAnimationLoop((ts) => {
      timer.update(ts);
      ease(Math.min(timer.getDelta(), 0.1));
      draw(timer.getElapsed());
    });
  }
}

function particles(count, uniforms, isEmber) {
  const geo = new THREE.BufferGeometry();
  const seeds = new Float32Array(count * 4);
  for (let i = 0; i < seeds.length; i++) seeds[i] = Math.random();
  geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(count * 3), 3));
  geo.setAttribute('aSeed', new THREE.BufferAttribute(seeds, 4));
  geo.boundingSphere = new THREE.Sphere(new THREE.Vector3(0, 2, 0), 6);
  const mat = new THREE.ShaderMaterial({
    uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, fog: false,
    defines: isEmber ? { EMBER: 1 } : {},
    vertexShader: /* glsl */`
      attribute vec4 aSeed;
      uniform float uTime, uFire, uPixel;
      varying float vLife;
      void main() {
        #ifdef EMBER
          float life = fract(uTime * (0.12 + aSeed.y * 0.1) + aSeed.x);
          float ang = aSeed.z * 6.2831 + uTime * 0.3;
          vec3 p = vec3(cos(ang) * (0.2 + life * 1.1), 0.4 + life * 4.6 * (0.4 + 0.6 * uFire), sin(ang) * (0.2 + life * 1.1));
          p.x += sin(uTime * 1.3 + aSeed.w * 20.0) * 0.25 * life;
          float size = (0.06 + 0.05 * aSeed.w) * (0.6 + 0.4 * sin(uTime * 9.0 + aSeed.x * 40.0));
          float alive = step(aSeed.y, 0.08 + uFire);
        #else
          float life = fract(uTime * (0.5 + aSeed.y * 0.6) + aSeed.x);
          float ang = aSeed.z * 6.2831;
          float r = sqrt(aSeed.w) * 0.68 * (1.0 - life * 0.8);
          vec3 p = vec3(cos(ang) * r, 0.22 + life * 2.2 * uFire, sin(ang) * r);
          p.x += sin(uTime * 2.3 + aSeed.x * 12.0 + life * 4.0) * 0.13 * life;
          p.z += cos(uTime * 1.7 + aSeed.y * 9.0) * 0.07 * life;
          float size = mix(1.5, 0.3, life) * (0.45 + 0.55 * uFire);
          float alive = step(aSeed.y, 0.12 + uFire);
        #endif
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_Position = projectionMatrix * mv;
        gl_PointSize = alive * size * uPixel * 330.0 / -mv.z;
        vLife = life;
      }`,
    fragmentShader: /* glsl */`
      uniform float uDeep;
      varying float vLife;
      void main() {
        float d = length(gl_PointCoord - 0.5);
        if (d > 0.5) discard;
        float soft = pow(1.0 - d * 2.0, 1.5);
        vec3 gold = vec3(0.957, 0.718, 0.251), ember = vec3(0.91, 0.455, 0.231), deep = vec3(0.76, 0.314, 0.165);
        #ifdef EMBER
          vec3 col = mix(gold, ember, vLife);
          float a = soft * (1.0 - smoothstep(0.7, 1.0, vLife)) * 0.9;
        #else
          vec3 col = mix(gold, ember, smoothstep(0.0, 0.35, vLife));
          col = mix(col, deep, smoothstep(0.35, 0.8, vLife));
          col = mix(col, deep, uDeep * 0.85);
          float a = soft * (1.0 - smoothstep(0.3, 1.0, vLife)) * (0.62 - 0.25 * uDeep);
        #endif
        gl_FragColor = vec4(col * a, a);
      }`,
  });
  return new THREE.Points(geo, mat);
}

function discTexture(crescent) {
  const c = document.createElement('canvas');
  c.width = c.height = 256;
  const g = c.getContext('2d');
  g.fillStyle = '#F4B740';
  g.beginPath(); g.arc(128, 128, 46, 0, Math.PI * 2); g.fill();
  if (crescent) {
    g.globalCompositeOperation = 'destination-out';
    g.beginPath(); g.arc(148, 114, 40, 0, Math.PI * 2); g.fill();
  }
  // The halo goes behind the disc, so the moon's shadow side shows sky, not a hole.
  g.globalCompositeOperation = 'destination-over';
  const halo = g.createRadialGradient(128, 128, 30, 128, 128, 128);
  halo.addColorStop(0, crescent ? 'rgba(244,183,64,0.16)' : 'rgba(244,183,64,0.55)');
  halo.addColorStop(1, 'rgba(244,183,64,0)');
  g.fillStyle = halo;
  g.fillRect(0, 0, 256, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
