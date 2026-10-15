# Systematics TRILLION MIDI maker

Turn an ordinary MIDI into a gigantic multi-track **black MIDI** — then store
it in a file small enough to share.

A finished result can be tens of millions of notes and many gigabytes of MIDI
data, but because the output is *highly repetitive* it compresses down to a few
megabytes. This app does the multiplying and the compressing in one step.

![what it does](CORE_FILES/assets/document_card.png)

---

## What it does

* **Duplicate tracks.** Tick the track(s) you want and pick a multiplier from
  **1 to 65,535**. Each ticked track is written that many times; unticked
  tracks are kept exactly once. So a "spam" track can be multiplied to the MIDI
  ceiling while the melody stays as it is.
* **Multiply notes instead (single track).** Merge every selected track's notes
  into *one* track repeated `N` times, instead of `N` copies of the track. This
  is how you get past 65,535 tracks' worth of notes in a normal format-1 file.
* **Merge another MIDI into the result.** Load a second `.mid` and its tracks
  are written **once each, after the duplicated part, unduplicated**. The
  classic use: duplicate the *arts* ×65,534, then lay an unduplicated *melody*
  underneath — the multiplier is automatically capped so the melody's tracks
  fit inside the 65,535-track limit.
* **Pick the archive format.** `.xz` (default), `.gz`, `.bz2`, `.zst`, `.zip`
  or `.7z`, at a compression preset from 0 (fastest) to 9 (smallest), with an
  optional second/third compression pass.
* **Fast mode.** Compress one track block and repeat that block (concatenated
  streams or repeated zip entries) — nearly instant, slightly larger output.
* **Memory control.** Normal / double / triple RAM preload, or cap the RAM a
  loaded file may use.
* **Remembers your choices.** Theme, language, multiplier, format, per-file
  track selection and the merge file are all restored on the next launch.

## A worked example

With the sample files in [`test_files/`](test_files/):

1. **File → Open MIDI…** → `test_files/Arts.mid` (1 track, 16 notes).
2. Tick **Merge another MIDI into the output** → **Choose MIDI…** →
   `test_files/Melody.mid` (1 track, 8 notes).
3. Set **Tracks** to `65535` and press **Start**.

The multiplier clamps to **65,534**, giving **65,535 tracks** and
**1,048,552 notes** — the arts duplicated 65,534 times with the melody written
once at the end. See [`test_files/README.md`](test_files/README.md) for more
recipes and the exact expected numbers.

## Running from source

The app uses **only Python's standard library** (tkinter, lzma, bz2, json,
urllib, …). There are no pip packages to install.

1. Install Python 3 with tkinter — run **`Install Dependencies.bat`**, or get
   the full installer from [python.org](https://www.python.org/downloads/) and
   tick *Add python.exe to PATH*. The *embeddable* Windows builds have no
   tkinter and will not work.
2. Run **`Launch Systematics TRILLION MIDI maker.bat`**, or from a terminal:

   ```
   python trillion.py
   ```

## Building the installers

| Script | Produces |
| --- | --- |
| `build_exe.bat` | `dist\Systematics TRILLION MIDI maker.exe` (PyInstaller one-file) |
| `build_msi.bat` | `dist\systematics-v<version>.msi` (WiX setup wizard) |
| `make_portable.bat` | `dist\systematics-v<version>-portable.zip` |

`build_exe.bat` installs PyInstaller automatically if it is missing.
`build_msi.bat` installs the WiX Toolset v7 into `.tools\wix` on first use
(no admin rights needed). All three build from the local files only.

## Layout

```
trillion.py                     launcher
CORE_FILES/
  app.py                        tkinter UI (layout, dialogs, worker wiring)
  midi_engine.py                MIDI parsing, planning and the archive writers
  settings.py                   persistent user preferences (~/.systematics_trillion_midi_maker.json)
  updater.py                    update feed checking + self-update
  lang_loader.py                reads the .SMLF language files
  icons.py                      canvas-drawn icon glyphs
  fast_scan.py                  fast note counting for big files
  Large_MIDI_Loader.py          streamed loader
  Mega_MIDI_Loader.py           memory-mapped loader
  assets/, lang/                icon sets and translations
test_files/                     sample MIDIs + a generator
```

## Languages

Drop a new `Name.SMLF` file into `CORE_FILES/lang/` and it appears in
**Settings → Language**. The file is plain text (`key = value`, with `#`
comments and `[sections]`); `CORE_FILES/lang/Example.SMLF` is a fully annotated
template. `{placeholders}` are filled in at runtime.

## Privacy / network use

**The only thing that uses the network is the update checker**, which fetches a
small `feed.json` from the project's GitHub Pages site. It can be turned off in
**Settings → Check for updates**. Nothing else is transmitted, and nothing about
your files or your machine is collected or sent anywhere.

## License

See [LICENSE](LICENSE). Free to use, copy and distribute unmodified; the
Software may not be modified or redistributed under a different name.
