#!/usr/bin/env python3
"""Neutralise UPBGE 0.50's startup 3D-audio calls (headless/GPU-less boxes).

Why this exists
---------------
``LA_Launcher::InitEngine`` ends its audio setup with::

    AUD_Device *device = BKE_sound_get_device();
    if (device) {
      AUD_Device_setSpeedOfSound(device, ...);
      AUD_Device_setDopplerFactor(device, ...);
      AUD_Device_setDistanceModel(device, ...);
    }

and audaspace implements those three as::

    auto dev = device ? std::dynamic_pointer_cast<I3DDevice>(*device)
                      : DeviceManager::get3DDevice();
    dev->setSpeedOfSound(value);          // <-- unguarded virtual call

The official linux build links **no I3D-capable backend** (no OpenAL, no
Jack - check ``ldd blenderplayer``), so with a PulseAudio or Null device the
cast returns nullptr and the player/editor segfaults in
``AUD_Device_setSpeedOfSound`` before the first logic tick (backtrace:
``LA_Launcher::InitEngine`` -> ``AUD_Device_setSpeedOfSound``).  The game
never runs, on any display, with any story.

This tool nops the three *call sites* inside ``LA_Launcher::InitEngine``
(the 5-byte ``E8 rel32`` calls whose target is one of the three setters).
Everything else stays intact: audio playback, the story driver, physics,
the whole engine.  3D-audio positioning is left at aud's defaults -
irrelevant for this repo (no ``./audio/`` content ships) and for any
2D-subtitle story.

Usage
-----
    python3 tools/patch_upbge_audio3d.py <upbge-dir> [--check|--revert]

In-place, reversible (original bytes + offsets are stored as JSON in
``<binary>.audio3d.orig`` next to each binary), idempotent, and loud:
every expected byte is asserted, so a future UPBGE build that moves the
code fails the patch instead of corrupting it.  Verified against the
official ``upbge-0.50-linux-x64`` release (Blender 5.0.1, 9fcf0da621c3).
"""
import base64
import json
import os
import struct
import sys

SETTERS = (
    "AUD_Device_setSpeedOfSound",
    "AUD_Device_setDopplerFactor",
    "AUD_Device_setDistanceModel",
)
LAUNCHER = "_ZN11LA_Launcher10InitEngineEv"  # LA_Launcher::InitEngine()
BINARIES = ("blender", "blenderplayer")
NOP = b"\x90" * 5


class Elf:
    """Minimal ELF64 reader: symtab lookup + vaddr<->file offset."""

    def __init__(self, data):
        self.d = data
        (magic,) = struct.unpack_from("<I", data, 0)
        assert magic == 0x464C457F, "not an ELF file"
        assert data[4] == 2, "not ELF64"
        (self.e_shoff,) = struct.unpack_from("<Q", data, 0x28)
        (self.e_shentsize,) = struct.unpack_from("<H", data, 0x3A)
        (self.e_shnum,) = struct.unpack_from("<H", data, 0x3C)
        (self.e_shstrndx,) = struct.unpack_from("<H", data, 0x3E)
        (self.e_phoff,) = struct.unpack_from("<Q", data, 0x20)
        (self.e_phentsize,) = struct.unpack_from("<H", data, 0x36)
        (self.e_phnum,) = struct.unpack_from("<H", data, 0x38)
        self.sections = [
            struct.unpack_from("<IIQQQQIIQQ",
                               data, self.e_shoff + i * self.e_shentsize)
            for i in range(self.e_shnum)]
        self.loads = []
        for i in range(self.e_phnum):
            off = self.e_phoff + i * self.e_phentsize
            p_type, _fl, p_offset, p_vaddr = struct.unpack_from(
                "<IIQQ", data, off)
            if p_type == 1:  # PT_LOAD
                (p_filesz,) = struct.unpack_from("<Q", data, off + 32)
                self.loads.append((p_vaddr, p_offset, p_filesz))

    def _str(self, table_off, idx):
        start = table_off + idx
        end = self.d.index(b"\0", start)
        return self.d[start:end].decode("latin-1")

    def names(self):
        base = self.sections[self.e_shstrndx][4]
        return {self._str(base, sh[0]): sh for sh in self.sections}

    def symbols(self):
        sec = self.names()
        symtab, strtab = sec.get(".symtab"), sec.get(".strtab")
        if not symtab or not strtab:
            return {}
        syms = {}
        for off in range(symtab[4], symtab[4] + symtab[5], 24):
            st_name = struct.unpack_from("<I", self.d, off)[0]
            st_value, st_size = struct.unpack_from("<QQ", self.d, off + 8)
            if st_name:
                syms[self._str(strtab[4], st_name)] = (st_value, st_size)
        return syms

    def file_off(self, vaddr):
        for p_vaddr, p_offset, p_filesz in self.loads:
            if p_vaddr <= vaddr < p_vaddr + p_filesz:
                return p_offset + (vaddr - p_vaddr)
        raise ValueError("vaddr %x not in any PT_LOAD" % vaddr)

    def vaddr(self, file_off):
        for p_vaddr, p_offset, p_filesz in self.loads:
            if p_offset <= file_off < p_offset + p_filesz:
                return p_vaddr + (file_off - p_offset)
        raise ValueError("offset %x not in any PT_LOAD" % file_off)


def _find_sites(data, elf, syms):
    """(file_offset, target_vaddr) per setter call inside the launcher."""
    lv, lsize = syms[LAUNCHER]
    if not lsize:  # local symbols may carry no size: bound by next symbol
        nxt = min(v for v, _s in syms.values() if v > lv)
        lsize = nxt - lv
    loff = elf.file_off(lv)
    targets = {syms[n][0] for n in SETTERS}
    hits = []
    i = 0
    while i < lsize - 5 and loff + i + 5 < len(data):
        if data[loff + i] == 0xE8:
            rel, = struct.unpack_from("<i", data, loff + i + 1)
            target = elf.vaddr(loff + i) + 5 + rel
            if target in targets:
                hits.append((loff + i, target))
                i += 5
                continue
        i += 1
        # stop once we leave the function: next symbol boundary is unknown,
        # so stop at the first 64-byte run of padding/int3 after a hit
        if hits and data[loff + i:loff + i + 64] in (b"\xcc" * 64, b"\x00" * 64):
            break
    return hits, targets


def patch_one(path, mode):
    orig_path = path + ".audio3d.orig"
    with open(path, "rb") as fh:
        data = bytearray(fh.read())
    elf = Elf(bytes(data))
    syms = elf.symbols()
    if LAUNCHER not in syms:
        raise SystemExit("%s: %s not found (different build?)"
                         % (path, LAUNCHER))
    missing = [n for n in SETTERS if n not in syms]
    if missing:
        raise SystemExit("%s: missing symbols %s" % (path, missing))

    if mode in ("check", "revert") and os.path.exists(orig_path):
        with open(orig_path) as fh:
            rec = json.load(fh)
        if mode == "check":
            ok = all(data[off:off + 5] == NOP for off in rec["sites"])
            print("%s: patched (%d sites, bytes %s)"
                  % (path, len(rec["sites"]), "ok" if ok else "TAMPERED"))
            if not ok:
                raise SystemExit(1)
            return
        raw = base64.b64decode(rec["orig"])
        for k, off in enumerate(rec["sites"]):
            data[off:off + 5] = raw[k * 5:(k + 1) * 5]
        with open(path, "wb") as fh:
            fh.write(data)
        os.remove(orig_path)
        print("%s: reverted" % path)
        return

    if os.path.exists(orig_path):
        print("%s: already patched (sidecar present)" % path)
        return
    hits, targets = _find_sites(data, elf, syms)
    if mode == "check":
        if len(hits) != len(SETTERS):
            raise SystemExit("%s: expected %d setter calls, found %d"
                             % (path, len(SETTERS), len(hits)))
        print("%s: unpatched (%d call site(s))" % (path, len(hits)))
        return
    if mode == "revert":
        print("%s: nothing to revert" % path)
        return
    if len(hits) != len(SETTERS):
        raise SystemExit("%s: expected %d setter calls, found %d"
                         % (path, len(SETTERS), len(hits)))
    sites, store = [], b""
    for off, _t in sorted(hits):
        assert data[off] == 0xE8
        store += bytes(data[off:off + 5])
        sites.append(off)
        data[off:off + 5] = NOP
    with open(orig_path, "w") as fh:
        json.dump({"sites": sites,
                   "orig": base64.b64encode(store).decode()}, fh)
    with open(path, "wb") as fh:
        fh.write(data)
    print("%s: patched %d call site(s): %s"
          % (path, len(sites), ", ".join(SETTERS)))


def main(argv):
    modes = [a for a in argv[1:] if a.startswith("--")]
    mode = modes[0].lstrip("-") if modes else "patch"
    if mode not in ("patch", "check", "revert"):
        raise SystemExit(__doc__)
    args = [a for a in argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    for b in BINARIES:
        p = os.path.join(args[0], b)
        if os.path.isfile(p):
            patch_one(p, mode)
        else:
            print("skip %s (missing)" % p)


if __name__ == "__main__":
    main(sys.argv)
