import { chromium } from 'playwright';
import { readFile } from 'node:fs/promises';
import { serve } from './server.mjs';

const [manifestPath] = process.argv.slice(2);
if (!manifestPath) throw Error('usage: node capture.mjs MANIFEST.json');
const jobs = JSON.parse(await readFile(manifestPath));
const server = serve(0);
await new Promise(resolve => server.on('listening', resolve));
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_EXECUTABLE,
  args: process.env.GPU === '1'
    ? ['--enable-gpu', '--ignore-gpu-blocklist', '--use-gl=angle', '--use-angle=gl']
    : ['--enable-unsafe-swiftshader'],
});
try {
  const page = await browser.newPage({viewport: {width: 512, height: 512}, deviceScaleFactor: 1});
  await page.goto(`http://127.0.0.1:${server.address().port}/preview/?model=gb`);
  await page.waitForFunction(() => window.preview?.stats().id === 'gb', {}, {timeout: 120000});
  await page.evaluate(() => {
    window.preview.setBackground('transparent');
    document.querySelector('header').style.display = 'none';
    document.querySelector('footer').style.display = 'none';
    document.body.style.background = 'transparent';
  });
  for (const job of jobs) {
    await page.evaluate(({id, asset}) => window.preview.load(id, 'compressed', asset), job);
    await page.evaluate(() => window.preview.frame('hero'));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await page.locator('canvas').screenshot({path: job.output, omitBackground: true});
  }
} finally {
  await browser.close();
  server.close();
}
