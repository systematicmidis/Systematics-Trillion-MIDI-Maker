# Merge-feature test MIDIs

Small, hand-made MIDI files for trying the **"Merge another MIDI into the output"**
option on the Convert page. Regenerate them at any time with:

```
python test_files/make_test_midis.py
```

## The files

| File | Tracks | Notes | Division | What it's for |
|---|---|---|---|---|
| `Arts.mid` | 1 ("Arts") | 16 | 480 ppqn | The part you **duplicate** (your "arts") |
| `Melody.mid` | 1 ("Melody") | 8 | 480 ppqn | The part you **merge** once, unduplicated |
| `Merge_2tracks.mid` | 2 ("Chords", "Bass") | 8 + 4 | 480 ppqn | Merge target with two tracks — both should land once each |
| `Melody_960ppqn.mid` | 1 ("Melody") | 8 | **960 ppqn** | Should be **refused**: merge needs the same division |

All four are plain format-1 MIDI, so they also open in any MIDI player (the arts is a
little 16th-note scale, the melody is 8 quarter notes — easy to hear which is which).

## Try it

1. **File → Open MIDI…** → `test_files/Arts.mid`
2. Tick **Merge another MIDI into the output** → **Choose MIDI…** → `test_files/Melody.mid`
3. Tracks: **65535**, then **Start**.

### Expected results

| Run | Multiplier you type | What it builds | Notes in the finish dialog |
|---|---|---|---|
| Tracks + 1-track merge | 65535 | Multiplier auto-clamped to **65534**, total **65535 tracks** (65534 arts + 1 melody) | **1,048,552** (65534 × 16 + 8) |
| Tracks + 2-track merge | 100 | **102 tracks** (100 arts + Chords + Bass) | **1,612** (100 × 16 + 12) |
| Multiply notes + 1-track merge | 3 | **1 track**; melody notes written once | **56** (3 × 16 + 8) |
| Multiply notes + 2-track merge | 1000 | **1 track**; all 12 merge notes written once | **16,012** (1000 × 16 + 12) |

### Also worth checking

- **`Melody_960ppqn.mid`** as the merge file → the run stops with a clear error saying
  the divisions differ (960 vs 480). This is intentional: a format-1 file has one
  division for every track, so a 960-ppqn track inside a 480-ppqn file would play at
  half speed.
- **Uncheck** the merge box again and the output goes back to arts-only
  (a clean multiple of 16 notes per copy) — nothing about the merge option is sticky
  in the file itself.
- The merged part is a **parallel track**: it plays *alongside* the duplicated arts
  (it's written last, so it sits underneath in the track stack), it does not start
  after the arts finish.

## Files can be editted

`make_test_midis.py` is a plain ~100-line script with no dependencies. Change the
`arts` / `melody` note lists near the bottom of `main()` if you want a different
number of notes (handy for checking the note-count math in the finish dialog) and
re-run it.
