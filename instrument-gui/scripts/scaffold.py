#!/usr/bin/env python3
"""
Generate a runnable skeleton for an instrument-automation GUI.

    python scaffold.py --name pump_probe --instrument LockIn --dest .
    python pump_probe_app.py --simulate      # works immediately
    python tests/test_pump_probe.py          # passes immediately

What you get: entry point, GUI window (status header, control column, live
plot, trend plot, drain loop), driver + simulator + acquisition worker,
session logger, phase state machine, and a headless end-to-end test. Every
hardware-specific line is marked TODO.

For a free-running monitor with no protocol, generate as normal and simply
collapse the phase machine to the phases you need - the window is built around
it, so removing it entirely means a different window.

Nothing is overwritten unless --force is given. Files that already exist in the
destination (gui/theme.py, gui/widgets.py) are left alone, so this is safe to
run inside an existing project.
"""

import argparse
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Templates. Tokens: @@NAME@@ @@TITLE@@ @@INSTR@@ @@INSTR_MOD@@ @@PROTO_IMPORT@@
# Written with r''' quoting so the generated code can use """ docstrings.
# --------------------------------------------------------------------------

T_INSTRUMENT = r'''"""
@@INSTR@@ driver, simulator, and acquisition worker.

The driver and the simulator are duck-typed twins: same methods, same return
types. The GUI picks between them once, at connect time, and never mentions
the difference again. That is what lets the whole application be developed and
tested with no hardware attached.

Replace every TODO with real calls. Keep vendor specifics (ctypes handles,
SCPI strings, status bits) inside the driver - the worker and the GUI must not
need to know how the device talks.
"""

import threading
import time
from dataclasses import dataclass
from datetime import datetime

import numpy as np

# TODO: replace with the real device constants, and cite the source
#       (datasheet page / programming manual section) for each one.
DEFAULT_RESOURCE = "TODO::resource::string"
# EXAMPLE data shape: one 512-point trace per sample, like a spectrometer.
# This is a placeholder regardless of the --instrument name you passed. A
# lock-in or a source meter returns a few scalars, a camera returns an image:
# change Sample, read() and the simulator to match what the device returns.
N_POINTS = 512
X_MIN, X_MAX = 400.0, 800.0
INT_TIME_MIN_S = 1e-3
INT_TIME_MAX_S = 10.0
MIN_PERIOD_S = 0.005          # fastest sustainable sample-to-sample period


@dataclass
class Sample:
    """One acquisition.

    ts_start/ts_end bracket the actual integration window rather than giving a
    single post-hoc timestamp. A sample whose window straddles a phase
    boundary is then detectable, and the midpoint is the honest timestamp.
    """
    ts_start: datetime
    ts_end: datetime
    x: np.ndarray
    y: np.ndarray


class @@INSTR@@Driver:
    """Real hardware."""

    def __init__(self, resource=DEFAULT_RESOURCE):
        self.resource = resource
        self._connected = False
        self._int_time = 0.1

    def connect(self):
        if self._connected:
            return
        # TODO: open the device. Raise with a useful message on failure -
        #       this string is what the operator sees in the dialog.
        raise NotImplementedError(
            "@@INSTR@@Driver.connect: wire up the real device, or tick "
            "'Simulate' to run without hardware.")

    def configure(self, integration_time=None):
        """Push settings to the device. Called only when a value changes."""
        if integration_time is not None:
            self._int_time = float(integration_time)
            # TODO: send the setting to the device.

    def read(self, integration_time=None):
        """Block until one sample is ready; return (x, y) arrays.

        TODO: poll the device status with a DEADLINE and raise on expiry. A
        read that waits forever on a wedged device hangs the worker, and the
        only visible symptom is that the plot stopped updating.
        """
        raise NotImplementedError("@@INSTR@@Driver.read")

    def close(self):
        """Disconnect, leaving the hardware in a SAFE state.

        TODO: if this device has an output that heats, energises or moves,
        switch it off HERE, before disconnecting. Quitting the app must not be
        able to leave a sample energised.
        """
        self._connected = False


class Simulated@@INSTR@@:
    """Software stand-in with enough physics to exercise the real code path.

    A simulator that returns a canned array tests only plumbing. This one
    models the measurement chain, so whatever the application derives from the
    raw signal - background subtraction, normalisation, a fitted peak - is
    genuinely exercised, including its edge cases.

    event_clock: callable returning seconds since the operator's mark (or
    None before it). The simulated sample responds to the protocol, so a
    rehearsal in simulate mode shows a real trace instead of a flat line.
    """

    #: What the marked event does to the sample, and how fast.
    PEAK_CENTER = 525.0
    PEAK_WIDTH = 26.0
    PEAK_AMPLITUDE = 0.40
    EVENT_SHIFT = 11.0
    EVENT_TAU_S = 240.0

    def __init__(self, event_clock=None, *args, **kwargs):
        self._connected = False
        self._int_time = 0.1
        self._x = np.linspace(X_MIN, X_MAX, N_POINTS)
        self._rng = np.random.default_rng(20260101)   # seeded: reproducible
        self._event_clock = event_clock
        self.simulate_error = False     # flip to exercise the error path

    def connect(self):
        self._connected = True

    def configure(self, integration_time=None):
        if integration_time is not None:
            self._int_time = float(integration_time)

    def read(self, integration_time=None):
        if self.simulate_error:
            raise RuntimeError("Simulated @@INSTR@@ failure")
        t_exp = integration_time if integration_time is not None else self._int_time
        time.sleep(min(t_exp, 0.15))       # capped so tests stay fast

        progress = 0.0
        if self._event_clock is not None:
            try:
                t_event = self._event_clock()
            except Exception:
                t_event = None
            if t_event is not None and t_event > 0:
                progress = 1.0 - np.exp(-t_event / self.EVENT_TAU_S)

        center = self.PEAK_CENTER + self.EVENT_SHIFT * progress
        half = 0.5 * self.PEAK_WIDTH
        signal = self.PEAK_AMPLITUDE * half ** 2 / ((self._x - center) ** 2 + half ** 2)
        background = 0.05 * (500.0 / np.clip(self._x, 250.0, None)) ** 4
        y = (signal + background) * (t_exp / 0.1) * 1000.0 + 200.0
        y = y + self._rng.normal(0.0, np.sqrt(np.clip(y, 1.0, None)) * 0.5)
        return self._x.copy(), y

    def close(self):
        self._connected = False


class @@INSTR@@Worker(threading.Thread):
    """Owns the driver, blocks freely, never touches Tk.

    params is a plain dict the GUI mutates live; it is re-read each iteration
    so a settings change takes effect on the next sample without restarting
    the thread.
    """

    def __init__(self, driver, params, data_queue, status_queue):
        super().__init__(daemon=True)
        self.driver = driver
        self.params = params                 # {"int_time": s, "interval": s}
        self.data_queue = data_queue
        self.status_queue = status_queue
        self._stop_event = threading.Event()
        self._last_int_time = None
        self.last_sample_ts = time.time()    # for an external watchdog

    def stop(self):
        self._stop_event.set()

    def run(self):
        first_sample = True
        try:
            while not self._stop_event.is_set():
                int_time = self.params["int_time"]
                if int_time != self._last_int_time:
                    self.driver.configure(integration_time=int_time)
                    self._last_int_time = int_time
                    # Most instruments return one stale or mis-exposed sample
                    # after a settings change. Drop it rather than let it into
                    # the data.
                    first_sample = True

                t_start = time.time()
                ts_start = datetime.now()
                try:
                    x, y = self.driver.read(integration_time=int_time)
                except Exception as exc:
                    self.status_queue.put(("error", f"Acquisition failed: {exc}"))
                    break
                ts_end = datetime.now()
                self.last_sample_ts = time.time()

                if first_sample:
                    first_sample = False
                    self.status_queue.put(("info", "Discarded warm-up sample."))
                    continue

                self.data_queue.put(Sample(ts_start, ts_end, x, y))

                period = max(int_time + self.params["interval"], MIN_PERIOD_S)
                remaining = period - (time.time() - t_start)
                while remaining > 0 and not self._stop_event.is_set():
                    chunk = min(remaining, 0.05)   # chunked: stop() stays prompt
                    time.sleep(chunk)
                    remaining -= chunk
        finally:
            self.status_queue.put(("stopped", None))
'''


T_PROTOCOL = r'''"""
Phase state machine for one @@TITLE@@ run.

    BASELINE --> EVENT --> RELAXATION --> COMPLETE
             ^         ^
         operator   operator

No Tk, no hardware, no thread: nothing here commands an instrument, so there
is nothing to time out and nothing that must keep running if the UI is busy.
The GUI ticks it from the same loop that drains the sample queue. (A machine
that DOES command hardware at phase boundaries needs its own thread, so it can
shut that hardware down even if the UI wedges.)

The clock is injectable, so tests step it instead of sleeping.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum


class Phase(Enum):
    IDLE = "idle"
    BASELINE = "baseline"
    EVENT = "event"
    RELAXATION = "relaxation"
    COMPLETE = "complete"
    ABORTED = "aborted"


ACTIVE_PHASES = (Phase.BASELINE, Phase.EVENT, Phase.RELAXATION)
TERMINAL_PHASES = (Phase.COMPLETE, Phase.ABORTED)


@dataclass
class Protocol:
    """Everything that defines a run. Snapshotted to protocol.json at start.

    relaxation_duration_s = None means "record until I press Finish".
    """
    sample_name: str = ""
    notes: str = ""
    baseline_min_duration_s: float = 60.0
    relaxation_duration_s: float = 600.0
    integration_time_s: float = 0.1
    interval_s: float = 0.0
    tracked_x: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class EventMark:
    ts: datetime
    label: str
    phase: str
    t_since_run_start_s: float


class Run:
    def __init__(self, protocol, now=None):
        self.protocol = protocol
        self.phase = Phase.IDLE
        self.start_ts = None
        self.end_ts = None
        self.phase_entry_ts = None
        self.event_start_ts = None
        self.event_end_ts = None
        self.abort_reason = ""
        self.events = []
        # (phase_value, entry_ts) in order. classify_sample searches this
        # rather than trusting self.phase, which is "now" and not "when that
        # integration window happened".
        self.transitions = []
        self._counts = {}
        self._now = now or datetime.now

    # ---- transitions. Each returns events for the GUI to render. ----
    def _enter(self, phase, ts):
        self.phase = phase
        self.phase_entry_ts = ts
        self.transitions.append((phase.value, ts))

    def start(self, ts=None):
        if self.phase is not Phase.IDLE:
            return []
        ts = ts or self._now()
        self.start_ts = ts
        self._enter(Phase.BASELINE, ts)
        return [("phase_entered", (Phase.BASELINE.value, ts))]

    def begin_event(self, ts=None):
        """Operator marks the start of the intervention."""
        if self.phase is not Phase.BASELINE:
            return []                       # refuse, do not reorder
        ts = ts or self._now()
        self.event_start_ts = ts
        self._enter(Phase.EVENT, ts)
        return [("phase_entered", (Phase.EVENT.value, ts)), ("event_start", ts)]

    def end_event(self, ts=None):
        """Operator marks the end; the relaxation clock starts here."""
        if self.phase is not Phase.EVENT:
            return []
        ts = ts or self._now()
        self.event_end_ts = ts
        self._enter(Phase.RELAXATION, ts)
        return [("phase_entered", (Phase.RELAXATION.value, ts)), ("event_end", ts)]

    def finish(self, ts=None, reason="relaxation_complete"):
        if self.phase in TERMINAL_PHASES or self.phase is Phase.IDLE:
            return []
        ts = ts or self._now()
        self.end_ts = ts
        self._enter(Phase.COMPLETE, ts)
        return [("phase_entered", (Phase.COMPLETE.value, ts)),
                ("run_finished", reason)]

    def abort(self, reason="operator_abort", ts=None):
        """Stop early, keeping the data. The distinction from finish() is
        recorded rather than smoothed over: an aborted run has a truncated
        curve, and status='aborted' is what stops analysis reading the missing
        tail as a plateau."""
        if self.phase in TERMINAL_PHASES or self.phase is Phase.IDLE:
            return []
        ts = ts or self._now()
        self.end_ts = ts
        self.abort_reason = reason
        self._enter(Phase.ABORTED, ts)
        return [("phase_entered", (Phase.ABORTED.value, ts)),
                ("run_aborted", reason)]

    def mark_event(self, label, ts=None):
        """Timestamp a free-text annotation without changing phase."""
        ts = ts or self._now()
        mark = EventMark(ts, label, self.phase.value,
                         (ts - self.start_ts).total_seconds()
                         if self.start_ts else 0.0)
        self.events.append(mark)
        return [("event_marked", mark)]

    def tick(self, now=None):
        """The only time-driven transition there is."""
        now = now or self._now()
        if (self.phase is Phase.RELAXATION
                and self.protocol.relaxation_duration_s is not None
                and self.phase_elapsed_s(now) >= self.protocol.relaxation_duration_s):
            return self.finish(now)
        return []

    # ---- queries ----
    def is_running(self):
        return self.phase in ACTIVE_PHASES

    def elapsed_s(self, now=None):
        if self.start_ts is None:
            return 0.0
        return ((self.end_ts or now or self._now()) - self.start_ts).total_seconds()

    def phase_elapsed_s(self, now=None):
        if self.phase_entry_ts is None:
            return 0.0
        return ((now or self._now()) - self.phase_entry_ts).total_seconds()

    def baseline_minimum_satisfied(self, now=None):
        if self.phase is not Phase.BASELINE:
            return True
        return self.phase_elapsed_s(now) >= self.protocol.baseline_min_duration_s

    def remaining_s(self, now=None):
        """Seconds left in this phase, or None when it waits on the operator."""
        now = now or self._now()
        if self.phase is Phase.BASELINE:
            return max(0.0, self.protocol.baseline_min_duration_s
                       - self.phase_elapsed_s(now))
        if (self.phase is Phase.RELAXATION
                and self.protocol.relaxation_duration_s is not None):
            return max(0.0, self.protocol.relaxation_duration_s
                       - self.phase_elapsed_s(now))
        return None

    def t_since_event_s(self, ts):
        if self.event_start_ts is None:
            return None
        return (ts - self.event_start_ts).total_seconds()

    # ---- sample classification ----
    def _phase_at(self, ts):
        best = (None, None)
        for phase_value, entry_ts in self.transitions:
            if entry_ts <= ts:
                best = (phase_value, entry_ts)
            else:
                break
        return best

    def classify_sample(self, sample):
        """Fields describing where in the run this sample sits.

        phase is the phase at the START of the integration window;
        boundary_contaminated says it ended in a different one - that sample is
        part of each and belongs to neither.
        """
        phase_value, entry_ts = self._phase_at(sample.ts_start)
        end_phase, _ = self._phase_at(sample.ts_end)
        if phase_value is None:
            return {}
        self._counts[phase_value] = self._counts.get(phase_value, 0) + 1
        return {
            "phase": phase_value,
            "t_since_run_start_s": ((sample.ts_start - self.start_ts).total_seconds()
                                    if self.start_ts else None),
            "t_since_phase_start_s": ((sample.ts_start - entry_ts).total_seconds()
                                      if entry_ts else None),
            "t_since_event_s": self.t_since_event_s(sample.ts_start),
            "boundary_contaminated": bool(end_phase != phase_value),
        }

    # ---- summary ----
    def phase_rows(self):
        rows = []
        for i, (phase_value, entry_ts) in enumerate(self.transitions):
            if phase_value in (Phase.COMPLETE.value, Phase.ABORTED.value):
                continue
            exit_ts = (self.transitions[i + 1][1]
                       if i + 1 < len(self.transitions) else self.end_ts)
            rows.append({"phase": phase_value, "entry_ts": entry_ts,
                         "exit_ts": exit_ts,
                         "duration_s": ((exit_ts - entry_ts).total_seconds()
                                        if exit_ts else None),
                         "n_samples": self._counts.get(phase_value, 0)})
        return rows

    def summary(self):
        def span(a, b):
            return (b - a).total_seconds() if (a and b) else None
        return {"status": self.phase.value,
                "abort_reason": self.abort_reason,
                "start_ts": self.start_ts, "end_ts": self.end_ts,
                "event_start_ts": self.event_start_ts,
                "event_end_ts": self.event_end_ts,
                "baseline_duration_s": span(self.start_ts, self.event_start_ts),
                "event_duration_s": span(self.event_start_ts, self.event_end_ts),
                "relaxation_duration_s": span(self.event_end_ts, self.end_ts),
                "total_duration_s": span(self.start_ts, self.end_ts),
                "samples_per_phase": dict(self._counts),
                "n_events": len(self.events)}
'''


T_LOGGING = r'''"""
On-disk layout for one @@TITLE@@ run.

    <base>/@@NAME@@_session_YYYYmmdd_HHMMSS_<sample>/
        session_info.txt    human-readable header
        protocol.json       every setting, for reproducibility
        calibration.npz     constants (background, reference, gain) stored ONCE
        index.csv           one row per sample: the master table
        samples/*.npz       the raw arrays
        derived.csv         one row per sample: the scalars you will plot
        phase_log.csv       one row per phase
        events.csv          operator annotations
        run_summary.json    status, marks, durations - written at the end

The test of this layout is whether someone who was not in the room can reduce
the data a year later without asking you anything.
"""

import json
from datetime import datetime
from pathlib import Path

import numpy as np

#: 'full' keeps the derived arrays too; 'compact' keeps only what cannot be
#: recomputed from calibration.npz.
STORAGE_MODES = ("full", "compact")

#: Measured bytes per sample, for the pre-flight disk estimate. Uncompressed,
#: so these are stable rather than data-dependent. Re-measure if the arrays
#: change size.
BYTES_PER_SAMPLE = {"full": 10_000, "compact": 6_000}


def _iso(ts):
    return "" if ts is None else ts.isoformat()


def _num(v, fmt="{:.6g}"):
    if v is None:
        return ""
    try:
        if isinstance(v, float) and v != v:        # NaN
            return ""
        return fmt.format(v)
    except (TypeError, ValueError):
        return ""


class SessionWriter:
    INDEX_COLUMNS = ("index", "timestamp_iso", "filename", "integration_time_s",
                     "phase", "t_since_run_start_s", "t_since_phase_start_s",
                     "t_since_event_s", "boundary_contaminated")
    PHASE_COLUMNS = ("phase", "entry_ts", "exit_ts", "duration_s", "n_samples")
    EVENT_COLUMNS = ("timestamp_iso", "t_since_run_start_s", "phase", "label")

    def __init__(self, base_dir, storage_mode="full"):
        self.base_dir = Path(base_dir)
        self.storage_mode = storage_mode if storage_mode in STORAGE_MODES else "full"
        self.session_dir = None
        self.samples_dir = None
        self.counter = 0
        self.bytes_written = 0
        self._enabled = False
        self._index_file = None
        self._derived_file = None
        self._events_file = None

    def start(self, protocol, x_axis, calibration=None, session_name=None):
        ts = datetime.now()
        safe = "".join(c if (c.isalnum() or c in "-_") else "_"
                       for c in protocol.sample_name).strip("_")
        name = session_name or (f"@@NAME@@_session_{ts.strftime('%Y%m%d_%H%M%S')}"
                                + (f"_{safe}" if safe else ""))
        self.session_dir = self.base_dir / name
        self.samples_dir = self.session_dir / "samples"
        self.samples_dir.mkdir(parents=True, exist_ok=True)

        with open(self.session_dir / "session_info.txt", "w", encoding="utf-8") as f:
            f.write("@@TITLE@@ session\n")
            f.write(f"Started      : {ts.isoformat()}\n")
            f.write(f"Sample       : {protocol.sample_name}\n")
            f.write(f"Int. time    : {protocol.integration_time_s:.6g} s\n")
            f.write(f"Interval     : {protocol.interval_s:.6g} s\n")
            f.write(f"Storage mode : {self.storage_mode}\n")
            if protocol.notes:
                f.write(f"Notes        : {protocol.notes}\n")

        with open(self.session_dir / "protocol.json", "w", encoding="utf-8") as f:
            json.dump(protocol.to_dict(), f, indent=2, default=str)

        # Constants once per session, not per sample: a background copied into
        # 18000 sample files is a gigabyte of identical numbers.
        cal = {"x": np.asarray(x_axis, dtype=np.float64)}
        for key, val in (calibration or {}).items():
            if val is not None:
                cal[key] = np.asarray(val, dtype=np.float64)
        np.savez_compressed(self.session_dir / "calibration.npz", **cal)

        self._index_file = open(self.session_dir / "index.csv", "w", encoding="utf-8")
        self._index_file.write(",".join(self.INDEX_COLUMNS) + "\n")
        self._events_file = open(self.session_dir / "events.csv", "w", encoding="utf-8")
        self._events_file.write(",".join(self.EVENT_COLUMNS) + "\n")

        cols = ["index", "timestamp_iso", "t_since_run_start_s", "t_since_event_s",
                "phase", "boundary_contaminated", "value"]
        cols += [f"x_{v:g}" for v in protocol.tracked_x]
        self._derived_file = open(self.session_dir / "derived.csv", "w",
                                  encoding="utf-8")
        self._derived_file.write(",".join(cols) + "\n")
        self.flush()

        self.counter = 0
        self.bytes_written = 0
        self._enabled = True
        return self.session_dir

    def is_active(self):
        return self._enabled

    def log_sample(self, ts, x, y, integration_time_s, y_derived=None,
                   value=None, tracked_values=None, phase=None,
                   t_since_run_start_s=None, t_since_phase_start_s=None,
                   t_since_event_s=None, boundary_contaminated=None):
        if not self._enabled:
            return None
        fname = f"sample_{self.counter:06d}_{ts.strftime('%H%M%S_%f')}.npz"
        fpath = self.samples_dir / fname

        arrays = {"x": x, "y_raw": y, "timestamp": ts.isoformat(),
                  "integration_time": integration_time_s}
        if self.storage_mode == "full" and y_derived is not None:
            # float32 for recomputable arrays: ~7 significant digits against an
            # instrument good for 4, at a quarter of the bytes.
            arrays["y_derived"] = np.asarray(y_derived, dtype=np.float32)
        if phase is not None:
            arrays["phase"] = phase

        # savez, NOT savez_compressed. Measured on real array sizes,
        # compression cost 26 ms per sample against 2.6 ms and saved 13% of the
        # bytes - noisy floating point has nothing for zlib to find. At 26 ms a
        # sample the event loop never catches up and the app stalls.
        np.savez(fpath, **arrays)
        try:
            self.bytes_written += fpath.stat().st_size
        except OSError:
            pass

        self._index_file.write(",".join([
            str(self.counter), ts.isoformat(), fname, f"{integration_time_s:.6g}",
            phase or "", _num(t_since_run_start_s, "{:.4f}"),
            _num(t_since_phase_start_s, "{:.4f}"), _num(t_since_event_s, "{:.4f}"),
            "" if boundary_contaminated is None else str(int(bool(boundary_contaminated))),
        ]) + "\n")

        row = [str(self.counter), ts.isoformat(),
               _num(t_since_run_start_s, "{:.4f}"), _num(t_since_event_s, "{:.4f}"),
               phase or "",
               "" if boundary_contaminated is None else str(int(bool(boundary_contaminated))),
               _num(value)]
        row += [_num(v) for v in (tracked_values or [])]
        self._derived_file.write(",".join(row) + "\n")

        # Flushed every 20 samples, not every one: an fsync per sample can cost
        # more than the measurement. The npz files are closed on write, so a
        # crash loses at most the last few metadata rows.
        self.counter += 1
        if self.counter % 20 == 0:
            self.flush()
        return fname

    def flush(self):
        for f in (self._index_file, self._derived_file, self._events_file):
            if f is not None:
                try:
                    f.flush()
                except Exception:
                    pass

    def log_event(self, mark):
        if not self._enabled or self._events_file is None:
            return
        label = str(mark.label).replace('"', "'")
        if ("," in label) or ("\n" in label):
            label = f'"{label}"'
        self._events_file.write(f"{_iso(mark.ts)},{mark.t_since_run_start_s:.4f},"
                                f"{mark.phase},{label}\n")
        self._events_file.flush()

    def write_phase_log(self, rows):
        if self.session_dir is None:
            return
        with open(self.session_dir / "phase_log.csv", "w", encoding="utf-8") as f:
            f.write(",".join(self.PHASE_COLUMNS) + "\n")
            for r in rows:
                f.write(",".join([r["phase"], _iso(r["entry_ts"]), _iso(r["exit_ts"]),
                                  _num(r["duration_s"], "{:.4f}"),
                                  str(r["n_samples"])]) + "\n")

    def write_run_summary(self, summary):
        if self.session_dir is None:
            return
        payload = dict(summary)
        payload.update(n_samples=self.counter, bytes_written=self.bytes_written,
                       storage_mode=self.storage_mode)
        with open(self.session_dir / "run_summary.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, default=str)

    def stop(self):
        self._enabled = False
        for attr in ("_index_file", "_derived_file", "_events_file"):
            f = getattr(self, attr)
            if f is not None:
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
                setattr(self, attr, None)
'''


T_WINDOW = r'''"""
@@TITLE@@ - main window.

All Tk lives here. No hardware calls, no physics: the window drains queues,
renders, and asks the protocol machine what to do. Anything you would want to
test without a display belongs in @@NAME@@_protocol.py, @@NAME@@_logging.py,
or a maths module.
"""

import json
import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

from instruments.@@INSTR_MOD@@ import (
    @@INSTR@@Driver, Simulated@@INSTR@@, @@INSTR@@Worker,
    DEFAULT_RESOURCE, X_MIN, X_MAX, INT_TIME_MIN_S, INT_TIME_MAX_S, MIN_PERIOD_S,
)
from @@NAME@@_logging import SessionWriter, STORAGE_MODES, BYTES_PER_SAMPLE
@@PROTO_IMPORT@@
from gui.theme import (apply_ttk_theme, apply_mpl_theme, BG_PANEL, BG_PANEL_ALT,
                       BG_HEADER, BG_INPUT, FG_PRIMARY, FG_SECONDARY, FG_DISABLED,
                       ACCENT_BLUE, ACCENT_GREEN, ACCENT_AMBER, ACCENT_RED,
                       DANGER_BG, DANGER_FG, MONO_FONT, MONO_FONT_LARGE,
                       MONO_FONT_MED, UI_FONT, UI_FONT_BOLD)
from gui.widgets import CollapsibleSection, ScrollableFrame, status_dot, style_mpl_toolbar

SETTINGS_FILE = Path(__file__).resolve().parent.parent / "@@NAME@@_settings.json"

_PERSISTED = ("resource_var", "int_time_var", "interval_var", "out_dir_var",
              "sample_var", "notes_var", "baseline_var", "relax_var",
              "storage_var")

_PHASE_STYLE = {Phase.IDLE: ("IDLE", FG_SECONDARY),
                Phase.BASELINE: ("BASELINE", ACCENT_BLUE),
                Phase.EVENT: ("EVENT", ACCENT_AMBER),
                Phase.RELAXATION: ("RELAXATION", ACCENT_GREEN),
                Phase.COMPLETE: ("COMPLETE", ACCENT_GREEN),
                Phase.ABORTED: ("ABORTED", ACCENT_RED)}


def _fmt_hms(seconds):
    if seconds is None:
        return "--:--"
    m, s = divmod(int(round(max(0.0, float(seconds)))), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fmt_bytes(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


class @@CLASS@@:
    POLL_INTERVAL_MS = 50      # drain the queues
    PLOT_INTERVAL_MS = 250     # redraw - deliberately slower, drawing is dear
    TREND_MAX_POINTS = 3000    # curves are decimated to this for display

    def __init__(self, root, simulate=False):
        self.root = root
        self.root.title("@@TITLE@@")
        self.root.geometry("1360x900")
        self.root.minsize(1100, 700)
        apply_ttk_theme(self.root)
        apply_mpl_theme()

        self.driver = None
        self.worker = None
        self.data_queue = queue.Queue()
        self.status_queue = queue.Queue()
        self.params = {"int_time": 0.1, "interval": 0.0}
        self.last_sample = None

        self.run = None
        self.writer = None
        self.session_dir = None
        self.sample_count = 0
        self.t0 = None

        self.trend_t = []
        self.trend_v = []
        self._marker_artists = []
        self._marker_row = 0
        self._run_frozen_widgets = []

        self._closing = False
        self._after_id = None
        self._last_plot_t = 0.0

        self._build_ui()
        if simulate:
            self.simulate_var.set(True)
        try:
            self._restore_settings()
        except Exception:
            pass
        self.root.bind("<F2>", lambda e: self._on_primary_action())
        self.root.bind("<F4>", lambda e: self._on_mark_event())
        self._refresh_controls()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._after_id = self.root.after(self.POLL_INTERVAL_MS, self._drain_queues)

    # ==================================================================
    # UI
    # ==================================================================
    def _freeze(self, widget, enabled_state=tk.NORMAL):
        """Register a widget to be disabled while recording, and return it so
        it can be laid out in the same expression. Settings that go into
        protocol.json must not change underneath a running run."""
        self._run_frozen_widgets.append((widget, enabled_state))
        return widget

    def _build_ui(self):
        self._build_header()
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        scroll = ScrollableFrame(paned)
        paned.add(scroll, weight=1)
        ctrl = scroll.interior
        plots = ttk.Frame(paned)
        paned.add(plots, weight=3)

        sect = CollapsibleSection(ctrl, "INSTRUMENT", expanded=True)
        sect.pack(side=tk.TOP, fill=tk.X)
        self._build_instrument_panel(sect.body)
        sect = CollapsibleSection(ctrl, "ACQUISITION", expanded=True)
        sect.pack(side=tk.TOP, fill=tk.X)
        self._build_acquisition_panel(sect.body)
        sect = CollapsibleSection(ctrl, "RUN", expanded=True)
        sect.pack(side=tk.TOP, fill=tk.X)
        self._build_run_panel(sect.body)

        self.notebook = ttk.Notebook(plots)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self._build_live_tab()
        self._build_trend_tab()
        # Only the visible tab is redrawn; switching re-renders from buffers.
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        bar = ttk.Frame(self.root, padding=2)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(
            value="Not connected. Tick 'Simulate' to run without hardware.")
        ttk.Label(bar, textvariable=self.status_var,
                  style="Secondary.TLabel").pack(side=tk.LEFT)
        self.count_var = tk.StringVar(value="0 samples")
        ttk.Label(bar, textvariable=self.count_var,
                  style="Secondary.TLabel").pack(side=tk.RIGHT)

    def _build_header(self):
        """Phase, clocks, and the one button the operator presses. Packed at
        the top of root, outside the paned area, so no amount of scrolling can
        push it out of sight."""
        head = tk.Frame(self.root, bg=BG_HEADER)
        head.pack(side=tk.TOP, fill=tk.X)
        left = tk.Frame(head, bg=BG_HEADER)
        left.pack(side=tk.LEFT, padx=10, pady=8)
        self.phase_badge = tk.Label(left, text="IDLE", bg=BG_HEADER,
                                    fg=FG_SECONDARY, font=MONO_FONT_LARGE)
        self.phase_badge.pack(side=tk.TOP, anchor="w")
        self.phase_detail_var = tk.StringVar(value="No run started.")
        tk.Label(left, textvariable=self.phase_detail_var, bg=BG_HEADER,
                 fg=FG_SECONDARY, font=UI_FONT, anchor="w").pack(side=tk.TOP,
                                                                 anchor="w")
        clocks = tk.Frame(head, bg=BG_HEADER)
        clocks.pack(side=tk.LEFT, padx=24, pady=8)
        self.clock_vars = {}
        for key, label in (("elapsed", "run elapsed"), ("phase", "in phase"),
                           ("remaining", "remaining"), ("event", "since event")):
            cell = tk.Frame(clocks, bg=BG_HEADER)
            cell.pack(side=tk.LEFT, padx=10)
            var = tk.StringVar(value="--:--")
            tk.Label(cell, textvariable=var, bg=BG_HEADER, fg=FG_PRIMARY,
                     font=MONO_FONT_MED).pack(side=tk.TOP)
            tk.Label(cell, text=label, bg=BG_HEADER, fg=FG_DISABLED,
                     font=("Segoe UI", 8)).pack(side=tk.TOP)
            self.clock_vars[key] = var

        actions = tk.Frame(head, bg=BG_HEADER)
        actions.pack(side=tk.RIGHT, padx=10, pady=6)
        self.abort_btn = tk.Button(actions, text="ABORT RUN", bg=DANGER_BG,
                                   fg=DANGER_FG, font=UI_FONT_BOLD, relief="flat",
                                   padx=10, pady=8, command=self._on_abort_run,
                                   state=tk.DISABLED)
        self.abort_btn.pack(side=tk.RIGHT, padx=(8, 0))
        self.event_btn = tk.Button(actions, text="MARK EVENT  (F4)", bg=BG_INPUT,
                                   fg=FG_PRIMARY, font=UI_FONT_BOLD, relief="flat",
                                   padx=10, pady=8, command=self._on_mark_event,
                                   state=tk.DISABLED)
        self.event_btn.pack(side=tk.RIGHT, padx=(8, 0))
        # One button that always performs the next step of the protocol, so the
        # operator never has to find the right one mid-intervention. The two
        # phase marks are deliberately NOT behind a confirmation dialog: their
        # whole point is a timestamp, and a dialog puts its latency into it.
        self.primary_btn = tk.Button(actions, text="- run not started -",
                                     bg=BG_PANEL_ALT, fg=FG_DISABLED,
                                     font=("Segoe UI", 12, "bold"), relief="flat",
                                     padx=18, pady=10,
                                     command=self._on_primary_action,
                                     state=tk.DISABLED)
        self.primary_btn.pack(side=tk.RIGHT)

    def _build_instrument_panel(self, parent):
        row = ttk.Frame(parent)
        row.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(row, text="@@INSTR@@", font=UI_FONT_BOLD).pack(side=tk.LEFT)
        self.conn_light, self._set_conn_light = status_dot(row, "Disconnected")
        self.conn_light.pack(side=tk.RIGHT)
        ttk.Label(parent, text="Resource:").pack(side=tk.TOP, anchor="w", pady=(4, 0))
        self.resource_var = tk.StringVar(value=DEFAULT_RESOURCE)
        self.resource_entry = ttk.Entry(parent, textvariable=self.resource_var)
        self.resource_entry.pack(side=tk.TOP, fill=tk.X)
        self.simulate_var = tk.BooleanVar(value=False)
        self.simulate_chk = ttk.Checkbutton(parent, text="Simulate (no hardware)",
                                            variable=self.simulate_var)
        self.simulate_chk.pack(side=tk.TOP, anchor="w", pady=2)
        self.connect_btn = ttk.Button(parent, text="Connect", command=self._on_connect)
        self.connect_btn.pack(side=tk.TOP, anchor="w", pady=(2, 0))

    def _build_acquisition_panel(self, parent):
        grid = ttk.Frame(parent)
        grid.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(grid, text="Integration (s):", width=16).grid(row=0, column=0,
                                                                sticky="w", pady=2)
        self.int_time_var = tk.StringVar(value="0.1")
        self.int_entry = ttk.Entry(grid, textvariable=self.int_time_var, width=10)
        self.int_entry.grid(row=0, column=1, sticky="w")
        self.int_entry.bind("<Return>", lambda e: self._apply_params())
        ttk.Label(grid, text="Interval (s):", width=16).grid(row=1, column=0,
                                                             sticky="w", pady=2)
        self.interval_var = tk.StringVar(value="0")
        self.interval_entry = ttk.Entry(grid, textvariable=self.interval_var, width=10)
        self.interval_entry.grid(row=1, column=1, sticky="w")
        self.interval_entry.bind("<Return>", lambda e: self._apply_params())
        self.apply_btn = ttk.Button(parent, text="Apply", command=self._apply_params)
        self.apply_btn.pack(side=tk.TOP, anchor="w", pady=(4, 6))
        row = ttk.Frame(parent)
        row.pack(side=tk.TOP, fill=tk.X)
        self.live_start_btn = ttk.Button(row, text="Start live",
                                         command=self._on_start_live)
        self.live_start_btn.pack(side=tk.LEFT)
        self.live_stop_btn = ttk.Button(row, text="Stop live",
                                        command=self._on_stop_live)
        self.live_stop_btn.pack(side=tk.LEFT, padx=4)
        self.clear_btn = ttk.Button(row, text="Clear plots", command=self._clear_history)
        self.clear_btn.pack(side=tk.LEFT)
        ttk.Label(parent, text="'Live' previews without recording - use it to "
                              "align and set exposure.", style="Secondary.TLabel",
                  wraplength=250, justify="left").pack(side=tk.TOP, anchor="w")

    def _build_run_panel(self, parent):
        grid = ttk.Frame(parent)
        grid.pack(side=tk.TOP, fill=tk.X)
        grid.columnconfigure(1, weight=1)
        ttk.Label(grid, text="Sample:", width=14).grid(row=0, column=0, sticky="w",
                                                       pady=2)
        self.sample_var = tk.StringVar(value="sample_01")
        self._freeze(ttk.Entry(grid, textvariable=self.sample_var)).grid(
            row=0, column=1, sticky="we", pady=2)
        ttk.Label(grid, text="Baseline (s):", width=14).grid(row=1, column=0,
                                                             sticky="w", pady=2)
        self.baseline_var = tk.StringVar(value="60")
        b = self._freeze(ttk.Entry(grid, textvariable=self.baseline_var, width=10))
        b.grid(row=1, column=1, sticky="w", pady=2)
        ttk.Label(grid, text="Relaxation (s):", width=14).grid(row=2, column=0,
                                                               sticky="w", pady=2)
        self.relax_var = tk.StringVar(value="600")
        self.relax_entry = self._freeze(
            ttk.Entry(grid, textvariable=self.relax_var, width=10))
        self.relax_entry.grid(row=2, column=1, sticky="w", pady=2)
        for e in (b, self.relax_entry):
            e.bind("<KeyRelease>", lambda ev: self._refresh_size_estimate())
        self.relax_open_var = tk.BooleanVar(value=False)
        self._freeze(ttk.Checkbutton(parent, text="Relax until I press Finish",
                                     variable=self.relax_open_var,
                                     command=self._refresh_controls)).pack(
            side=tk.TOP, anchor="w")
        ttk.Label(parent, text="Baseline is a minimum: recording continues until "
                              "you mark the event.", style="Secondary.TLabel",
                  wraplength=250, justify="left").pack(side=tk.TOP, anchor="w")
        ttk.Label(parent, text="Notes:").pack(side=tk.TOP, anchor="w", pady=(4, 0))
        self.notes_var = tk.StringVar(value="")
        self._freeze(ttk.Entry(parent, textvariable=self.notes_var)).pack(
            side=tk.TOP, fill=tk.X)
        ttk.Label(parent, text="Session folder:").pack(side=tk.TOP, anchor="w",
                                                       pady=(4, 0))
        row = ttk.Frame(parent)
        row.pack(side=tk.TOP, fill=tk.X)
        self.out_dir_var = tk.StringVar(value=str(Path.cwd() / "@@NAME@@_data"))
        self._freeze(ttk.Entry(row, textvariable=self.out_dir_var)).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        self._freeze(ttk.Button(row, text="...", width=3,
                                command=self._browse_out_dir)).pack(side=tk.LEFT,
                                                                    padx=(4, 0))
        row = ttk.Frame(parent)
        row.pack(side=tk.TOP, fill=tk.X, pady=(4, 0))
        ttk.Label(row, text="Storage:").pack(side=tk.LEFT)
        self.storage_var = tk.StringVar(value="full")
        self._freeze(ttk.Combobox(row, textvariable=self.storage_var,
                                  values=list(STORAGE_MODES), state="readonly",
                                  width=9), enabled_state="readonly").pack(
            side=tk.LEFT, padx=4)
        self.size_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.size_var, style="Secondary.TLabel",
                  wraplength=250, justify="left").pack(side=tk.TOP, anchor="w",
                                                        pady=(2, 4))
        self.run_start_btn = ttk.Button(parent, text="START RUN",
                                        command=self._on_start_run)
        self.run_start_btn.pack(side=tk.TOP, anchor="w")
        self.session_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.session_var, foreground=ACCENT_GREEN,
                  background=BG_PANEL, wraplength=250, justify="left").pack(
            side=tk.TOP, anchor="w", pady=(4, 0))

    def _build_live_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Live")
        self.live_fig = Figure(figsize=(8, 4.6), dpi=100)
        self.live_ax = self.live_fig.add_subplot(111)
        self.live_ax.set_xlabel("x")           # TODO: real axis label + units
        self.live_ax.set_ylabel("signal")
        self.live_ax.grid(True, alpha=0.3)
        self.live_ax.set_xlim(X_MIN, X_MAX)
        (self.live_line,) = self.live_ax.plot([], [], lw=1.0, color=ACCENT_BLUE)
        # Explicit margins, not tight_layout(): the title is set on every
        # redraw, and tight_layout ran once before any title existed, so the
        # title would be drawn into the margin and clipped.
        self.live_fig.subplots_adjust(left=0.10, right=0.98, top=0.90, bottom=0.13)
        self.live_canvas = FigureCanvasTkAgg(self.live_fig, master=tab)
        self.live_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        style_mpl_toolbar(NavigationToolbar2Tk(self.live_canvas, tab))
        bar = ttk.Frame(tab)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.autoscale_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Auto Y", variable=self.autoscale_var).pack(
            side=tk.LEFT, padx=4)

    def _build_trend_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Trend")
        self.trend_fig = Figure(figsize=(8, 4.6), dpi=100)
        self.trend_ax = self.trend_fig.add_subplot(111)
        self.trend_ax.set_xlabel("Time since run start (s)")
        self.trend_ax.set_ylabel("value")      # TODO: what you actually track
        self.trend_ax.grid(True, alpha=0.3)
        (self.trend_line,) = self.trend_ax.plot([], [], lw=1.2, color=ACCENT_GREEN)
        self.trend_fig.tight_layout()
        self.trend_canvas = FigureCanvasTkAgg(self.trend_fig, master=tab)
        self.trend_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        style_mpl_toolbar(NavigationToolbar2Tk(self.trend_canvas, tab))
        bar = ttk.Frame(tab)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.readout_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.readout_var, font=MONO_FONT).pack(
            side=tk.LEFT, padx=6)

    # ==================================================================
    # Connection and parameters
    # ==================================================================
    def _on_connect(self):
        if self.driver is not None:
            return
        try:
            if self.simulate_var.get():
                self.driver = Simulated@@INSTR@@(event_clock=self._sim_event_clock)
            else:
                self.driver = @@INSTR@@Driver(resource=self.resource_var.get())
            self.driver.connect()
        except Exception as exc:
            messagebox.showerror("Connection failed", str(exc))
            self.driver = None
            return
        mode = "SIMULATED" if self.simulate_var.get() else "HARDWARE"
        self.connect_btn.config(text=f"Connected ({mode})", state=tk.DISABLED)
        self._set_conn_light(True, mode)
        self.status_var.set(f"Connected - {mode}.")
        self._refresh_controls()

    def _sim_event_clock(self):
        """Seconds since the operator's mark, so the simulated sample responds
        to the same button the real one does."""
        if self.run is None or self.run.event_start_ts is None:
            return None
        return (datetime.now() - self.run.event_start_ts).total_seconds()

    def _apply_params(self):
        try:
            int_time = float(self.int_time_var.get())
            interval = float(self.interval_var.get())
        except ValueError:
            messagebox.showerror("Invalid input", "Both must be numbers (seconds).")
            return False
        if not (INT_TIME_MIN_S <= int_time <= INT_TIME_MAX_S):
            messagebox.showerror("Out of range",
                                 f"Integration time must be between "
                                 f"{INT_TIME_MIN_S:g} and {INT_TIME_MAX_S:g} s.")
            return False
        if interval < 0 or int_time + interval < MIN_PERIOD_S:
            messagebox.showerror("Invalid period",
                                 f"Integration + interval must be at least "
                                 f"{MIN_PERIOD_S*1e3:g} ms.")
            return False
        self.params["int_time"] = int_time
        self.params["interval"] = interval
        self._refresh_size_estimate()
        self.status_var.set(f"Integration {int_time:g} s, interval {interval:g} s.")
        return True

    # ==================================================================
    # Acquisition
    # ==================================================================
    def _start_worker(self):
        if self.worker is not None and self.worker.is_alive():
            return True
        try:
            self.worker = @@INSTR@@Worker(self.driver, self.params,
                                          self.data_queue, self.status_queue)
            self.worker.start()
        except Exception as exc:
            messagebox.showerror("Could not start acquisition", str(exc))
            return False
        return True

    def _stop_worker(self):
        if self.worker is not None:
            self.worker.stop()
            self.worker = None

    def _on_start_live(self):
        if self.driver is None:
            messagebox.showerror("Not connected", "Connect the instrument first.")
            return
        if not self._apply_params():
            return
        if self.t0 is None:
            self.t0 = time.time()
        if self._start_worker():
            self.status_var.set("Live preview (nothing is being recorded).")
        self._refresh_controls()

    def _on_stop_live(self):
        if self.run is not None and self.run.is_running():
            return
        self._stop_worker()
        self.status_var.set("Live preview stopped.")
        self._refresh_controls()

    # ==================================================================
    # Run control
    # ==================================================================
    def _browse_out_dir(self):
        d = filedialog.askdirectory(initialdir=self.out_dir_var.get(),
                                    title="Choose session folder")
        if d:
            self.out_dir_var.set(d)
            self._refresh_size_estimate()

    def _read_protocol(self):
        try:
            baseline = float(self.baseline_var.get())
        except ValueError:
            messagebox.showerror("Invalid baseline", "Baseline must be a number.")
            return None
        relax = None
        if not self.relax_open_var.get():
            try:
                relax = float(self.relax_var.get())
            except ValueError:
                messagebox.showerror("Invalid relaxation",
                                     "Relaxation must be a number, or tick "
                                     "'Relax until I press Finish'.")
                return None
            if relax <= 0:
                messagebox.showerror("Invalid relaxation", "Must be positive.")
                return None
        return Protocol(sample_name=self.sample_var.get().strip(),
                        notes=self.notes_var.get().strip(),
                        baseline_min_duration_s=baseline,
                        relaxation_duration_s=relax,
                        integration_time_s=self.params["int_time"],
                        interval_s=self.params["interval"])

    def _estimate(self):
        if self.relax_open_var.get():
            return None, None
        try:
            total = float(self.baseline_var.get()) + float(self.relax_var.get())
        except ValueError:
            return None, None
        period = max(self.params["int_time"] + self.params["interval"], MIN_PERIOD_S)
        n = int(total / period)
        return n, n * BYTES_PER_SAMPLE.get(self.storage_var.get(),
                                           BYTES_PER_SAMPLE["full"])

    def _nearest_existing(self, p):
        p = Path(p)
        while not p.exists() and p.parent != p:
            p = p.parent
        return p

    def _refresh_size_estimate(self):
        n, nbytes = self._estimate()
        if n is None:
            self.size_var.set("Open-ended: size depends on how long you record.")
            return
        msg = f"~{n} samples, ~{_fmt_bytes(nbytes)} on disk."
        try:
            msg += (f" Free: "
                    f"{_fmt_bytes(shutil.disk_usage(self._nearest_existing(self.out_dir_var.get())).free)}.")
        except Exception:
            pass
        self.size_var.set(msg)

    def _on_start_run(self):
        """Pre-flight everything questionable in ONE dialog, then commit."""
        if self.driver is None:
            messagebox.showerror("Not connected", "Connect the instrument first.")
            return
        if self.run is not None and self.run.is_running():
            return
        if not self._apply_params():
            return
        protocol = self._read_protocol()
        if protocol is None:
            return

        warnings = []
        if not protocol.sample_name:
            warnings.append("No sample name - the folder will be the only record "
                            "of what this was.")
        # TODO: add the checks this experiment needs - missing calibration,
        #       an instrument that should be connected, a setting that
        #       contradicts another.
        n, nbytes = self._estimate()
        if nbytes is not None:
            try:
                free = shutil.disk_usage(
                    self._nearest_existing(self.out_dir_var.get())).free
                if nbytes > 0.8 * free:
                    warnings.append(f"Estimated {_fmt_bytes(nbytes)} against "
                                    f"{_fmt_bytes(free)} free.")
            except Exception:
                pass
        if warnings:
            body = "\n\n".join(f"- {w}" for w in warnings)
            if not messagebox.askokcancel("Start run anyway?",
                                          f"{body}\n\nStart the run?"):
                return

        try:
            base = Path(self.out_dir_var.get())
            base.mkdir(parents=True, exist_ok=True)
            self.writer = SessionWriter(base, storage_mode=self.storage_var.get())
            x_axis = (self.last_sample.x if self.last_sample is not None
                      else np.array([]))
            self.session_dir = self.writer.start(protocol, x_axis)
        except Exception as exc:
            messagebox.showerror("Could not create session folder", str(exc))
            self.writer = None
            return

        self._clear_history()
        self.t0 = time.time()
        self.run = Run(protocol)
        if not self._start_worker():
            self.writer.stop()
            self.writer = None
            self.run = None
            return
        self._handle_run_events(self.run.start())
        self.session_var.set(f"Recording to {self.session_dir}")
        self._refresh_controls()

    def _on_primary_action(self):
        """The one button / F2: dispatch on the current phase."""
        if self.run is None:
            return
        phase = self.run.phase
        if phase is Phase.BASELINE:
            if not self.run.baseline_minimum_satisfied():
                # The only confirm on this path: an early mark cannot be taken
                # back, and the time pressure is lower before the event than
                # during it.
                if not messagebox.askokcancel(
                        "Baseline is still short",
                        f"Only {_fmt_hms(self.run.phase_elapsed_s())} of the "
                        f"configured baseline recorded. Mark the event now?"):
                    return
            self._handle_run_events(self.run.begin_event())
        elif phase is Phase.EVENT:
            self._handle_run_events(self.run.end_event())
        elif phase is Phase.RELAXATION:
            if messagebox.askokcancel("Finish run", "End the run and close the "
                                                     "session now?"):
                self._handle_run_events(self.run.finish(reason="operator_finish"))
        self._refresh_controls()

    def _on_mark_event(self):
        if self.run is None or not self.run.is_running():
            return
        label = simpledialog.askstring("Mark event", "Annotation:", parent=self.root)
        if label:
            self._handle_run_events(self.run.mark_event(label.strip()))

    def _on_abort_run(self):
        if self.run is None or not self.run.is_running():
            return
        if messagebox.askokcancel("Abort run",
                                  "Abort and close the session?\n\nEverything "
                                  "recorded so far is kept and marked aborted."):
            self._handle_run_events(self.run.abort())

    def _handle_run_events(self, events):
        for kind, payload in events or []:
            if kind == "phase_entered":
                phase_value, ts = payload
                t_rel = ((ts - self.run.start_ts).total_seconds()
                         if self.run.start_ts else 0.0)
                if phase_value == Phase.EVENT.value:
                    self._add_marker(t_rel, "event start", ACCENT_AMBER)
                elif phase_value == Phase.RELAXATION.value:
                    self._add_marker(t_rel, "event end", ACCENT_GREEN)
                self.status_var.set(f"Phase: {phase_value}")
            elif kind in ("event_start", "event_end"):
                self.status_var.set(
                    f"{kind.replace('_', ' ')} recorded at "
                    f"{payload.strftime('%H:%M:%S.%f')[:-3]}.")
            elif kind == "event_marked":
                if self.writer is not None:
                    self.writer.log_event(payload)
                self._add_marker(payload.t_since_run_start_s, payload.label,
                                 FG_SECONDARY)
            elif kind in ("run_finished", "run_aborted"):
                self._close_out_run(kind == "run_aborted", str(payload))
        self._refresh_header()

    def _close_out_run(self, aborted, reason):
        self._stop_worker()
        summary = self.run.summary() if self.run is not None else {}
        if self.writer is not None:
            try:
                self.writer.write_phase_log(self.run.phase_rows())
                self.writer.write_run_summary(summary)
            except Exception as exc:
                self.status_var.set(f"Could not finish session files: {exc}")
            self.writer.stop()
        self.session_var.set(f"Session closed: {self.session_dir}")
        self._refresh_controls()
        messagebox.showinfo("Run aborted" if aborted else "Run complete",
                            f"Status   : {summary.get('status', '?')}\n"
                            f"Samples  : {self.sample_count}\n"
                            f"Total    : {_fmt_hms(summary.get('total_duration_s'))}\n\n"
                            f"{self.session_dir}")

    # ==================================================================
    # Main loop
    # ==================================================================
    def _drain_queues(self):
        while True:
            try:
                kind, payload = self.status_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_status(kind, payload)

        samples = []
        while True:
            try:
                samples.append(self.data_queue.get_nowait())
            except queue.Empty:
                break
        for s in samples:
            self._on_new_sample(s)

        if self.run is not None:
            self._handle_run_events(self.run.tick())

        if samples:
            now = time.time()
            if (now - self._last_plot_t) * 1000.0 >= self.PLOT_INTERVAL_MS:
                self._last_plot_t = now
                self._refresh_plots(samples[-1])
        self._refresh_header()

        if not self._closing:
            self._after_id = self.root.after(self.POLL_INTERVAL_MS,
                                             self._drain_queues)

    def _handle_status(self, kind, payload):
        if kind == "error":
            # A dead acquisition thread during a run is not survivable: the
            # phase machine would keep advancing while nothing is recorded.
            if self.run is not None and self.run.is_running():
                self._handle_run_events(self.run.abort(reason="acquisition_error"))
                messagebox.showerror("Acquisition error",
                                     f"{payload}\n\nThe run was aborted; "
                                     f"everything up to the failure is on disk.")
            else:
                self._stop_worker()
                messagebox.showerror("Acquisition error", str(payload))
            self._refresh_controls()
        elif kind == "info":
            self.status_var.set(str(payload))

    def _derive(self, sample):
        """TODO: the physics. Return (y_display, scalar_value).

        Keep this a thin call into a pure module so it can be tested without a
        display - that is where the science belongs.
        """
        y = sample.y
        value = float(sample.x[int(np.argmax(y))])   # placeholder: peak position
        return y, value

    def _on_new_sample(self, sample):
        self.last_sample = sample
        self.sample_count += 1
        ts = sample.ts_start + (sample.ts_end - sample.ts_start) / 2   # midpoint
        y_disp, value = self._derive(sample)

        fields = self.run.classify_sample(sample) if self.run is not None else {}
        if (self.writer is not None and self.writer.is_active()
                and self.run is not None and self.run.is_running()):
            try:
                self.writer.log_sample(ts, sample.x, sample.y,
                                       self.params["int_time"], y_derived=y_disp,
                                       value=value, **fields)
            except Exception as exc:
                self.status_var.set(f"Logging error: {exc}")

        if self.t0 is None:
            self.t0 = time.time()
        self.trend_t.append(time.time() - self.t0)
        self.trend_v.append(value)
        self._last_value = value

    @staticmethod
    def _decimate(x, y, limit):
        if len(x) <= limit:
            return x, y
        step = int(np.ceil(len(x) / limit))
        return x[::step], y[::step]

    def _visible_canvas(self):
        try:
            idx = self.notebook.index(self.notebook.select())
        except Exception:
            return None
        canvases = (self.live_canvas, self.trend_canvas)
        return canvases[idx] if 0 <= idx < len(canvases) else None

    def _draw(self, canvas):
        """draw_idle(), but only for the tab the operator can actually see."""
        if canvas is self._visible_canvas():
            canvas.draw_idle()

    def _on_tab_changed(self, _event=None):
        if self.last_sample is not None:
            self._refresh_plots(self.last_sample)

    def _refresh_plots(self, sample):
        y_disp, value = self._derive(sample)
        self.live_line.set_data(sample.x, y_disp)
        if self.autoscale_var.get():
            lo, hi = self.live_ax.get_xlim()
            m = (sample.x >= lo) & (sample.x <= hi) & np.isfinite(y_disp)
            if m.any():
                ymin, ymax = float(np.min(y_disp[m])), float(np.max(y_disp[m]))
                pad = (0.06 * (ymax - ymin) if ymax > ymin
                       else max(abs(ymax), 1.0) * 0.1)
                self.live_ax.set_ylim(ymin - pad, ymax + pad)
        self.live_ax.set_title(
            f"{sample.ts_start.strftime('%H:%M:%S.%f')[:-3]}   "
            f"int {self.params['int_time']:g} s   #{self.sample_count}", fontsize=9)
        self._draw(self.live_canvas)

        if self.trend_t:
            tx, ty = self._decimate(np.asarray(self.trend_t),
                                    np.asarray(self.trend_v), self.TREND_MAX_POINTS)
            self.trend_line.set_data(tx, ty)
            self.trend_ax.relim()
            self.trend_ax.autoscale_view()
            self._draw(self.trend_canvas)
            base = self.trend_v[0]
            self.readout_var.set(f"value = {value:10.4f}   "
                                 f"change {value - base:+.4f}")
        self.count_var.set(
            f"{self.sample_count} samples"
            + (f" | logged {self.writer.counter} | "
               f"{_fmt_bytes(self.writer.bytes_written)}"
               if self.writer is not None and self.writer.is_active() else ""))

    def _add_marker(self, t_s, label, color):
        """Added once when the mark happens, not on every redraw. Labels are
        stepped down in rotation so marks seconds apart stay readable."""
        self._marker_artists.append(
            self.trend_ax.axvline(t_s, color=color, lw=1.0, ls="--", alpha=0.9))
        row = self._marker_row % 3
        self._marker_row += 1
        self._marker_artists.append(self.trend_ax.annotate(
            label, xy=(t_s, 1.0), xycoords=("data", "axes fraction"),
            xytext=(3, -12 - 11 * row), textcoords="offset points",
            color=color, fontsize=8,
            bbox=dict(boxstyle="square,pad=0.15", fc=BG_PANEL, ec="none",
                      alpha=0.75)))
        self._draw(self.trend_canvas)

    def _clear_history(self):
        self.trend_t.clear()
        self.trend_v.clear()
        for a in self._marker_artists:
            try:
                a.remove()
            except Exception:
                pass
        self._marker_artists.clear()
        self._marker_row = 0
        self.trend_line.set_data([], [])
        self.sample_count = 0
        self.t0 = time.time()
        self.count_var.set("0 samples")
        for c in (self.live_canvas, self.trend_canvas):
            c.draw_idle()

    # ==================================================================
    # Header and control state
    # ==================================================================
    def _refresh_header(self):
        phase = self.run.phase if self.run is not None else Phase.IDLE
        text, color = _PHASE_STYLE[phase]
        self.phase_badge.config(text=text, fg=color)
        if self.run is None:
            self.phase_detail_var.set("No run started.")
            for v in self.clock_vars.values():
                v.set("--:--")
            return
        self.clock_vars["elapsed"].set(_fmt_hms(self.run.elapsed_s()))
        self.clock_vars["phase"].set(_fmt_hms(self.run.phase_elapsed_s()))
        remaining = self.run.remaining_s()
        self.clock_vars["remaining"].set("open" if remaining is None
                                         else _fmt_hms(remaining))
        self.clock_vars["event"].set(
            _fmt_hms(self.run.t_since_event_s(datetime.now()))
            if self.run.event_start_ts is not None else "--:--")

        if phase is Phase.BASELINE:
            detail = ("Baseline complete - mark the event when you start."
                      if self.run.baseline_minimum_satisfied()
                      else f"Baseline minimum {_fmt_hms(remaining)} remaining.")
            self.primary_btn.config(text="EVENT  START  (F2)", bg=ACCENT_AMBER,
                                    fg="#1a1204", activebackground=ACCENT_AMBER,
                                    state=tk.NORMAL)
        elif phase is Phase.EVENT:
            detail = "Event in progress - press again the moment you finish."
            self.primary_btn.config(text="EVENT  DONE  (F2)", bg=ACCENT_GREEN,
                                    fg="#04170f", activebackground=ACCENT_GREEN,
                                    state=tk.NORMAL)
        elif phase is Phase.RELAXATION:
            detail = ("Relaxation - recording until you press Finish."
                      if self.run.protocol.relaxation_duration_s is None
                      else f"Relaxation - {_fmt_hms(remaining)} remaining.")
            self.primary_btn.config(text="FINISH RUN NOW", bg=BG_INPUT,
                                    fg=FG_PRIMARY, state=tk.NORMAL)
        elif phase is Phase.COMPLETE:
            detail = f"Run complete. {self.sample_count} samples."
            self.primary_btn.config(text="- run complete -", bg=BG_PANEL_ALT,
                                    fg=FG_DISABLED, state=tk.DISABLED)
        else:
            detail = f"Run aborted ({self.run.abort_reason})."
            self.primary_btn.config(text="- run aborted -", bg=BG_PANEL_ALT,
                                    fg=FG_DISABLED, state=tk.DISABLED)
        self.phase_detail_var.set(detail)

    def _refresh_controls(self):
        connected = self.driver is not None
        running = self.run is not None and self.run.is_running()
        live = self.worker is not None and self.worker.is_alive()

        def st(flag):
            return tk.NORMAL if flag else tk.DISABLED

        self.connect_btn.config(state=st(not connected))
        self.simulate_chk.config(state=st(not connected))
        self.resource_entry.config(state=st(not connected))
        for w in (self.int_entry, self.interval_entry, self.apply_btn):
            w.config(state=st(not running))
        self.live_start_btn.config(state=st(connected and not live and not running))
        self.live_stop_btn.config(state=st(live and not running))
        self.clear_btn.config(state=st(not running))
        self.run_start_btn.config(state=st(connected and not running))
        self.abort_btn.config(state=st(running))
        self.event_btn.config(state=st(running))
        self.relax_entry.config(state=st(not self.relax_open_var.get()
                                         and not running))
        # Settings baked into protocol.json must not change mid-run. The
        # snapshot enforces it; disabling the widgets explains it.
        for widget, enabled in self._run_frozen_widgets:
            try:
                widget.config(state=(enabled if not running else tk.DISABLED))
            except Exception:
                pass
        self._refresh_size_estimate()
        self._refresh_header()

    # ==================================================================
    # Settings persistence
    # ==================================================================
    def _restore_settings(self):
        """Reload the form. Nothing here touches a driver or a calibration -
        those are re-acquired every session by design."""
        if not SETTINGS_FILE.exists():
            return
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for attr in _PERSISTED:
            if attr in data and hasattr(self, attr):
                try:
                    getattr(self, attr).set(data[attr])
                except Exception:
                    pass
        # Parsed silently: a corrupted settings file must not greet the
        # operator with an error dialog before the window is even up.
        try:
            int_time = float(self.int_time_var.get())
            interval = float(self.interval_var.get())
            if (INT_TIME_MIN_S <= int_time <= INT_TIME_MAX_S and interval >= 0
                    and int_time + interval >= MIN_PERIOD_S):
                self.params.update(int_time=int_time, interval=interval)
        except ValueError:
            pass

    def _save_settings(self):
        data = {}
        for attr in _PERSISTED:
            if hasattr(self, attr):
                try:
                    data[attr] = getattr(self, attr).get()
                except Exception:
                    pass
        try:
            SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _on_close(self):
        if self.run is not None and self.run.is_running():
            if not messagebox.askokcancel("Run in progress",
                                          "A run is still recording. Close "
                                          "anyway? It will be marked aborted."):
                return
            self._handle_run_events(self.run.abort(reason="app_closed"))
        try:
            self._save_settings()
        except Exception:
            pass
        self._closing = True
        # Cancel the pending callback: otherwise it fires against a destroyed
        # interpreter and prints "invalid command name ..." to stderr.
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
        for fn in (self._stop_worker,
                   lambda: self.writer.stop() if self.writer else None,
                   lambda: self.driver.close() if self.driver else None):
            try:
                fn()
            except Exception:
                pass
        self.root.destroy()
'''


T_APP = r'''"""
Entry point for @@TITLE@@.

    python @@NAME@@_app.py              # hardware
    python @@NAME@@_app.py --simulate   # no hardware needed

See gui/@@NAME@@_window.py for the GUI, @@NAME@@_protocol.py for the phase
machine, @@NAME@@_logging.py for the on-disk layout.
"""

import argparse
import tkinter as tk

from gui.theme import apply_ttk_theme, apply_mpl_theme
from gui.@@NAME@@_window import @@CLASS@@


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate", action="store_true",
                        help="start with the simulator pre-selected")
    args = parser.parse_args(argv)

    root = tk.Tk()
    # Applied once, before any widget or figure exists - never in a redraw path.
    apply_ttk_theme(root)
    apply_mpl_theme()
    @@CLASS@@(root, simulate=args.simulate)
    root.mainloop()


if __name__ == "__main__":
    main()
'''


T_TEST = r'''"""
Headless end-to-end test for @@TITLE@@.

    python tests/test_@@NAME@@.py

Builds the real window with root.withdraw() and drives it by calling methods
and setting Tk variables - not by synthesising clicks, which tests the toolkit
rather than the application.
"""

import csv
import json
import shutil
import sys
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Not the OS temp dir: sessions are large, and this keeps them on the same
# volume as the project.
_TMP = Path(__file__).resolve().parent / "_tmp_output" / "@@NAME@@"
_TMP.mkdir(parents=True, exist_ok=True)

import tkinter as tk
from tkinter import messagebox, simpledialog

# Before importing the window module: it binds SETTINGS_FILE at import time.
# Without this the test reads the operator's real settings and overwrites them
# on close.
import gui.@@NAME@@_window as _win
_win.SETTINGS_FILE = _TMP / "test_settings.json"

from gui.@@NAME@@_window import @@CLASS@@
from @@NAME@@_protocol import Phase, Protocol, Run


def _fresh_dir(name):
    """A never-before-used directory. Not rmtree() of a fixed path: on Windows
    an interrupted previous run can still hold a file open, and the resulting
    PermissionError fails the test for an unrelated reason."""
    p = _TMP / f"{name}_{uuid.uuid4().hex[:8]}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _patch_dialogs():
    messagebox.showinfo = lambda *a, **k: "ok"
    messagebox.showwarning = lambda *a, **k: "ok"
    # Printed, not swallowed: a silently stubbed showerror turns a failing
    # pre-flight into a mysteriously empty session.
    messagebox.showerror = lambda *a, **k: print(f"    [dialog:error] {a}")
    messagebox.askokcancel = lambda *a, **k: True
    messagebox.askyesno = lambda *a, **k: True
    simpledialog.askstring = lambda *a, **k: "test annotation"
    _win.messagebox = messagebox
    _win.simpledialog = simpledialog


def _pump(root, seconds):
    """Run the event loop for `seconds` of wall clock.

    NOT root.update(): that does not return until the queue is empty, and a
    recurring after() callback whose body outlasts its own interval keeps the
    queue permanently non-empty - the call never comes back and the test
    appears to hang. One bounded mainloop turn keeps the deadline enforceable.
    """
    import time as _t
    end = _t.time() + seconds
    while _t.time() < end:
        root.after(10, root.quit)
        root.mainloop()


def _teardown(app, root):
    app._closing = True
    if app._after_id is not None:
        try:
            root.after_cancel(app._after_id)
        except Exception:
            pass
    for fn in (app._stop_worker,
               lambda: app.writer.stop() if app.writer else None,
               lambda: app.driver.close() if app.driver else None):
        try:
            fn()
        except Exception:
            pass
    root.destroy()


def test_phase_machine():
    t = datetime(2026, 1, 1, 12, 0, 0)
    run = Run(Protocol(baseline_min_duration_s=60.0, relaxation_duration_s=300.0))
    assert run.end_event(t) == []           # refused, not reordered
    run.start(t)
    assert run.phase is Phase.BASELINE
    # Operator-gated: never advances on its own, however long it waits.
    assert run.tick(t + timedelta(seconds=600)) == []
    assert run.phase is Phase.BASELINE
    run.begin_event(t + timedelta(seconds=190))
    run.end_event(t + timedelta(seconds=212))
    assert run.phase is Phase.RELAXATION
    assert run.tick(t + timedelta(seconds=511)) == []
    assert any(k == "run_finished"
               for k, _ in run.tick(t + timedelta(seconds=513)))
    s = run.summary()
    assert abs(s["baseline_duration_s"] - 190.0) < 1e-6
    assert abs(s["event_duration_s"] - 22.0) < 1e-6
    print("  phase order, operator gating, timed finish  OK")


def test_boundary_contamination():
    class _S:
        def __init__(self, a, b):
            self.ts_start, self.ts_end = a, b
    t = datetime(2026, 1, 1, 12, 0, 0)
    run = Run(Protocol(baseline_min_duration_s=10.0))
    run.start(t)
    t_event = t + timedelta(seconds=20)
    run.begin_event(t_event)
    f = run.classify_sample(_S(t_event - timedelta(seconds=0.05),
                               t_event + timedelta(seconds=0.05)))
    assert f["phase"] == "baseline" and f["boundary_contaminated"] is True
    f = run.classify_sample(_S(t_event + timedelta(seconds=1),
                               t_event + timedelta(seconds=1.1)))
    assert f["phase"] == "event" and f["boundary_contaminated"] is False
    print("  sample classification across a phase boundary  OK")


def test_end_to_end():
    _patch_dialogs()
    out = _fresh_dir("e2e")
    root = tk.Tk()
    root.withdraw()
    app = @@CLASS@@(root, simulate=True)
    try:
        app.int_time_var.set("0.02")
        app.interval_var.set("0")
        app.out_dir_var.set(str(out))
        app.sample_var.set("e2e")
        app.baseline_var.set("1.0")
        app.relax_var.set("2.0")
        app._on_connect()
        assert app.driver is not None
        assert app._apply_params()

        app._on_start_run()
        assert app.run is not None and app.run.phase is Phase.BASELINE
        _pump(root, 1.5)
        assert app.sample_count > 0, "no samples recorded during baseline"

        app._on_primary_action()           # event start
        assert app.run.phase is Phase.EVENT
        app._on_mark_event()
        _pump(root, 0.5)
        app._on_primary_action()           # event end
        assert app.run.phase is Phase.RELAXATION

        _pump(root, 3.0)                   # relaxation then auto-finish
        assert app.run.phase is Phase.COMPLETE, app.run.phase
        assert app.worker is None, "acquisition should stop with the run"

        session = app.session_dir
        summary = json.loads((session / "run_summary.json").read_text())
        assert summary["status"] == "complete"
        assert summary["n_samples"] > 0
        assert summary["event_start_ts"] and summary["event_end_ts"]

        rows = list(csv.DictReader(open(session / "index.csv", encoding="utf-8")))
        assert len(rows) == summary["n_samples"]
        assert {r["phase"] for r in rows} >= {"baseline", "relaxation"}
        phases = (session / "phase_log.csv").read_text()
        for name in ("baseline", "event", "relaxation"):
            assert name in phases, name
        events = (session / "events.csv").read_text().strip().splitlines()
        assert len(events) == 2 and "test annotation" in events[1]
        with np.load(session / "samples" / rows[0]["filename"]) as d:
            assert "x" in d.files and "y_raw" in d.files
        print(f"  end-to-end simulated run  OK ({summary['n_samples']} samples)")
    finally:
        _teardown(app, root)


def main():
    tests = [test_phase_machine, test_boundary_contamination, test_end_to_end]
    failures = 0
    for fn in tests:
        print(f"[{fn.__name__}]")
        try:
            fn()
        except Exception:
            failures += 1
            traceback.print_exc()
            print(f"  FAILED: {fn.__name__}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
'''


T_THEME = r'''"""
Dark instrument-panel theme: ttk style + matplotlib rcParams, applied once at
startup and never inside a redraw path.

On Windows the native ttk themes ('vista', 'winnative') largely IGNORE custom
background and foreground colours on frames and buttons - they paint from the
OS theme regardless of what Style.configure says, which gives dark widgets on
light chrome. 'clam' is drawn by Tk from scratch and obeys style colours
cross-platform, so it is the one to use whenever a custom palette matters.

If the optional `sv_ttk` package (pip install sv-ttk) is importable, its
"dark" theme is used instead of the hand-styled clam theme below: it redraws
check boxes, tabs, entries and buttons with modern Fluent-style assets, which
is the single biggest visual upgrade available to a ttk app. Everything else
in this file (palette constants, matplotlib rcParams) still applies.
"""

import sys

import matplotlib

BG_ROOT = "#12161d"
BG_PANEL = "#1a212b"
BG_PANEL_ALT = "#212a36"
BG_INPUT = "#242e3b"
BG_HEADER = "#0d1117"
FG_PRIMARY = "#e8ecf1"
FG_SECONDARY = "#8b96a5"
FG_DISABLED = "#5a6472"
BORDER = "#2e3844"
ACCENT_BLUE = "#4da3ff"
ACCENT_GREEN = "#2ecc8f"
ACCENT_AMBER = "#f2a93c"
ACCENT_RED = "#ef4b4b"
# Danger states only - an output energised, a heater on, an abort. If
# everything is red, nothing is.
DANGER_BG = "#3a1414"
DANGER_FG = "#ff6b6b"
SAFE_BG = "#0f2a20"
SAFE_FG = ACCENT_GREEN

# Tk silently falls back to its default face when a family is missing, which
# is what makes the same app look polished on Windows and dated on Linux.
if sys.platform == "win32":
    _UI, _MONO = "Segoe UI", "Consolas"
elif sys.platform == "darwin":
    _UI, _MONO = "SF Pro Text", "Menlo"
else:
    _UI, _MONO = "DejaVu Sans", "DejaVu Sans Mono"
MONO_FONT = (_MONO, 10)
MONO_FONT_LARGE = (_MONO, 15, "bold")
MONO_FONT_MED = (_MONO, 11, "bold")
UI_FONT = (_UI, 9)
UI_FONT_BOLD = (_UI, 9, "bold")


def apply_ttk_theme(root):
    from tkinter import ttk
    style = ttk.Style(root)
    root.configure(bg=BG_ROOT)
    try:
        import sv_ttk
    except ImportError:
        sv_ttk = None
    if sv_ttk is not None:
        sv_ttk.set_theme("dark")
        style.configure(".", font=UI_FONT)
        return style
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(".", background=BG_PANEL, foreground=FG_PRIMARY, font=UI_FONT,
                    fieldbackground=BG_INPUT, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER)
    style.configure("TFrame", background=BG_PANEL)
    style.configure("TLabel", background=BG_PANEL, foreground=FG_PRIMARY)
    style.configure("Secondary.TLabel", background=BG_PANEL, foreground=FG_SECONDARY)
    style.configure("TLabelframe", background=BG_PANEL, foreground=FG_PRIMARY,
                    bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BG_PANEL,
                    foreground=FG_SECONDARY, font=UI_FONT_BOLD)
    style.configure("TButton", background=BG_INPUT, foreground=FG_PRIMARY,
                    bordercolor=BORDER, focusthickness=1, padding=4)
    style.map("TButton", background=[("active", BG_PANEL_ALT),
                                     ("disabled", BG_PANEL)],
              foreground=[("disabled", FG_DISABLED)])
    style.configure("TEntry", fieldbackground=BG_INPUT, foreground=FG_PRIMARY,
                    insertcolor=FG_PRIMARY, bordercolor=BORDER)
    style.map("TEntry", foreground=[("disabled", FG_DISABLED)])
    style.configure("TCombobox", fieldbackground=BG_INPUT, foreground=FG_PRIMARY,
                    background=BG_INPUT, arrowcolor=FG_PRIMARY, bordercolor=BORDER)
    style.map("TCombobox", fieldbackground=[("readonly", BG_INPUT)],
              foreground=[("disabled", FG_DISABLED)])
    root.option_add("*TCombobox*Listbox.background", BG_INPUT)
    root.option_add("*TCombobox*Listbox.foreground", FG_PRIMARY)
    style.configure("TCheckbutton", background=BG_PANEL, foreground=FG_PRIMARY)
    style.map("TCheckbutton", foreground=[("disabled", FG_DISABLED)])
    style.configure("TRadiobutton", background=BG_PANEL, foreground=FG_PRIMARY)
    style.configure("TNotebook", background=BG_ROOT, bordercolor=BORDER)
    style.configure("TNotebook.Tab", background=BG_PANEL_ALT,
                    foreground=FG_SECONDARY, padding=(10, 5))
    style.map("TNotebook.Tab", background=[("selected", BG_PANEL)],
              foreground=[("selected", FG_PRIMARY)])
    style.configure("TPanedwindow", background=BG_ROOT)
    style.configure("TScrollbar", background=BG_PANEL_ALT, troughcolor=BG_ROOT,
                    bordercolor=BORDER, arrowcolor=FG_SECONDARY)
    style.configure("TSeparator", background=BORDER)
    return style


def apply_mpl_theme():
    """Applied once at startup. Every figure created afterwards inherits these,
    so nothing in the redraw path needs to know about theming."""
    matplotlib.rcParams.update({
        "figure.facecolor": BG_PANEL, "axes.facecolor": BG_PANEL,
        "axes.edgecolor": BORDER, "axes.labelcolor": FG_PRIMARY,
        "axes.titlecolor": FG_PRIMARY, "xtick.color": FG_SECONDARY,
        "ytick.color": FG_SECONDARY, "text.color": FG_PRIMARY,
        "grid.color": BORDER, "grid.alpha": 0.6,
        "legend.facecolor": BG_PANEL_ALT, "legend.edgecolor": BORDER,
        "legend.labelcolor": FG_PRIMARY, "savefig.facecolor": BG_PANEL,
    })
'''


T_WIDGETS = r'''"""
Reusable layout widgets: a collapsible section and a scrollable frame, so a
dense control column fits in a third of the window without losing anything.

Both keep every child alive at all times (pack/pack_forget, never
destroy/rebuild). Collapsing only hides. This matters beyond tidiness:
headless tests drive the app by setting Tk variables directly, so every
variable must exist whether or not its section happens to be visible.
"""

import tkinter as tk
from tkinter import ttk

from gui.theme import (BG_PANEL, BG_PANEL_ALT, BG_ROOT, FG_PRIMARY, FG_SECONDARY,
                       ACCENT_GREEN, UI_FONT_BOLD)


def style_mpl_toolbar(toolbar):
    """Recolour a NavigationToolbar2Tk for the dark theme.

    The toolbar is plain tk, not ttk, so it ignores the style and comes out
    light grey. Matplotlib picks the icon colour from each button's
    `foreground` at the moment the icon is rendered, so set the colours first
    and then ask it to render again.
    """
    toolbar.config(background=BG_PANEL)
    toolbar._message_label.config(background=BG_PANEL, foreground=FG_SECONDARY)
    for child in toolbar.winfo_children():
        try:
            child.config(background=BG_PANEL, foreground=FG_PRIMARY,
                         activebackground=BG_PANEL_ALT, activeforeground=FG_PRIMARY,
                         highlightthickness=0, borderwidth=0)
            if isinstance(child, tk.Checkbutton):          # pan / zoom toggles
                child.config(selectcolor=BG_PANEL_ALT)
        except tk.TclError:
            pass
        if getattr(child, "_image_file", None):
            toolbar._set_image_for_button(child)
    return toolbar


class CollapsibleSection(ttk.Frame):
    def __init__(self, parent, title, expanded=False, **kwargs):
        super().__init__(parent, style="TFrame", **kwargs)
        self._expanded = tk.BooleanVar(value=expanded)
        header = tk.Frame(self, bg=BG_PANEL_ALT, cursor="hand2")
        header.pack(side=tk.TOP, fill=tk.X)
        self._arrow = tk.StringVar(value=self._arrow_char())
        arrow = tk.Label(header, textvariable=self._arrow, bg=BG_PANEL_ALT,
                         fg=FG_SECONDARY, font=UI_FONT_BOLD, width=2)
        arrow.pack(side=tk.LEFT, padx=(6, 0), pady=4)
        label = tk.Label(header, text=title, bg=BG_PANEL_ALT, fg=FG_PRIMARY,
                         font=UI_FONT_BOLD, anchor="w")
        label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4, pady=4)
        for w in (header, arrow, label):
            w.bind("<Button-1>", lambda e: self.toggle())
        self.body = ttk.Frame(self)
        if expanded:
            self.body.pack(side=tk.TOP, fill=tk.X, padx=2, pady=(2, 6))

    def _arrow_char(self):
        return "v" if self._expanded.get() else ">"

    def toggle(self):
        self.set_expanded(not self._expanded.get())

    def set_expanded(self, expanded):
        if expanded == self._expanded.get():
            return
        self._expanded.set(expanded)
        self._arrow.set(self._arrow_char())
        if expanded:
            self.body.pack(side=tk.TOP, fill=tk.X, padx=2, pady=(2, 6))
        else:
            self.body.pack_forget()


class ScrollableFrame(ttk.Frame):
    """Vertically scrollable container. Put widgets in .interior."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self.canvas = tk.Canvas(self, bg=BG_ROOT, highlightthickness=0, bd=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.interior = ttk.Frame(self.canvas)
        self._window = self.canvas.create_window((0, 0), window=self.interior,
                                                 anchor="nw")
        self.interior.bind("<Configure>", lambda e: self.canvas.configure(
            scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(
            self._window, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all(
            "<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


def status_dot(parent, initial_text):
    """Compact connected/disconnected indicator. Neutral colours only -
    a connection state is not a danger state."""
    lbl = tk.Label(parent, text=f"* {initial_text}", bg=BG_PANEL_ALT,
                   fg=FG_SECONDARY, font=("Segoe UI", 8), padx=6, pady=2)

    def set_state(connected, text=None):
        text = text or ("Connected" if connected else "Disconnected")
        lbl.configure(text=f"* {text}",
                      fg=(ACCENT_GREEN if connected else FG_SECONDARY))

    return lbl, set_state
'''


T_README = r'''# @@TITLE@@

```
python @@NAME@@_app.py --simulate   # no hardware needed
python @@NAME@@_app.py              # real instrument
python tests/test_@@NAME@@.py       # headless end-to-end
```

Scaffolded by the `instrument-gui` skill. Search for `TODO` - those are the
places that need real hardware and real physics.

## Run structure

```
BASELINE ──────────▶ EVENT ──▶ RELAXATION ──▶ done
 timed minimum,      operator-  timed, or until
 then waits for you  timed      you press Finish
```

One button (or **F2**) always performs the next step; **F4** timestamps a
free-text annotation at any time. The two phase marks are deliberately not
behind a confirmation dialog, because their whole point is the timestamp.

## What to fill in

| File | What |
|---|---|
| `instruments/@@INSTR_MOD@@.py` | `connect` / `configure` / `read` / `close`; make the simulator model your measurement chain |
| `gui/@@NAME@@_window.py` | `_derive()` - the physics; axis labels; pre-flight checks |
| `@@NAME@@_protocol.py` | phase names and durations if your protocol differs |
| `@@NAME@@_logging.py` | `BYTES_PER_SAMPLE` - re-measure once your arrays are real |

Keep physics out of the window: put it in a module with no Tk and no hardware
so it can be tested in milliseconds.

## Session layout

```
<out>/@@NAME@@_session_YYYYmmdd_HHMMSS_<sample>/
    session_info.txt  protocol.json  calibration.npz
    index.csv         samples/*.npz  derived.csv
    phase_log.csv     events.csv     run_summary.json
```

`run_summary.json` carries `status` - `complete` or `aborted`. A truncated run
must not be read as a plateau.
'''


FILES = [
    ("instruments/@@INSTR_MOD@@.py", T_INSTRUMENT, False),
    ("@@NAME@@_protocol.py", T_PROTOCOL, False),
    ("@@NAME@@_logging.py", T_LOGGING, False),
    ("gui/@@NAME@@_window.py", T_WINDOW, False),
    ("@@NAME@@_app.py", T_APP, False),
    ("tests/test_@@NAME@@.py", T_TEST, False),
    ("gui/theme.py", T_THEME, False),
    ("gui/widgets.py", T_WIDGETS, False),
    ("README_@@NAME@@.md", T_README, False),
]


def camel(name):
    return "".join(p.capitalize() for p in re.split(r"[_\-\s]+", name) if p)


def render(text, subs):
    for token, value in subs.items():
        text = text.replace(token, value)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True,
                    help="app slug, snake_case, e.g. pump_probe")
    ap.add_argument("--instrument", required=True,
                    help="instrument class name, e.g. LockIn, CCS200, Camera")
    ap.add_argument("--dest", default=".", help="project root (default: .)")
    ap.add_argument("--title", default=None,
                    help="human-readable title (default: derived from --name)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite files that already exist")
    args = ap.parse_args(argv)

    if not re.fullmatch(r"[a-z][a-z0-9_]*", args.name):
        ap.error("--name must be lowercase snake_case, starting with a letter")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", args.instrument):
        ap.error("--instrument must be a valid Python identifier")

    dest = Path(args.dest).resolve()
    title = args.title or (args.name.replace("_", " ").title() + " control")
    subs = {
        "@@NAME@@": args.name,
        "@@TITLE@@": title,
        "@@CLASS@@": camel(args.name) + "GUI",
        "@@INSTR@@": args.instrument,
        "@@INSTR_MOD@@": args.instrument.lower(),
        "@@PROTO_IMPORT@@":
            f"from {args.name}_protocol import Phase, Protocol, Run",
    }

    written, skipped = [], []
    for rel_template, body, _ in FILES:
        rel = render(rel_template, subs)
        path = dest / rel
        if path.exists() and not args.force:
            skipped.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(body, subs), encoding="utf-8")
        written.append(rel)

    for pkg in ("gui", "instruments", "tests"):
        init = dest / pkg / "__init__.py"
        if (dest / pkg).is_dir() and not init.exists():
            init.write_text("", encoding="utf-8")
            written.append(f"{pkg}/__init__.py")

    print(f"Scaffolded into {dest}\n")
    for rel in written:
        print(f"  created  {rel}")
    for rel in skipped:
        print(f"  kept     {rel}  (already exists; --force to overwrite)")
    print(f"\nNext:\n"
          f"  python {args.name}_app.py --simulate\n"
          f"  python tests/test_{args.name}.py\n"
          f"\nThen replace the TODOs in instruments/{subs['@@INSTR_MOD@@']}.py "
          f"and _derive() in gui/{args.name}_window.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
