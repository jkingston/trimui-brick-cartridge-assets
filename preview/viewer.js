import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';

const viewport = document.querySelector('#viewport'), metrics = document.querySelector('#metrics'), status = document.querySelector('#status');
const renderer = new THREE.WebGLRenderer({antialias:true, powerPreference:'low-power'});
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
// Preserve printed label colours rather than applying a cinematic colour curve.
renderer.toneMapping = THREE.NeutralToneMapping;
renderer.outputColorSpace = THREE.SRGBColorSpace;
viewport.append(renderer.domElement);
const scene = new THREE.Scene(); scene.background = new THREE.Color('#e9e6df');
const pmrem = new THREE.PMREMGenerator(renderer), room = new RoomEnvironment();
const environments = {room:pmrem.fromScene(room, 0.04)}; room.dispose();
const studio = new THREE.Scene(); studio.background = new THREE.Color(.12,.12,.12);
const cardMaterial = new THREE.MeshBasicMaterial({color:new THREE.Color(6,6,6),side:THREE.DoubleSide});
const cardGeometry = new THREE.PlaneGeometry(.8,5);
for (const z of [-4,4]) {
  const card = new THREE.Mesh(cardGeometry,cardMaterial); card.position.set(-1.4,0,z); card.lookAt(0,0,0); studio.add(card);
  const secondary = new THREE.Mesh(cardGeometry,cardMaterial); secondary.position.set(3.5,1,z*.5); secondary.lookAt(0,0,0); studio.add(secondary);
}
environments.studio = pmrem.fromScene(studio, .02); cardGeometry.dispose(); cardMaterial.dispose(); pmrem.dispose();
environments.relief = environments.room;
scene.environment = environments.room.texture; scene.environmentIntensity = .32;
const keyDirection = new THREE.Vector3(1,2,3);
const key = new THREE.DirectionalLight(0xffffff, 1.2); key.position.set(1,2,3); scene.add(key);
const fill = new THREE.DirectionalLight(0xffffff, .65); fill.position.set(-2,1,-3); scene.add(fill);
const camera = new THREE.PerspectiveCamera(35, 1, .001, 10);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = false; controls.enablePan = false;
const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
let model, framePending = false, frames = 0, generation = 0, currentId, loadMs = 0, bounds, distance = 1, benchmarking = false;
let lastFrameMs = 0;
let shadows = false;
function setShadows(enabled) {
  shadows = enabled; renderer.shadowMap.enabled = enabled; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  key.castShadow = enabled; key.shadow.mapSize.set(2048,2048); key.shadow.bias = -0.0003;
  if (model) {
    const radius = bounds.getSize(new THREE.Vector3()).length()/2;
    key.shadow.normalBias = radius*.001;
    key.position.copy(keyDirection).normalize().multiplyScalar(radius*10);
    Object.assign(key.shadow.camera,{left:-radius*1.2,right:radius*1.2,top:radius*1.2,bottom:-radius*1.2,near:radius*8,far:radius*12}); key.shadow.camera.updateProjectionMatrix();
    model.traverse(node => {if(node.isMesh) {const materials=Array.isArray(node.material)?node.material:[node.material]; node.castShadow=enabled && materials.every(m=>!m.transmission && !m.transparent);node.receiveShadow=enabled;}});
  }
  invalidate();
}
function draw() {
  framePending = false;
  if (document.hidden) return;
  const start = performance.now(); renderer.render(scene, camera); lastFrameMs = performance.now() - start; frames++;
  metrics.textContent = `${currentId ?? 'No model'} · ${renderer.info.render.triangles.toLocaleString()} triangles · ${renderer.info.render.calls} draw calls · ${lastFrameMs.toFixed(1)} ms CPU submission · ${frames} frames`;
}
function setBackground(name) {
  const colours = {paper:'#e9e6df',dark:'#262a30'};
  if (!colours[name]) throw Error('Unknown background');
  scene.background.set(colours[name]); document.querySelector('#background').value=name;
  const url=new URL(location.href); url.searchParams.set('background',name); history.replaceState(null,'',url); invalidate();
}
function setLighting(name) {
  if (!environments[name]) throw Error('Unknown lighting preset');
  scene.environment = environments[name].texture;
  keyDirection.set(...(name === 'relief' ? [-3,2,1] : [1,2,3]));
  keyDirection.z *= camera.position.z < 0 ? -1 : 1;
  key.intensity = name === 'relief' ? 12 : 1.2; scene.environmentIntensity = name === 'relief' ? .3 : .32;
  setShadows(name === 'relief' || new URL(location.href).searchParams.get('shadows') === '1');
  document.querySelector('#lighting').value = name;
  const url = new URL(location.href); url.searchParams.set('lighting',name); history.replaceState(null,'',url); invalidate();
}
function invalidate() { if (!framePending) { framePending = true; requestAnimationFrame(draw); } }
controls.addEventListener('change', invalidate);
document.addEventListener('visibilitychange', () => { if (!document.hidden) invalidate(); });
function resize() {
  const {width,height} = viewport.getBoundingClientRect(); renderer.setSize(width,height);
  camera.aspect = width/height; camera.updateProjectionMatrix(); if (bounds) frame('hero'); else invalidate();
}
new ResizeObserver(resize).observe(viewport);
function frame(view = 'hero') {
  if (!bounds) return;
  const size = bounds.getSize(new THREE.Vector3());
  const v = THREE.MathUtils.degToRad(camera.fov)/2, h = Math.atan(Math.tan(v)*camera.aspect);
  // Fit a bounding sphere in either screen axis, preserving the model's physical proportions.
  distance = size.length()/2 / Math.sin(Math.min(v,h)) * 1.12;
  const direction = view === 'rear' ? new THREE.Vector3(0,0,-1) : view === 'front' ? new THREE.Vector3(0,0,1) : new THREE.Vector3(.35,.16,view === 'rear-hero' ? -1 : 1).normalize();
  // Keep micron-scale stacked surfaces distinguishable in the depth buffer.
  camera.position.copy(direction.multiplyScalar(distance)); camera.near = distance/20; camera.far = distance*6; camera.updateProjectionMatrix();
  // Front/rear presets illuminate the inspected side; manual orbit keeps the studio fixed.
  const side = camera.position.z < 0 ? -1 : 1;
  keyDirection.z = Math.abs(keyDirection.z) * side;
  fill.position.set(-2,1,-3 * side);
  setShadows(shadows);
  controls.target.set(0,0,0); controls.minDistance = distance*.4; controls.maxDistance = distance*4; controls.update(); invalidate();
}
function dispose(object) {
  const textures = new Set(), materials = new Set(), geometries = new Set();
  object.traverse(node => { if (!node.isMesh) return; geometries.add(node.geometry); for (const material of Array.isArray(node.material)?node.material:[node.material]) { materials.add(material); for (const value of Object.values(material)) if (value?.isTexture) textures.add(value); } });
  const images = new Set([...textures].map(x => x.source?.data));
  textures.forEach(x => x.dispose()); images.forEach(image => image?.close?.()); materials.forEach(x => x.dispose()); geometries.forEach(x => x.dispose());
}
async function load(id, format = document.querySelector('#format').value, assetPath) {
  const token = ++generation, start = performance.now(); status.textContent = `Loading ${id}…`;
  try {
    const path = assetPath ?? (format === 'compressed' ? `../assets-compressed/${encodeURIComponent(id)}/model.glb` : `../assets/${encodeURIComponent(id)}/model.gltf`);
    const assetURL = new URL(path, location.href);
    if (assetURL.origin !== location.origin) throw Error('Use a local asset URL');
    const gltf = await loader.loadAsync(assetURL.href);
    if (token !== generation) { dispose(gltf.scene); return; }
    if (model) { scene.remove(model); dispose(model); }
    model = gltf.scene; bounds = new THREE.Box3().setFromObject(model);
    const center = bounds.getCenter(new THREE.Vector3()); model.position.sub(center); scene.add(model);
    document.querySelector('#format').value = format;
    setShadows(shadows);
    currentId = id; loadMs = performance.now()-start;
    const select = document.querySelector('#model');
    if (assetPath) {
      let option = [...select.options].find(option => option.value === id);
      if (!option) { option = document.createElement('option'); option.value = id; select.append(option); }
      option.textContent = `${id} (composed)`; option.dataset.asset = assetPath;
    }
    select.value = id;
    const url = new URL(location.href); url.searchParams.set('model',id); url.searchParams.set('format',format); if (assetPath) url.searchParams.set('asset',assetPath); else url.searchParams.delete('asset'); history.replaceState(null,'',url);
    frame(new URL(location.href).searchParams.get('view') ?? 'hero');
    status.textContent = `Loaded in ${loadMs.toFixed(0)} ms. Idle until interaction. Host preview; not a Brick performance measurement.`;
  } catch(error) { if (token === generation) { status.textContent = `Load failed: ${error.message}`; throw error; } }
}
async function benchmark(count = 60) {
  if (benchmarking || !model) return;
  benchmarking = true; const saved = camera.position.clone(), samples = [], start = performance.now();
  // Explicit bounded measurement only; normal viewing has no continuous animation loop.
  for (let i=0;i<count;i++) {
    await new Promise(requestAnimationFrame);
    camera.position.applyAxisAngle(new THREE.Vector3(0,1,0),Math.PI*2/count); camera.lookAt(0,0,0);
    const t = performance.now(); renderer.render(scene,camera); renderer.getContext().finish(); samples.push(performance.now()-t); frames++;
  }
  camera.position.copy(saved); controls.update(); benchmarking=false; invalidate(); samples.sort((a,b)=>a-b);
  const result = {id:currentId,frames:count,elapsedMs:performance.now()-start,medianSubmissionAndFinishMs:samples[Math.floor(count*.5)],p95SubmissionAndFinishMs:samples[Math.floor(count*.95)]};
  result.meanFrameIntervalMs = result.elapsedMs/count;
  status.textContent = `Host frame interval ${result.meanFrameIntervalMs.toFixed(1)} ms; submission + finish: median ${result.medianSubmissionAndFinishMs.toFixed(1)} ms, p95 ${result.p95SubmissionAndFinishMs.toFixed(1)} ms (${count} frames). Browser/host measurement, not native Brick performance.`;
  return result;
}
for (const view of ['front','rear','hero']) document.querySelector('#'+view).onclick = () => frame(view);
document.querySelector('#background').onchange = event => setBackground(event.target.value);
document.querySelector('#lighting').onchange = event => setLighting(event.target.value);
document.querySelector('#benchmark').onclick = () => benchmark();
document.querySelector('#format').onchange = () => { const select = document.querySelector('#model'); load(select.value, document.querySelector('#format').value, select.selectedOptions[0]?.dataset.asset).catch(console.error); };
document.querySelector('#model').onchange = event => load(event.target.value, document.querySelector('#format').value, event.target.selectedOptions[0]?.dataset.asset).catch(console.error);
window.preview = {load,frame,benchmark,setLighting,setShadows,setBackground, stats:() => ({id:currentId,format:document.querySelector('#format').value,frames,loadMs,lastFrameMs,drawCalls:renderer.info.render.calls,triangles:renderer.info.render.triangles,textures:renderer.info.memory.textures,geometries:renderer.info.memory.geometries,renderer:renderer.getContext().getParameter(renderer.getContext().RENDERER)})};
try {
 const catalogue = await fetch('../sources/catalogue.json').then(r=>r.json());
 for (const item of catalogue.models) { const option=document.createElement('option'); option.value=item.id; option.textContent=`${item.name} (${item.id})`; document.querySelector('#model').append(option); }
 setBackground(new URL(location.href).searchParams.get('background') ?? 'paper');
 setShadows(new URL(location.href).searchParams.get('shadows') === '1');
 setLighting(new URL(location.href).searchParams.get('lighting') ?? 'room');
 resize(); await load(new URL(location.href).searchParams.get('model') ?? 'gb',new URL(location.href).searchParams.get('format') ?? 'raw', new URL(location.href).searchParams.get('asset') ?? undefined);
} catch(error) { status.textContent = error.message; console.error(error); }
