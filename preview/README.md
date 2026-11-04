# Runtime preview

`npm ci && npm start` in this directory, then open http://127.0.0.1:8088/preview/.
The server binds to loopback only. No CDN or external runtime requests.

Uses Three.js's official glTF loader, meshopt decoder and physically based
transmission/volume materials. Models keep their physical proportions; the
camera fits their bounds. Rendering stops completely when idle. Model changes
dispose GPU resources. There is no animation loop or damping timer.

`npm test` loads GB, Ruby, PSX and Crystal, captures both sides, checks idle frame
counts and measures a bounded 12-frame rotation. Install Playwright's Chromium
with `npx playwright install chromium` if needed. `ALL_MODELS=1 npm test` also
loads the entire catalogue. Results are written to `build/preview-verification-raw` (or `-compressed`). `FORMAT=compressed` selects
Meshopt GLBs; `MODELS=gb,gba-ruby,psx` narrows the set. `FRAMES=60` runs a longer
measurement. `CHROMIUM_EXECUTABLE=/path/to/chromium` selects an existing browser.
`ASSET=../build/library/tetris-compressed.gltf` also checks a composed asset.
Timings include `gl.finish()` and describe that host/browser only, not the Brick.

A composed file can also be opened using `?model=composed&asset=../build/library/example.gltf`.

This is a material/export inspection tool, not firmware UI. Blender-specific
shaders cannot be assumed equivalent to glTF; compare the actual rendered asset.

The Room and Studio strips presets change reflected illumination, never asset
materials. The latter makes dark glossy optical discs legible. Camera clipping
is proportional to the model bounds, retaining depth precision for thin labels
and polished hub layers. `LIGHTING=studio VIEWS=front,rear-hero npm test` captures
those views. `REPORT=name` keeps independent runs in separate output folders.

Front/rear presets move the key and fill lights to the inspected side; manual orbit keeps the lighting fixed.

Raking light adds grazing illumination and self-shadows to inspect shallow
moulded lettering. Paper and Dark backgrounds help separate the silhouette.
These are inspection presets, not changes to the supplied materials. Three.js's
transmission approximation can make layered transparent shells (notably Crystal)
look frostier than matched path-traced renders; changing the background does not
remove that difference.
