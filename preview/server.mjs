import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
const root = fileURLToPath(new URL('../', import.meta.url));
const types = {'.html':'text/html', '.js':'text/javascript', '.mjs':'text/javascript', '.json':'application/json', '.gltf':'model/gltf+json', '.bin':'application/octet-stream', '.png':'image/png', '.jpg':'image/jpeg', '.webp':'image/webp', '.css':'text/css', '.wasm':'application/wasm'};
export function serve(port = 8088) {
  return createServer(async (req, res) => {
    try {
      const path = resolve(root, '.' + decodeURIComponent(new URL(req.url, 'http://localhost').pathname));
      if (!path.startsWith(root.endsWith(sep) ? root : root + sep)) throw Error('Invalid path');
      const file = (await stat(path)).isDirectory() ? resolve(path, 'index.html') : path;
      const data = await readFile(file);
      res.writeHead(200, {'Content-Type':types[extname(file)] ?? 'application/octet-stream', 'Cache-Control':'no-store'}).end(data);
    } catch { res.writeHead(404).end('Not found'); }
  }).listen(port, '127.0.0.1');
}
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const port = Number(process.env.PORT ?? 8088);
  serve(port); console.log(`http://127.0.0.1:${port}/preview/`);
}
