import { chromium } from 'playwright';
import { mkdir, writeFile, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { serve } from './server.mjs';
const output = fileURLToPath(new URL(`../build/preview-verification-${process.env.REPORT ?? process.env.FORMAT ?? 'raw'}/`,import.meta.url));
await mkdir(output,{recursive:true});
const server = serve(0); await new Promise(resolve=>server.on('listening',resolve));
const browser = await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_EXECUTABLE,args:process.env.GPU === '1' ? ['--enable-gpu','--ignore-gpu-blocklist','--use-gl=angle','--use-angle=gl'] : ['--enable-unsafe-swiftshader']});
try {
 const page = await browser.newPage({viewport:{width:1024,height:768},deviceScaleFactor:1});
 const errors=[]; page.on('pageerror',error=>errors.push(error.message));
 page.on('console',msg=>{if(msg.type()==='error') errors.push(msg.text());});
 await page.goto(`http://127.0.0.1:${server.address().port}/preview/?model=gb`);
 await page.waitForFunction(()=>window.preview?.stats().id==='gb',{},{timeout:120000});
 await page.evaluate(name=>window.preview.setLighting(name),process.env.LIGHTING ?? 'room');
 const catalogue=JSON.parse(await readFile(new URL('../sources/catalogue.json',import.meta.url)));
 const ids=process.env.MODELS?.split(',') ?? ['gb','gba-ruby','psx','crystal'];
 const measurements=[];
 for (const id of ids) {
  await page.evaluate(({id,format})=>window.preview.load(id,format),{id,format:process.env.FORMAT ?? 'raw'});
  for(const view of (process.env.VIEWS ?? 'front,rear').split(',')) {
   await page.evaluate(view=>window.preview.frame(view),view);
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   await page.screenshot({path:`${output}/${id}-${view}.png`});
  }
  const before=await page.evaluate(()=>window.preview.stats());
  await page.waitForTimeout(1000);
  const after=await page.evaluate(()=>window.preview.stats());
  if(after.frames!==before.frames) throw Error(`${id} rendered while idle`);
  const benchmark=await page.evaluate(count=>window.preview.benchmark(count),Number(process.env.FRAMES ?? 12));
  measurements.push({...after,idleFrames:after.frames-before.frames,benchmark});
  await writeFile(`${output}/progress.json`,JSON.stringify({measurements,errors},null,2));
 }
 if(process.env.ALL_MODELS==='1') {
  for(const {id} of catalogue.models) {
   if(ids.includes(id)) continue;
   await page.evaluate(({id,format})=>window.preview.load(id,format),{id,format:process.env.FORMAT ?? 'raw'});
   await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
   measurements.push(await page.evaluate(()=>window.preview.stats()));
   await writeFile(`${output}/progress.json`,JSON.stringify({measurements,errors},null,2));
  }
 }
 if (process.env.ASSET) {
  await page.evaluate(asset=>window.preview.load('composed','compressed',asset),process.env.ASSET);
  for (const view of (process.env.VIEWS ?? 'front,rear').split(',')) { await page.evaluate(v=>window.preview.frame(v),view); await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))); await page.screenshot({path:`${output}/composed-${view}.png`}); }
  measurements.push(await page.evaluate(()=>window.preview.stats()));
 }
 const gpu=await page.evaluate(()=>{const gl=document.querySelector('canvas').getContext('webgl2'); const ext=gl.getExtension('WEBGL_debug_renderer_info'); return {vendor:ext?gl.getParameter(ext.UNMASKED_VENDOR_WEBGL):null,renderer:ext?gl.getParameter(ext.UNMASKED_RENDERER_WEBGL):null};});
 const report={browser:browser.version(),gpu,viewport:{width:1024,height:768},measurements,errors};
 await writeFile(`${output}/report.json`,JSON.stringify(report,null,2)); console.log(JSON.stringify(report,null,2));
 if(errors.length) process.exitCode=1;
} finally {await browser.close();server.close();}
