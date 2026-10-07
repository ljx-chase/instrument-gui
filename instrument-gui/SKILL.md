---
name: instrument-gui
description: Architecture and working patterns for Python/Tkinter applications that drive laboratory instruments and run automated measurement protocols - acquisition threads, live plotting, phase sequencers, session logging, and operator safety for runs that cannot be repeated. Use this whenever the user wants to build, extend, or debug software that controls lab hardware (spectrometer, camera, stage, laser, source meter, temperature controller, pump, shutter, DAQ) or that records a timed measurement protocol, even when they only say "write me a GUI for my instrument", "automate this measurement", or mention live plots, data logging, or a control panel. Also use when an existing instrument GUI stutters, freezes, drops frames, blocks on hardware, or produces data nobody can reproduce afterwards.
---

# Instrument automation GUIs

A measurement application is not a normal app. It owns hardware that can be
slow, can hang, and can damage a sample; it produces data that must still be
interpretable in a year; and it is operated by someone whose hands are busy.
Those three facts drive every pattern here.

## The one rule everything follows

**The Tk thread owns the UI. Worker threads own the hardware. They meet only
at a queue.**

A driver call that blocks for an 8-second exposure, or hangs on a dead USB
port, must never be on the thread that redraws the window - a frozen window
during a live measurement is indistinguishable from a crashed one, and the
operator will reach for the power switch. Equally, a worker thread must never
touch a Tk widget: Tkinter is not thread-safe, and the failure mode is an
intermittent crash hours into a run.

```
  hardware            worker thread                 Tk thread
 ┌────────┐   blocking   ┌──────────┐   queue.Queue  ┌──────────────┐
 │ driver │◄────────────►│ acquire  │───────────────►│ root.after() │──► plots
 │        │   calls      │   loop   │   (data +      │  drain loop  │──► logger
 └────────┘              └──────────┘    status)     └──────────────┘──► widgets
```

Everything else in this skill is a consequence of that diagram plus the
realities of lab work.

## Scope

This skill is for software-timed measurement apps: one instrument or a few
loosely coupled ones, a timed protocol or a simple sweep, sample periods of
milliseconds and up, open-loop acquisition. If the request needs
hardware-clocked acquisition, feedback control, or orchestration of many
instruments with a shared clock, say so and build on a framework (bluesky,
PyMoDAQ, pymeasure) instead of the scaffold; the non-negotiables below still
apply inside those frameworks.

## Start here: scaffold, then fill in

Do not hand-write the wiring. It is the same every time and easy to get subtly
wrong. Generate a working skeleton first, confirm it runs in simulate mode,
then replace the stubs with real hardware calls:

```bash
python scripts/scaffold.py --name pump_probe --instrument LockIn --dest .
python pump_probe_app.py --simulate      # works immediately
python tests/test_pump_probe.py          # headless, passes immediately
```

This writes the entry point, the GUI window, the driver + simulator + worker,
the session logger, the phase machine, and a headless test. Every
hardware-specific line is marked `TODO`. The scaffold targets Tkinter because
it is already on every lab PC; the architecture is toolkit-independent and
`references/qt.md` maps it onto PyQt/PySide when that is the better fit. A free-running measurement is just a
protocol with fewer phases - collapse the ones you do not need rather than
removing the machine, which the window is built around.

Working from a running simulated app means you are always one step from a
thing that works, and the hardware stubs are the only unknown. Starting from a
blank file means the first time anything runs is also the first time the
instrument is involved, and you will not know which half is broken.

**The scaffold is the starting point, not the deliverable.** What it writes is
a generic skeleton whose driver raises `NotImplementedError` and whose
`_derive()` returns a placeholder. A scaffolded app that passes its own tests
proves the wiring works and nothing about the instrument the user actually
named. Before you are finished, the driver must speak that instrument's real
protocol - its SCPI strings, its registers, its DLL calls, its safety
interlocks - and the simulator must model what it really measures. If you
cannot find a command in the device's documentation, say so and leave a marked
stub rather than quietly shipping the placeholder as though it were done.

## Non-negotiables, and why

**Every driver gets a simulator twin.** Same method names, same return types,
plausible physics. This is not a nicety: it is what lets the GUI be developed
without occupying the instrument, lets tests run headlessly in CI, and lets
the operator rehearse a protocol before committing a real sample. Give the
simulator enough physics that the *derived* quantities come out right - if the
app computes absorbance, the simulator should model a lamp and a sample, not
emit a ready-made absorbance curve, or the whole correction chain goes
untested.

**Snapshot the protocol at run start; never read the form during a run.** Write
every setting to `protocol.json` when the run begins, and have the running code
read its parameters from that snapshot. Then disable the widgets that fed it.
A run whose later frames were measured with settings the protocol file does not
mention cannot be reconciled afterwards, and the person doing the reconciling
is usually you, months later.

**Log raw, derive later.** Store what the instrument actually returned, plus
the calibration needed to reduce it, and derive everything else. A dark frame,
a reference spectrum, a gain setting - store them once per session, not per
frame. Reduction pipelines change; raw counts do not.

**Record phase and timing on every sample.** Which phase, seconds since run
start, seconds since each operator mark, and a flag for samples whose
integration window straddled a phase boundary. Analysis always needs to select
"the ones before the change" and "the ones after", and a sample that is half of
each belongs to neither.

**Separate the measurement clock from the wall clock.** The quantity analysis
wants is usually "seconds since the thing happened", not a timestamp. Compute
it at acquisition time from recorded marks and store it as a column.

## Common failure modes

These cost real debugging time. The reference files explain each one.

| Symptom | Cause | Fix |
|---|---|---|
| App stutters, falls behind, eventually freezes | Per-sample work exceeds the drain interval | Profile acquire -> derive -> log -> draw; see `references/data-logging.md` and `references/live-plotting.md` |
| Window frozen during long exposures | Driver called on the Tk thread | Move it to a worker; see `references/architecture.md` |
| Headless test hangs forever in `root.update()` | Same overload, now unbounded | Bounded pump; see `references/testing.md` |
| Compressed saves eat the frame budget | `savez_compressed` on noisy float data | Plain `savez`; see `references/data-logging.md` |
| Heatmap is one flat colour | A few bad pixels own the min/max range | Percentile limits; see `references/live-plotting.md` |
| Derived quantity has huge spikes | Dividing by a near-zero calibration pixel | Propagate NaN; see `references/live-plotting.md` |
| Data can't be reproduced | Settings changed mid-run, or never recorded | Snapshot; see `references/data-logging.md` |

## Read the reference for the part you are building

Each is self-contained; read the one you need rather than all of them.

| File | Covers |
|---|---|
| `references/architecture.md` | Layers, threading, the queue protocol, phase state machines, composing a large window from mixins |
| `references/instrument-drivers.md` | The driver/simulator contract, warm-up samples, reconfiguration mid-run, watchdogs, connection UX |
| `references/data-logging.md` | Session directory layout, file formats, flush cadence, write-cost budget, reproducibility snapshots |
| `references/operator-safety.md` | Irreversible runs, pre-flight checks, freezing inputs, abort vs finish, hands-busy controls, emergency stops |
| `references/live-plotting.md` | Redraw throttling, drawing only what is visible, decimation, colour scaling, NaN handling |
| `references/testing.md` | Headless end-to-end tests, the bounded-pump idiom, simulator-backed validation, screenshot checks |
| `references/look-and-feel.md` | Why Tk apps look dated and the four fixes (sv_ttk, platform fonts, toolbar recolour, spacing), colour rules for operators |
| `references/qt.md` | Building the same architecture in PyQt/PySide: pattern mapping, pyqtgraph, offscreen tests; when Qt is actually warranted |

## Before calling it done

- [ ] No `NotImplementedError` or scaffold `TODO` left in the driver for the
      instrument the user named - every one either implemented against its
      documentation, or explicitly flagged to the user as unresolved.
- [ ] Runs end to end in simulate mode with no hardware attached.
- [ ] Headless test drives a full protocol and asserts the output files.
- [ ] Nothing on the Tk thread can block for longer than one drain interval.
- [ ] A dead or unplugged instrument surfaces as a message, not a hang.
- [ ] `protocol.json` (or equivalent) fully describes the run; inputs frozen
      while recording.
- [ ] Abort keeps the data and marks it aborted, distinguishably from a
      completed run.
- [ ] Measured a frame's worth of work (acquire → derive → log → draw) and it
      fits inside the sample period at the shortest usable setting.
- [ ] A README states the protocol, the file layout and the units, aimed at
      whoever analyses the data.
