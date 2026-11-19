# pwi_decode — Pocket Word Ink (.pwi) decoder

Extracts text and handwriting from **Pocket Word Ink** (`.pwi`) files, the
note format of Pocket Word on Windows CE / Pocket PC / Windows Mobile PDAs
(also called *InkWriter / Note Taker* documents).

## About

Microsoft never published the `.pwi` format, and no open-source reader exists
(LibreOffice's old Pocket Word filter handled only `.psw`, not `.pwi`).
Today's Word and LibreOffice can't open these files, and the handwriting in
them is usually considered lost.

This decoder was reverse-engineered from real sample files. It recovers:

- **Typed text**, including non-Latin scripts (tested with German and
  Cyrillic).
- **Handwriting (ink)**: every stroke is decoded into vector coordinates.
- **Page layout**: line breaks and the placement of handwritten words
  between the lines of text.

Output:

- Plain text on standard output. Handwritten words appear as `[ink]`.
- An **SVG image** of the whole page (text plus handwriting) whenever a file
  contains handwriting. SVG is vector, so it stays sharp at any zoom, and it
  opens in any web browser, Inkscape and similar programs.

### Requirements

- Python 3.6 or newer. No extra packages.
- Works on Linux, macOS and Windows.
- `decode_wrapper.sh` needs a Bash shell (Linux, macOS, or Git Bash/WSL on
  Windows).

## Bundle contents

| File                | Purpose                                                  |
|---------------------|----------------------------------------------------------|
| `pwi_decode.py`     | The decoder. All the logic lives here.                   |
| `decode_wrapper.sh` | Shell helper that decodes one file or a whole folder and saves the text next to each file. |
| `README.md`         | This document.                                           |

## Usage: `pwi_decode.py`

```
python3 pwi_decode.py note.pwi [more.pwi ...] [options]
```

Default behaviour:

- The text of each file is printed to standard output.
- If a file contains handwriting, `note.svg` is written next to `note.pwi`.
  A message such as `[handwriting found, wrote note.svg]` goes to standard
  error, so it never ends up in redirected text.
- Files with only typed text produce no SVG.
- With several input files, each one's text is headed `==> name <==`.

Options:

| Option          | Effect                                                         |
|-----------------|----------------------------------------------------------------|
| `--svg-always`  | Write an SVG even for files without handwriting.               |
| `--no-svg`      | Never write SVGs.                                              |
| `--svg OUT.svg` | Write the page SVG to this path (single input file only).      |
| `--ink-dir DIR` | Also save each handwritten word as its own SVG in `DIR`.       |
| `-v`            | Print the decoded structure (paragraphs, lines, ink objects) instead of plain text. |

Examples:

```bash
# Text to the screen, SVG automatically if there is handwriting
python3 pwi_decode.py note.pwi

# Save the text to a file
python3 pwi_decode.py note.pwi > note.txt

# Every .pwi in the current folder
python3 pwi_decode.py *.pwi

# Every handwritten word as a separate image
python3 pwi_decode.py note.pwi --ink-dir words/
```

The exit status is `0` on success and `1` if any input file could not be
read (missing file, or not a `.pwi` file). The other files are still
processed.

## Usage: `decode_wrapper.sh`

A convenience script that runs the decoder and saves each file's text to
`<file>.txt` next to the original. SVGs for handwritten notes are written
automatically, as described above.

```
./decode_wrapper.sh -d <directory>     # decode all .pwi files in a folder
./decode_wrapper.sh -f <file.pwi>      # decode a single file
./decode_wrapper.sh -h                 # show usage
```

| Option              | Meaning                                                    |
|---------------------|------------------------------------------------------------|
| `-d`, `--directory` | Process every `.pwi` file in this folder (not recursive). Upper- and lower-case `.pwi`/`.PWI` both match; other files are ignored. |
| `-f`, `--file`      | Process this one file.                                     |
| `-h`, `--help`      | Print usage.                                               |

Both `-d` and `-f` can be given in the same call. Run without arguments,
the script prints its usage and exits.

### What you get

For a folder containing `a.pwi` (typed text only) and `b.pwi` (contains
handwriting):

```
notes/
├── a.pwi
├── a.pwi.txt      ← text of a.pwi
├── b.pwi
├── b.pwi.txt      ← text of b.pwi, handwriting shown as [ink]
└── b.svg          ← page image with the handwriting
```

Note the naming: text files keep the `.pwi` in their name (`a.pwi.txt`),
SVGs replace it (`b.svg`).

Re-running is safe: only `.pwi` files are decoded, and existing `.txt`/`.svg`
files are overwritten with fresh output.

Errors:

- If a file can't be decoded (damaged, or not really a `.pwi`), the error is
  printed, no `.txt` is left behind, and the remaining files are still
  processed.
- The exit status is `0` when everything succeeded and `1` if any file
  failed or a given file/folder doesn't exist. That makes it usable from
  other scripts.

### How to use it in the bundle

1. Keep `decode_wrapper.sh` and `pwi_decode.py` in the **same folder**. The
   script finds the decoder next to itself, so it can be started from
   anywhere.
2. Make the script executable once: `chmod +x decode_wrapper.sh`.
3. Point it at your notes:

   ```bash
   /path/to/bundle/decode_wrapper.sh -d ~/PDA-Backup/My\ Documents
   /path/to/bundle/decode_wrapper.sh -f ~/Downloads/note.pwi
   ```

Without the wrapper, the same result for a whole folder:

```bash
for f in notes/*.pwi; do python3 pwi_decode.py "$f" > "$f.txt"; done
```

## Format notes (short)

Full details are in the comment block at the top of `pwi_decode.py`.

- The file starts with `{\pwi` and is a chain of binary records
  (little-endian).
- Paragraphs hold a token stream: plain ASCII characters; non-ASCII
  characters as UTF-8 with the bytes in **reverse order**; and control
  codes for font, size, paragraph end, and "insert handwritten word here".
- Each handwritten word is an ink record made of strokes. A stroke is a start
  point followed by small steps packed as 5-bit signed dx/dy values. Larger
  steps use an escape code followed by a 6-bit extension.
- Coordinates use the same units as the line layout, with y = 0 on the text
  baseline.

## Limitations

Fully verified on the available samples:

- ASCII, German and Cyrillic text.
- Handwriting, including fast strokes that need the escape code.
- Line breaks.

Not yet confirmed (no sample file contained them):

- Bold, italic, underline and text colour. These are reported as unknown
  tokens with `-v` and otherwise skipped.
- Pen colour and thickness. All ink is drawn black at one width.
- Characters beyond the Cyrillic range (e.g. Chinese) and large *positive*
  pen steps. Both are decoded by symmetry with the confirmed cases.
- Embedded voice recordings.
- Drawings and multi-page notes.

The size of typed text relative to the handwriting is an estimate, so text
and ink may be scaled slightly differently in the SVG than on the device.

A `.pwi` that decodes wrongly or reports unknown tokens is the most useful
thing to send for improving the decoder.
