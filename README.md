# Cartridge assets

Cartridge and optical-disc models for game libraries, including regional shells and special hardware.

- `sources/`: editable Blender scenes and their accuracy limits.
- `catalogue/`: physical editions, regional defaults and model mappings.
- `tools/cartridge.py`: edition resolution, artwork acquisition and glTF composition.
- `tools/release_assets.py`: deterministic, range-addressable artwork releases.
- `tools/sync_library_assets.py`: install only the artwork used by an SD library.

The library contains 43 models, 39 edition mappings and 39 release-specific artwork recipes. Generated `assets/` and `assets-compressed/` exports are not committed. Compressed exports use meshopt compression and 16-bit position/UV precision; editable sources retain their full geometry.

## Artwork

Run commands with `uv run python tools/cartridge.py`:

```sh
uv run python tools/cartridge.py resolve gbc --title 'Pokémon Gold' --region JP
uv run python tools/cartridge.py artwork tetris-us-dmg-tr-usa-1 --output label.png
uv run python tools/cartridge.py fetch 'https://example.org/label.png'
uv run python tools/cartridge.py prepare scan.png --crop 99 258 692 785 --output label.png
uv run python tools/cartridge.py assemble gb --title Tetris --region US --artwork label.png --output library/tetris.gltf
uv run python tools/cartridge.py assemble psx --edition castlevania-sotn-us --output library/castlevania.gltf
uv run python tools/cartridge.py compose model.glb --slot artwork.front --artwork label.png --output game.gltf
```

Use a release-matched label scan. Cartridge photographs need an explicit crop or perspective rectification; box artwork is not a cartridge label. `prepare --quad` takes four corners in top-left, top-right, bottom-right, bottom-left order and requires `--size WIDTH HEIGHT`. The output size must match the intended label proportions. `compose` preserves the selected material’s UV coordinates and rejects artwork whose proportions differ by more than 2% from the reference texture. Use `--fit contain` for transparent padding or `--fit cover` for an intentional crop. Some shaped labels use a full-cartridge reference image; their recipes retain that UV contract. Exported models use stable `artwork.front` and `artwork.disc` slots. `assemble` resolves the edition, selects its built model and shares resources across the output library; models not yet built produce an error. Use `--format compressed` to assemble from a compressed model. Use repeatable `--surface artwork.top=top.png` or `--surface artwork.rear=rear.png` to replace additional labelled surfaces. Exact editions retain their verified secondary artwork. Generic selections hide all unreplaced artwork surfaces to avoid showing another game’s title; board imagery still comes from the reference model. PNG and JPEG labels are supported; source preparation also accepts WebP. Images are normalized to sRGB. `assemble` stores identical buffers and images once in the output directory’s `shared/` folder. `compose` embeds resources by default; its `--shared-assets assets/shared` option enables sharing. Verified acquisition recipes in `catalogue/artwork.json` record release, checksum, crop or perspective corners, and paired surfaces. The output retains provenance per acquired surface. Explicit `--surface` replacements override automatic secondary recipes. Unused replaced textures and embedded image bytes are removed.

A title without enough information to choose a physical edition returns an ambiguity error. Unknown games use an explicitly marked generic shell. SNES and Mega Drive require a region.

Omit `--artwork` to download and prepare the selected edition's verified recipe. Editions without a recipe require a prepared image; artwork is never guessed from a different release. Optical-disc scans use `artwork.disc`; the model retains its physical hole, rim and underside.

Verified colour variants reuse the base geometry and change only named shell materials. For example, `assemble gb --edition pokemon-red-us-1 --output library/red.gltf` applies the US red shell and label; Japanese Red retains the grey shell. Photo-derived colours are approximate, not measured material albedo.

```sh
uv run python -m unittest discover -s tests
```

Embedded artwork retains its original ownership; no blanket licence is asserted for third-party artwork.

## Library synchronisation

An artwork release stores prepared images once by SHA-256 in bounded pack files.
The index maps ROM hashes and exact catalogue names to physical models and label
surfaces. No game artwork is included in the base firmware.

```sh
INDEX=https://github.com/jkingston/trimui-brick-cartridge-assets/releases/download/v0.2.0/artwork-index.json
uv run python tools/sync_library_assets.py /run/media/$USER/brick-data \
  --index "$INDEX" --dry-run
uv run python tools/sync_library_assets.py /run/media/$USER/brick-data \
  --index "$INDEX" --prune
```

The synchroniser reads single-ROM ZIP files without extracting them, normalises
common NES and SNES headers, and prefers SHA-256, SHA-1 or CRC32 identity over an
exact-name fallback. Ambiguous and missing entries are reported rather than
guessed. It writes checksummed objects, a minimal `Assets/Brick/library.json`,
the required neutral models, and attribution for the installed subset. Writes are staged and atomically
renamed. It never uploads library information.

With the custom firmware running, the same operation works over USB SSH. ROMs
are enumerated and cartridge files are hashed locally on the Brick; only the
small inventory travels to the host. The completed asset directory is streamed
back and atomically swapped into place.

```sh
python3 tools/sync_ssh_assets.py --dry-run
python3 tools/sync_ssh_assets.py
```

The defaults use the Brick's USB address and the matching published release.
Both workflows run from the host and require no wireless service on the Brick.

## Build

Blender 4.5.9 is the tested authoring/export version. Compression uses gltfpack 1.3.

```sh
blender -b -t 3 --python tools/export_assets.py -- --skip-current
uv run python tools/compress_assets.py
```

`compress_assets.py` expects `gltfpack` on PATH; `--gltfpack-command` accepts an explicit command. Build reports record source hashes, material limits and output sizes.

For the 1024×768 handheld runtime profile:

```sh
uv run python tools/build_runtime_assets.py
```

This caps textures at 512 pixels and applies error-bounded mesh simplification.
Topology-sensitive transparent shells retain locked borders; other shells use
permissive simplification. Per-model reports record the selected policy,
texture dimensions, material-slot preservation and output checksum.

Create a production release after building that profile:

```sh
make release PYTHON=.venv/bin/python VERSION=0.2.0
```

This produces range-addressable screen-sized artwork and neutral runtime GLBs,
plus a reproducible complete model archive. Neutral models retain each artwork
mesh and UV set but contain no game-specific label image.
`tools/verify_release.py` verifies every artwork hash, byte range, dimension and
catalogue reference.

For a portable glTF library with shared geometry and images:

```sh
uv run python tools/share_assets.py --output build/shared assets/*/model.gltf
```

The output directory must be new. Keep its model files and `shared/` resources together. Only identical bytes are shared; UVs, materials and regional geometry remain unchanged. This reduces storage, not draw calls or GPU memory. Standalone compressed GLBs remain the smaller deployment option.

## Preview

```sh
cd preview
npm ci
npm start
```

The local viewer renders only when needed. `npm test` checks loading and idle rendering; set `GPU=1` to use the host OpenGL GPU and `CHROMIUM_EXECUTABLE` to select a browser. Desktop results do not establish handheld performance. Transmission, photographed colours and estimated dimensions have limits recorded with their source models.
