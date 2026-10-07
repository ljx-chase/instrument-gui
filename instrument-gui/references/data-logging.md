# Session logging

The test of a logging layout is whether someone who was not in the room can
reduce the data a year later without asking you anything.

## Layout

One directory per run, self-describing:

```
<base>/<prefix>_YYYYmmdd_HHMMSS_<sample>/
    session_info.txt     human-readable header: what, when, settings
    protocol.json        every setting, machine-readable
    calibration.npz      dark/reference/gain - the constants, stored ONCE
    index.csv            one row per sample: the master table
    samples/
        sample_000000_HHMMSS_ffffff.npz
    derived.csv          one row per sample: the scalars you will actually plot
    phase_log.csv        one row per phase: entry, exit, duration, count
    events.csv           operator annotations with timestamps
    run_summary.json     status, marks, durations, counts - written at the end
```

Why both `index.csv` and `derived.csv`: the index maps samples to files and
timings, and stays stable; the derived table holds whatever scalars this
experiment tracks (a fitted peak, a ratio, a temperature) and changes as the
analysis evolves. Keeping them apart means adding a tracked quantity does not
disturb the file that everything else keys on.

`run_summary.json` is written last and carries `status` - `complete` or
`aborted`. Analysis must be able to tell a truncated run from a finished one;
a tail that stops early looks exactly like a plateau otherwise.

## What goes in each sample file

Store what the instrument returned, plus whatever is cheap and useful:

```python
arrays = {
    "wavelength": wl,               # float64, the x axis
    "intensity_raw": raw,           # float64, THE primary record
    "intensity_corrected": corr,    # float32, derived - recomputable
    "timestamp": ts.isoformat(),
    "integration_time": int_time,
    "phase": phase, "t_since_run_start_s": t_run, ...
}
np.savez(path, **arrays)
```

- **Raw stays float64.** It is the record of what happened.
- **Derived arrays can be float32.** They are recomputable, float32 still
  carries ~7 significant digits, and it is a quarter of the per-sample bytes.
- **Never store a quantity that is an exact function of another one you are
  already storing.** Transmittance next to absorbance is a second copy of the
  same numbers.
- **Constants go in `calibration.npz`, once.** A dark frame copied into 18 000
  sample files is half a gigabyte of identical numbers.

Offer a compact mode that stores only the non-recomputable arrays. Say in the
documentation what has to be re-derived and from what.

## The write-cost budget

Per-sample work must fit inside the sample period. Measure it; do not assume.

A concrete measurement worth knowing, on 3648-point float64 spectra:

| | time | size |
|---|---|---|
| `np.savez_compressed` | **26 ms** | 150 KB |
| `np.savez` | **2.6 ms** | 173 KB |

Compression cost ten times the CPU to save 13% of the bytes, because
shot-noise-dominated floating point has almost no redundancy for zlib to find.
Worse, 26 ms exceeds a 50 ms drain interval once a redraw is added, so the
event loop never catches up and the application stalls outright at short
integration times.

**Default to uncompressed for per-sample files.** Compression is for archiving
a finished session, where it runs once and nobody is waiting.

Flush metadata periodically, not per sample:

```python
self.counter += 1
if self.counter % 20 == 0:
    self.flush()
```

An fsync per sample can cost more than the measurement. The sample files
themselves are closed on write, so a crash loses at most the last few metadata
rows - and those are rebuildable from the filenames.

Put the estimated session size in front of the operator *before* the run, with
the free space next to it. Filling a disk two hours into an irreversible
measurement is a bad way to find out.

```python
n = duration / period
estimated = n * BYTES_PER_SAMPLE[mode]       # measured, not guessed
free = shutil.disk_usage(nearest_existing_ancestor(out_dir)).free
```

## Reproducibility

`protocol.json` is written at run start and holds every setting that affects
the data: instrument settings, phase durations, calibration metadata (how many
frames averaged, when), analysis-parameter choices, tracked quantities,
software version if you have one.

If the operator changes something legitimately mid-run - extending a phase, for
instance - rewrite `protocol.json` so the file describes what actually ran.
Everything else stays frozen; see `operator-safety.md`.

## Interoperating with existing analysis

If scripts already read a session layout, keep the column names and array keys
they require, and append new ones rather than renaming. Verify by running the
real loader against a freshly written session in a test:

```python
import existing_analysis
loaded = existing_analysis.load_session(session_dir)
assert loaded["intensities"].shape == (n_samples, n_points)
```

That assertion is worth more than any amount of documentation claiming
compatibility, and it fails the moment someone renames a column.

## Timestamps

Record the **midpoint** of each integration window in the index, not an
endpoint - either end is wrong by half an integration time in a known
direction, and that is the same scale as the phase boundaries you are trying to
resolve.

Store elapsed-seconds columns relative to each meaningful origin (run start,
phase start, each operator mark) alongside the ISO timestamp. Analysis wants
"seconds since the event"; making it re-derive that from timestamps and a
separate log is an invitation to off-by-one errors.
