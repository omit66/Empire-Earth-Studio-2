# Empire Earth Studio II

A better version of the original EEStudio, that hopefully will finally no longer be used. :)

### Supported games

- Empire Earth BETA
- Empire Earth DEMO
- Empire Earth (CD / Retail)
- Empire Earth AoC (Addon)
- Empire Earth Gold Edition (GOG)
- Empire Earth DOMW (SST only, `data.ssa` can be read and patched with the command line tool)

### How to install

- Download the version you need from the [release page](https://github.com/EE-modders/Empire-Earth-Studio-2/releases).
- Run the portable version or install it using the installer.

### How to use

Tutorials can be found in our [Empire Earth wiki](https://github.com/EE-modders/Empire-Earth-toolbox/wiki).

### How to run from source code

```bash
git clone https://github.com/EE-modders/Empire-Earth-Studio-2.git
git submodule update --init
cd Empire-Earth-Studio-2/src
pip3 install -r requirements.txt

# run it
python3 EEStudio2.py
```

### Command line tool

`src/eestool.py` works without PyQt5 and can rebuild SSTs from edited TGA / DDS parts and patch files in an SSA archive
without touching anything else in it (used for HD texture packs of Empires: Dawn of the Modern World).
Step by step guide (unpack, upscale, repack): [docs/HD-TEXTURES.md](docs/HD-TEXTURES.md). Tests: `python -m unittest discover -s tests`.

### Known limitations / bugs

- the SST Slicer does sometimes produce black borders when using JOIN
  (It should however work with the default split of 4x3, which EE is using)
- on the Slicer tab Drag & Drop does not work (use the select button instead)

### Reporting

If you find any bug or if you want to request a new feature, please feel free to

- [create a new issue](https://github.com/EE-modders/Empire-Earth-Studio-2/issues) on GitHub or
- join our [Empire Earth: Reborn Discord](https://discord.gg/BjUXbFB) server
