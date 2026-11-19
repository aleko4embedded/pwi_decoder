#!/usr/bin/env python3
"""
pwi_decode.py - decoder for Pocket Word Ink (.pwi) files
(Windows CE / Pocket PC "Pocket Word", a.k.a. InkWriter / Note Taker format).

No public specification exists; everything below was reverse-engineered from
sample files. Fields marked "?" are not understood yet and are ignored.

Usage
-----
  python3 pwi_decode.py note.pwi [more.pwi ...]
      prints the text (ink shown as [ink]); if a file contains handwriting,
      note.svg is written next to it automatically (whole page, text + ink)

  options:
    --svg-always    write note.svg even for text-only files
    --no-svg        never write SVGs
    --svg OUT.svg   explicit SVG path (single input file only)
    --ink-dir DIR   additionally save one SVG per handwritten word
    -v              structure dump instead of plain text

FORMAT NOTES
============
All integers little-endian.

File header
  0x00  "{\\pwi"                      magic
  0x34  64 bytes UTF-16LE             default font face (e.g. "Tahoma")
  0x74  record chain; every record starts with u16 type, u16 object id

Records
  0x14, 0x15, 0x07, 0x08              settings / page metrics (not decoded)
  0x40  paragraph index               30-byte header (u16 count at +6), then
                                      count x { u16 kind?, u16 nchars, u32 id }
                                      nchars counts text chars + control tokens
                                      (C2/C3/C4); id -> 0x41 record (0 = none)
  0x41  paragraph                     30-byte header (u32 at +14 -> 0x42 id),
                                      then token stream, 00-terminated
  0x42  line-break table              u16 type, u16 id, u16 ?, then entries of
                                        u16 nchars, u32 ?, u16 width, u16 ?
                                      until the nchars add up to the paragraph's
                                      nchars from the 0x40 index
  0x43  ink object (handwritten word) u16 type, u16 id, u16 ?, u16 0xF0,
                                      u16 0x40, u16 width, u16 nstrokes,
                                      u16 datalen, then the strokes
  0x82  end of document

Paragraph token stream
  E5 u16     font index
  E6 u16     font size, pt
  C2 u16     inline ink object (id of a 0x43 record)
  C3 u16     horizontal gap before the next ink word (layout units)
  C4 u8      end of paragraph
  00         end of stream
  01..7F     ASCII character
  80..BF     non-ASCII character: UTF-8 sequence stored in REVERSE byte
             order, e.g. 95 10 = UTF-8 D0 95 = U+0415 'Е'. The continuation
             bytes come first, then the lead byte with its 110/1110 prefix
             stripped (0x10..0x1F for 2-byte, 0x00..0x0F for 3-byte).
             (2-byte form confirmed with Cyrillic; 3-byte form is a guess.)

Stroke (inside 0x43)
  normal header (4 bytes):    u8 npoints, u8 nbytes, u8 x, s8 y
  extended header (10 bytes): u8 0xF0, u8 ?(0x40), u16 nbytes, u16 npoints,
                              u16 x, s16 y          (used when x > 255 etc.)
  nbytes = whole stroke incl. header. After the header, npoints deltas,
  packed LSB-first: 5-bit signed dx, then 5-bit signed dy.
  Escape: a 5-bit field of 10000 (-16) is followed by a 6-bit signed value
  e giving the step beyond +-15: d = e - 15 if e < 0, else e + 15.
  (Confirmed for negative steps of -16 and -18; the positive branch is
  inferred by symmetry.)
  Positions are cumulative from (x, y); the first delta is always (0, 0).
  Y grows UPWARDS; y = 0 is the text baseline. Trailing zero bits may be
  omitted, so pad with zeros. Units are the same layout units as the
  0x42 line widths.
"""
import os
import struct
import sys

MAGIC = b"{\\pwi"


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


class PWI:
    def __init__(self, path):
        self.b = open(path, "rb").read()
        if not self.b.startswith(MAGIC):
            raise ValueError("not a .pwi file")
        self.font = self.b[0x34:0x74].decode("utf-16le", "replace").split("\0")[0]
        self.paragraphs = self._paragraphs()

    # ---- record lookup -------------------------------------------------
    def find(self, rtype, rid, check=None):
        pat = struct.pack("<HH", rtype, rid)
        o = self.b.find(pat, 0x74)
        while o != -1:
            if o % 2 == 0 and (check is None or check(o)):
                return o
            o = self.b.find(pat, o + 1)
        return -1

    # ---- paragraphs ----------------------------------------------------
    def _index(self):
        o = 0x74
        while True:
            o = self.b.find(b"\x40\x00", o)
            if o == -1:
                raise ValueError("paragraph index not found")
            n = u16(self.b, o + 6)
            if o % 2 == 0 and 0 < n < 10000 and o + 30 + 8 * n <= len(self.b):
                ok = all(u32(self.b, o + 30 + 8 * i + 4) == 0 or
                         self.find(0x41, u32(self.b, o + 30 + 8 * i + 4)) != -1
                         for i in range(n))
                if ok:
                    return [(u16(self.b, e), u16(self.b, e + 2), u32(self.b, e + 4))
                            for e in (o + 30 + 8 * i for i in range(n))]
            o += 1

    def _paragraphs(self):
        paras = []
        for kind, nchars, pid in self._index():
            p = {"kind": kind, "nchars": nchars, "items": [], "lines": [],
                 "font": None, "size": None}
            if pid:
                r = self.find(0x41, pid)
                p["items"], p["font"], p["size"] = self._tokens(r + 30)
                lr = self.find(0x42, u32(self.b, r + 14))
                if lr != -1:
                    p["lines"] = self._lines(lr, nchars)
            paras.append(p)
        return paras

    def _tokens(self, o):
        b, items, font, size, text = self.b, [], None, None, bytearray()

        def flush():
            if text:
                items.append(("text", text.decode("cp1252", "replace")))
                text.clear()
        while o < len(b) and b[o] != 0:
            c = b[o]
            if c == 0xE5:
                font = u16(b, o + 1); o += 3
            elif c == 0xE6:
                size = u16(b, o + 1); o += 3
            elif c == 0xC2:
                flush(); items.append(("ink", u16(b, o + 1))); o += 3
            elif c == 0xC3:
                flush(); items.append(("gap", u16(b, o + 1))); o += 3
            elif c == 0xC4:
                flush(); items.append(("par", None)); o += 2
            elif c >= 0xC0:
                flush(); items.append(("unknown", c)); o += 1
            elif c >= 0x80:
                # reversed UTF-8: continuation bytes, then stripped lead byte
                cont = []
                while o < len(b) and 0x80 <= b[o] <= 0xBF and len(cont) < 3:
                    cont.append(b[o] & 0x3F); o += 1
                lead = b[o] if o < len(b) else 0
                o += 1
                cp = lead & (0x1F if len(cont) == 1 else 0x0F if len(cont) == 2 else 0x07)
                for v in reversed(cont):
                    cp = (cp << 6) | v
                flush_bytes = text.decode("cp1252", "replace")
                text.clear()
                if flush_bytes:
                    items.append(("text", flush_bytes))
                items.append(("text", chr(cp) if 0x80 <= cp <= 0x10FFFF else "\ufffd"))
            elif c < 0x20 and c not in (0x09,):
                flush(); items.append(("unknown", c)); o += 1
            else:
                text.append(c); o += 1
        flush()
        # merge adjacent text runs
        merged = []
        for it in items:
            if merged and it[0] == "text" and merged[-1][0] == "text":
                merged[-1] = ("text", merged[-1][1] + it[1])
            else:
                merged.append(it)
        return merged, font, size

    def _lines(self, o, total):
        lines, e, seen = [], o + 6, 0
        while e + 10 <= len(self.b) and seen < total:
            n, w = u16(self.b, e), u16(self.b, e + 6)
            if n == 0 or seen + n > total:
                break
            lines.append((n, w))
            seen += n
            e += 10
        return lines

    # ---- ink -------------------------------------------------------------
    def ink(self, iid):
        """Return (width, strokes); strokes = list of [(x, y), ...], y up."""
        b = self.b
        r = self.find(0x43, iid, lambda o: b[o + 6:o + 10] == b"\xf0\x00\x40\x00")
        if r == -1:
            return None
        width, nstrokes, dlen = u16(b, r + 10), u16(b, r + 12), u16(b, r + 14)
        data, o, strokes = b[r + 16:r + 16 + dlen], 0, []
        for _ in range(nstrokes):
            if o >= len(data):
                break
            if data[o] == 0xF0:
                nb, npt, x, y = struct.unpack_from("<HHHh", data, o + 2)
                ds = o + 10
            else:
                npt, nb, x = data[o], data[o + 1], data[o + 2]
                y = struct.unpack_from("<b", data, o + 3)[0]
                ds = o + 4
            if nb == 0:
                break
            val = int.from_bytes(data[ds:o + nb], "little")  # LSB-first bits
            pts, bit = [], 0

            def coord():
                # 5-bit signed delta; 10000 (-16) escapes to a 6-bit
                # "excess over 15": d = e - 15 (e < 0) or e + 15 (e > 0)
                nonlocal bit
                d = (val >> bit) & 31
                bit += 5
                if d != 16:
                    return d - 32 if d & 16 else d
                e = (val >> bit) & 63
                bit += 6
                e = e - 64 if e & 32 else e
                return e - 15 if e < 0 else e + 15
            for _ in range(npt):
                x += coord()
                y += coord()
                pts.append((x, y))
            strokes.append(pts)
            o += nb
        return width, strokes

    # ---- output ----------------------------------------------------------
    def text(self):
        out = []
        for p in self.paragraphs:
            s = ""
            for kind, v in p["items"]:
                if kind == "text":
                    s += v
                elif kind == "ink":
                    s += "[ink]"
                elif kind == "gap":
                    s += " "
            out.append(s)
        return "\n".join(out)


def path_d(strokes, ox, oy):
    d = []
    for s in strokes:
        if not s:
            continue
        x0, y0 = s[0]
        d.append(f"M{ox + x0},{oy - y0}")
        if len(s) == 1:
            d.append("l0.5,0")
        d.extend(f"L{ox + x},{oy - y}" for x, y in s[1:])
    return " ".join(d)


def render_svg(doc, margin=20, pt_scale=5.5):
    """Lay paragraphs out line by line, using the 0x42 line tables."""
    parts, y, maxw = [], margin, 0
    for p in doc.paragraphs:
        size = (p["size"] or 10) * pt_scale
        # split tokens into lines by character counts
        toks = []
        for kind, v in p["items"]:
            if kind == "text":
                toks.extend(("char", ch) for ch in v)
            elif kind in ("ink", "gap", "par"):
                toks.append((kind, v))
        counts = [n for n, _ in p["lines"]] or [len(toks) or 1]
        widths = [w for _, w in p["lines"]] or [None]
        pos = 0
        for li, n in enumerate(counts):
            line = toks[pos:pos + n]
            pos += n
            # measure ink extents for line height
            asc, desc = size * 0.8, size * 0.3
            inks = {}
            for kind, v in line:
                if kind == "ink" and v not in inks:
                    inks[v] = doc.ink(v)
                    if inks[v]:
                        ys = [pt[1] for s in inks[v][1] for pt in s] or [0]
                        asc, desc = max(asc, max(ys)), max(desc, -min(ys))
            base = y + asc
            x = margin
            run = ""
            nchars = sum(1 for k, _ in line if k == "char")
            ink_w = sum((inks[v][0] if inks.get(v) else 0) for k, v in line if k == "ink")
            text_w = (widths[li] - ink_w) if (li < len(widths) and widths[li]) else None

            def flush_run():
                nonlocal x, run
                if run:
                    tw = text_w * len(run) / nchars if text_w and nchars else size * 0.55 * len(run)
                    esc = run.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    parts.append(f'<text x="{x}" y="{base}" font-family="{doc.font}, sans-serif" '
                                 f'font-size="{size}" textLength="{tw:.0f}" '
                                 f'lengthAdjust="spacingAndGlyphs" xml:space="preserve">{esc}</text>')
                    x += tw
                    run = ""
            for kind, v in line:
                if kind == "char":
                    run += v
                    continue
                flush_run()
                if kind == "ink" and inks.get(v):
                    w, strokes = inks[v]
                    parts.append(f'<path d="{path_d(strokes, x, base)}"/>')
                    x += w
                elif kind == "gap":
                    x += v
            flush_run()
            maxw = max(maxw, x + (size * 2 if nchars else 0))
            y = base + desc + size * 0.25
    W, H = int(maxw + margin), int(y + margin)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}">\n<rect width="100%" height="100%" fill="white"/>\n'
            f'<g fill="none" stroke="#123" stroke-width="2.5" stroke-linecap="round" '
            f'stroke-linejoin="round">\n' + "\n".join(
                q for q in parts if q.startswith("<path")) +
            '\n</g>\n<g fill="#000">\n' + "\n".join(
                q for q in parts if q.startswith("<text")) + "\n</g>\n</svg>\n")


def ink_svg(width, strokes, pad=10):
    ys = [p[1] for s in strokes for p in s] or [0]
    top, bot = max(ys), min(ys)
    W, H = width + 2 * pad, top - bot + 2 * pad
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}"><path d="{path_d(strokes, pad, top + pad)}" '
            f'fill="none" stroke="#000" stroke-width="2.5" stroke-linecap="round" '
            f'stroke-linejoin="round"/></svg>\n')


def has_ink(doc):
    return any(k == "ink" for p in doc.paragraphs for k, _ in p["items"])


def main(argv):
    args, files, i = argv[1:], [], 0
    opts = {"svg": None, "ink_dir": None}
    flags = set()
    while i < len(args):
        a = args[i]
        if a in ("--svg", "--ink-dir"):
            if i + 1 >= len(args):
                sys.exit(f"{a} needs an argument")
            opts["svg" if a == "--svg" else "ink_dir"] = args[i + 1]
            i += 2
            continue
        if a.startswith("-"):
            flags.add(a)
        else:
            files.append(a)
        i += 1
    if not files:
        sys.exit(__doc__.split("FORMAT NOTES")[0])
    if opts["svg"] and len(files) > 1:
        sys.exit("--svg OUT.svg works with a single input file only")

    status = 0
    for path in files:
        try:
            doc = PWI(path)
        except Exception as e:
            print(f"{path}: error: {e}", file=sys.stderr)
            status = 1
            continue
        if len(files) > 1:
            print(f"==> {path} <==")

        if "-v" in flags:
            print(f"# default font: {doc.font}")
            for n, p in enumerate(doc.paragraphs):
                print(f"para {n}: kind={p['kind']} nchars={p['nchars']} font={p['font']} "
                      f"size={p['size']} lines={p['lines']}")
                for kind, v in p["items"]:
                    if kind == "ink":
                        ink = doc.ink(v)
                        d = f"{len(ink[1])} strokes, width {ink[0]}" if ink else "MISSING"
                        print(f"    ink {v:#x}: {d}")
                    else:
                        print(f"    {kind}: {v!r}")
        else:
            print(doc.text())

        ink = has_ink(doc)
        out = opts["svg"]
        if out is None and "--no-svg" not in flags and (ink or "--svg-always" in flags):
            out = os.path.splitext(path)[0] + ".svg"
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(render_svg(doc))
            print(f"[{'handwriting found, ' if ink else ''}wrote {out}]", file=sys.stderr)

        if opts["ink_dir"] and ink:
            os.makedirs(opts["ink_dir"], exist_ok=True)
            stem = os.path.splitext(os.path.basename(path))[0]
            for p in doc.paragraphs:
                for kind, v in p["items"]:
                    if kind == "ink":
                        res = doc.ink(v)
                        if res:
                            fn = os.path.join(opts["ink_dir"], f"{stem}_ink_{v:04x}.svg")
                            with open(fn, "w", encoding="utf-8") as f:
                                f.write(ink_svg(*res))
            print(f"[wrote ink words to {opts['ink_dir']}/]", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
