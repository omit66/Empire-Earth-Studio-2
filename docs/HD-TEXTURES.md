# HD textures for Empires: Dawn of the Modern World - step by step

> **Basis of this guide:** everything here is based on one project, the HD texture pack for
> *Empires: Dawn of the Modern World* (retail `data.ssa`), and was only tested on that game and its files.
> Empire Earth and its add-ons may differ (other SST types, other archive layout, other texture rules).
> The name lists and the factors per group are results of that experience, not general rules.

This guide shows how to unpack `data.ssa`, upscale the textures and pack everything into a new `data.ssa`,
using only `src/eestool.py` (no GUI, no PyQt5). It is the way it was done for the first HD texture pack.

Tested with the retail `data.ssa` (9751 files, 2875 textures): parse -> assemble gives the identical file and
2867 of 2867 SSTs can be rebuilt byte for byte from their own parts. A full run on that archive worked, the
game starts and ran for an hour with the result. **Not tested:** Empire Earth itself, `moredata.ssa` (it is an OLE2 container, not an
SSA archive), Linux, macOS, and the GUI after the `lib/BinUtil.py` change.

## 0. What you need

| | |
|---|---|
| Python 3.9+ | no third party packages needed |
| `libDCL.dll` / `libDCL.so` | compression of the archive entries. Take `lib\DCL\libDCL.dll` from the portable EE Studio II release, or build it: `cd src/lib/DCL && make windows` (MinGW) / `make linux`. Put it next to `DCL.py`, or set `EES_LIBDCL` to the file or its folder |
| ImageMagick (`magick`) | `winget install ImageMagick.ImageMagick` |
| [Real-ESRGAN ncnn Vulkan](https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/releases) | unpack it, the `models` folder has to be next to the exe. Needs a Vulkan GPU |
| disk space | about 5 GB for all part folders and the new SSTs (measured: originals 0.3 GB, upscaled and repaired parts 3 GB, new SSTs 0.9 GB), plus about 1 GB per archive you write |

All commands are run in `src` of this repository. Paths below are examples, use your own.

```
cd Empire-Earth-Studio-2\src
set EES_LIBDCL=C:\Tools\EEStudio2\lib\DCL
```

**Step 0 - back up `data.ssa`.** Copy it, e.g. to `data.ssa.bak`. All commands below read the backup and write new files,
none of them overwrites their input. A damaged `data.ssa` is a re-install of the game.

## 1. Look at the archive (optional)

```
python eestool.py ssa-extract "D:\Game\Data\data.ssa.bak" --out D:\hd\extracted --folder textures
python eestool.py ssa-selftest "D:\Game\Data\data.ssa.bak"
```

`ssa-extract` writes the decompressed files (without `--folder` all 9751). `ssa-selftest` reads the archive and writes it
again: both ways must give the identical file.

## 2. Split the SSTs into parts

```
python eestool.py sst-split "D:\Game\Data\data.ssa.bak" --out D:\hd\in
```

Every SST is split into one TGA or DDS file per resolution (mip level) or tile, named `<name>_<i>-<n>_RES.tga|dds`
(tiles: `<name>_<i>-<n>`, single images: `<name>.tga`). That are about 20 000 files, roughly 330 MB for the retail archive.
Names that collide (`name.sst` and `name.xyz.sst`) and SSTs of unknown types are skipped and reported.

**Check the toolchain before you spend hours on the GPU:**

```
python eestool.py sst-selftest "D:\Game\Data\data.ssa.bak" --parts D:\hd\in
```

This builds every SST again from the *original* parts. It has to say `different: 0`.

## 3. Decide what to upscale

Not every texture can be made bigger. The **user interface** (menus, buttons, icons, backgrounds, cursors) is addressed
with pixel coordinates: with doubled size the game shows only the top left quarter, enlarged, and borders are missing.
So there are three groups; lists for the retail game are in [docs/lists](lists) (from `src`: `..\docs\lists`):

| list | factor | model | content |
|---|---|---|---|
| `domw-world.txt` (934 SSTs) | 2 | `realesr-animevideov3-x4` | units, buildings, terrain, water, shadows |
| `domw-world-organic.txt` (419) | 2 | `realesrgan-x4plus` | smoke, effects, trees, vegetation (the anime model makes these look painted) |
| `domw-ui.txt` (1516) | 1 | `realesr-animevideov3-x4` | user interface: the AI works at 4x, the result is scaled back to the original size: cleaner edges, same pixel size |

The lists were made by name and by which model / unit files mention a texture; they can contain mistakes.
If something looks wrong in the game, remove it from the list (or move it to the `1` group) and rebuild.
Factor 2 means four times the memory per texture. The game is a 32 bit program, but memory was not a problem in a
longer test.

## 4. Upscale (or edit) the parts

From here on the parts in `in` are ordinary image files, so **you can use any tool you like**: another AI upscaler,
Photoshop / GIMP, a batch script, hand painting, or a mix of them. `eestool upscale` is only one convenient way,
and it is not required. What the later steps need from the result is:

- the **same file names** as in `in` (one folder per tool or group is fine), only TGA or DDS files
- **size** = original size times the factor you chose (2 = doubled edge length, 1 = unchanged)
- **TGA:** uncompressed truecolor (type 2), same bit depth as the original (24 or 32 bit)
- **DDS:** DXT1, DXT3 or DXT5, one image without mip maps; if the format differs from the original, `fix-parts` converts it
- alpha: if your tool keeps the alpha channel, it is used; if it is missing, `fix-parts` puts the original's alpha back

If your tool writes PNG, convert to the original names and format first, e.g. `magick x.png -type TrueColorAlpha -depth 8 -compress none x.tga`
(use `-type TrueColor` for 24 bit originals) or `magick x.png -define dds:compression=dxt5 -define dds:mipmaps=0 x.dds`.
Step 5 checks sizes and formats and reports every part that does not fit, so mistakes show up before anything is built.

**What we did and why** (retail Dawn of the Modern World): we used Real-ESRGAN (ncnn, Vulkan) because it is free, runs on
any Vulkan GPU without Python or CUDA, and has ready-made models. Two models were compared side by side on 19 textures:

- `realesr-animevideov3-x4` was preferred on almost all of the 19 sample textures (a subjective choice) and is about
  ten times faster (2 s instead of 22 s for the samples), so it became the default.
- `realesrgan-x4plus` was clearly better for smoke, effects, trees and vegetation (the other one makes them look painted),
  so those groups use it.
- Real-ESRGAN always works at 4x. The result is scaled down with Lanczos to the wanted size (2x for the 3D world, 1x for the
  user interface, where the game does not accept bigger textures).

Your taste or other textures may favour a different tool. Compare a few textures side by side before you run everything.
`preview` does that for Real-ESRGAN models: it takes a random sample of parts and writes sheets with the original and one
column per model.

```
python eestool.py preview --orig D:\hd\in --out D:\hd\cmp --model realesr-animevideov3-x4 --model realesrgan-x4plus --esrgan %E% --count 30
```

Note that normal / bump maps (names ending in `_bm` or containing `bump`) are not pictures: an AI upscaler invents noise
or smooths them, which changes the lighting in the game. Scale them with a plain resize instead: `upscale --plain` resizes with Lanczos and does not use the AI at all.
`preview --plain --match bump` shows the difference.
Every resolution of a texture is a separate part, so the small mip levels can be treated differently from the big ones
(for example only upscale level 1 with the AI and let a script shrink it for the others).

```
set E=C:\Tools\realesrgan\realesrgan-ncnn-vulkan.exe
python eestool.py upscale --orig D:\hd\in --out D:\hd\out_world --sst-list ..\docs\lists\domw-world.txt --factor 2 --esrgan %E%
python eestool.py upscale --orig D:\hd\in --out D:\hd\out_organic --sst-list ..\docs\lists\domw-world-organic.txt --factor 2 --model realesrgan-x4plus --esrgan %E%
python eestool.py upscale --orig D:\hd\in --out D:\hd\out_ui --sst-list ..\docs\lists\domw-ui.txt --factor 1 --esrgan %E%
```

Interrupted runs continue where they stopped (existing output files are skipped). Parts smaller than 16 px are not
sent to the AI. A few parts may fail (names are printed); run the command again.

## 5. Restore alpha channels and the original DXT formats

```
python eestool.py fix-parts --orig D:\hd\in --upscaled D:\hd\out_world   --fixed D:\hd\fixed_world   --factor 2
python eestool.py fix-parts --orig D:\hd\in --upscaled D:\hd\out_organic --fixed D:\hd\fixed_organic --factor 2
python eestool.py fix-parts --orig D:\hd\in --upscaled D:\hd\out_ui      --fixed D:\hd\fixed_ui      --factor 1
```

Add `--dry-run` to only count. Why this is needed:

- Real-ESRGAN (ncnn) delivers images **without alpha**. Transparent areas (grass, fences, trees, frames, effects)
  would become opaque, in the game e.g. black boxes around trees. The alpha channel of the original is scaled and put back.
  DXT1 with 1 bit alpha gets its own encoder, because ImageMagick writes DXT1 without transparency.
- ImageMagick cannot write **DXT3**. The game takes the surface format from the SST header, so a wrong format can mean
  black or wrong textures or a crash. DXT3 parts are made from DXT5 (the colour block is the same, only the alpha block
  is converted).

The repaired parts are written to the `--fixed` folder, the upscaled files are not touched.

**You may not need this step:** it exists to make up for these two weaknesses of the tools used here (Real-ESRGAN ncnn and
ImageMagick). If your tool keeps the alpha channel and writes the original format, `fix-parts` changes almost nothing
(`keep`). It is still worth running: it also checks the size and bit depth of every part, so mistakes show up here and
not later as SSTs that `sst-rebuild` skips. Without it, put your parts directly into the `--parts` folders of step 6.

## 6. Build the new SSTs

```
python eestool.py sst-rebuild "D:\Game\Data\data.ssa.bak" --parts D:\hd\fixed_world   D:\hd\out_world   --out-dir D:\hd\new --scale 2 --only ..\docs\lists\domw-world.txt
python eestool.py sst-rebuild "D:\Game\Data\data.ssa.bak" --parts D:\hd\fixed_organic D:\hd\out_organic --out-dir D:\hd\new --scale 2 --only ..\docs\lists\domw-world-organic.txt
python eestool.py sst-rebuild "D:\Game\Data\data.ssa.bak" --parts D:\hd\fixed_ui      D:\hd\out_ui      --out-dir D:\hd\new --scale 1 --only ..\docs\lists\domw-ui.txt
```

`--parts` takes several folders, earlier ones win. An SST is only built if **all** its parts exist and fit
(size, format, bit depth); otherwise it is skipped and stays as in the original. The reasons are written to
`skipped.txt` in the output folder (the last run overwrites it). `--exclude cur_*` leaves SSTs out by wildcard.
With `--scale 2` one image is added to the resolution chain (the original smallest image), so the chain still ends at 1x1.

## 7. Write the new archive

```
python eestool.py ssa-patch "D:\Game\Data\data.ssa.bak" D:\hd\new -o "D:\Game\Data\data_HD.ssa"
```

The folder has to contain `textures\*.sst`. Replaced entries are compressed again if the original entry was compressed
(`--no-compress` stores them raw: bigger file, needs no `libDCL`, but not tested in the game). Index order, metadata and
all other files stay byte for byte as they were. `ssa-patch` refuses to overwrite its input and reads the result
again to verify it; it has to end with `verification: OK`.

## 8. Try it in the game

Rename the old `data.ssa` and the new file:

```
cd D:\Game\Data
ren data.ssa data.ssa.orig
ren data_HD.ssa data.ssa
```

Check a unit, a building, terrain, water, trees and the menus. To go back, rename the files again. If the game looks
pixelated, check the graphics wrapper (dgVoodoo: resolution, mipmapping, filtering) before blaming the textures.
If single textures look wrong, remove them from the lists, run steps 6 and 7 again (a few minutes, the AI does not
run again).

## 9. The format (verified on all texture SSTs)

| SST header | |
|---|---|
| byte 0 | revision: 0 = TGA images, 1 = DDS images |
| byte 1 / 2 | number of resolutions (mip levels) / tiles |
| byte 6-9 / 10-13 | width / height of the biggest image |
| byte 14 | revision 0: 0. revision 1: format code 4 = DXT1, 5 = DXT1 + 1 bit alpha, 7 = DXT3, 9 = DXT5 |

The body is the images one after another, separated by one byte (revision 0: `0`, revision 1: the format code
again), no separator after the last image. Revision 0 files with several resolutions store the 2x2 image with 5 extra
bytes; the 2x2 and 1x1 images are treated as an untouched tail. EE Studio's slicer cannot split these correctly (the last
two parts of such an SST are garbage), `eestool` does not need them. The SSA archive (magic `rass`) is handled by
`lib/SSA/SSA.py`; entries starting with `PK01` are DCL compressed (PKWare implode).

## 10. Troubleshooting

| | |
|---|---|
| `libDCL not found` | see section 0 (`EES_LIBDCL`) |
| `no decode delegate`, `magick failed` | ImageMagick missing or not in `PATH`; open a new terminal after installing |
| `sst-selftest` shows `different` | do not continue, the build chain does not match your files. Please report it |
| SST is skipped: `part is 512x512, expected ...` | a part has the wrong size, rerun `upscale` / `fix-parts` with the right `--factor` |
| menu is zoomed in, borders missing | a user interface texture was made bigger: use factor 1 for it |

## Distributing results

The textures in `data.ssa` are copyrighted game assets and so are upscaled versions of them. Share the tool,
the name lists and your settings, not the archive itself.
