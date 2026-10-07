# Architecture

## Layers

Keep these in separate files. The boundaries are what let you test the hard
parts without hardware and without a display.

```
<name>_app.py            entry point: build root, apply theme, construct window
gui/<name>_window.py     all Tk; owns the drain loop; no hardware calls
gui/theme.py             ttk style + matplotlib rcParams, applied once
gui/widgets.py           reusable layout widgets
instruments/<dev>.py     driver + simulator + worker thread; no Tk
<name>_protocol.py       phase state machine; no Tk, no hardware
<name>_logging.py        session writer; no Tk, no hardware
<name>_math.py           pure array functions; no Tk, no hardware, no I/O
tests/test_<name>.py     headless
```

The three modules with "no Tk, no hardware" are where the science lives, and
they are testable in milliseconds. Resist the pull to let the window class
accumulate physics - a 2000-line window that computes absorbance inline cannot
be tested except by launching it.

## Threading

### The contract

- Worker threads: `daemon=True`, own the driver, block freely.
- Workers communicate **only** by putting tuples on `queue.Queue`.
- The Tk thread drains those queues on a `root.after()` timer and is the only
  thread that touches a widget, a Matplotlib artist, or a Tk variable.
- Workers expose a `stop()` that sets a `threading.Event`, and sleep in short
  chunks so stopping is prompt.

```python
class AcquisitionWorker(threading.Thread):
    def __init__(self, driver, params, data_queue, status_queue):
        super().__init__(daemon=True)
        self._stop_event = threading.Event()
        self.last_sample_ts = time.time()   # watched by a watchdog

    def stop(self):
        self._stop_event.set()

    def run(self):
        try:
            while not self._stop_event.is_set():
                ...
                try:
                    data = self.driver.read(...)
                except Exception as exc:
                    self.status_queue.put(("error", f"Acquisition failed: {exc}"))
                    break
                self.last_sample_ts = time.time()
                self.data_queue.put(Sample(...))
                # sleep the remainder in chunks so stop() is responsive
                remaining = period - (time.time() - t_start)
                while remaining > 0 and not self._stop_event.is_set():
                    chunk = min(remaining, 0.05)
                    time.sleep(chunk)
                    remaining -= chunk
        finally:
            self.status_queue.put(("stopped", None))
```

`params` is a plain dict the GUI mutates live. The worker re-reads it each
iteration, so an integration-time change takes effect on the next sample
without restarting the thread. Only push the change to the hardware when the
value actually differs - reconfiguring every iteration hammers the device.

### The drain loop

One timer, draining every queue, then redrawing. Batch: take everything
queued, process it all, redraw once at the end.

```python
def _drain_queues(self):
    while True:                                  # status first: errors win
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
        self._on_new_sample(s)                   # log + buffers, cheap

    if self.run is not None:
        self._handle_run_events(self.run.tick()) # time-driven transitions

    if samples and self._due_for_redraw():
        self._refresh_plots(samples[-1])         # expensive, throttled

    if not self._closing:
        self._after_id = self.root.after(self.POLL_MS, self._drain_queues)
```

Two separate rates matter: drain often (50 ms) so nothing backs up, redraw
rarely (250 ms) because drawing is the expensive part. See
`live-plotting.md`.

### Shutdown

Pending `after()` callbacks fire against a destroyed interpreter and print
`invalid command name ..._drain_queues` to stderr. Track the id and cancel it:

```python
def _on_close(self):
    self._closing = True                     # stop re-registering
    if self._after_id is not None:
        self.root.after_cancel(self._after_id)   # kill the one already queued
    for worker in (...):
        worker.stop(); worker.join(timeout=1.0)
    for driver in (...):
        driver.close()                       # drivers put hardware in a safe state
    self.root.destroy()
```

Order matters: stop producers, then close drivers, then destroy the window.
A driver's `close()` is the right place to switch off a heater or an output -
the app should not be able to exit leaving hardware energised.

## Phase state machines

An automated protocol is a sequence of phases with entry conditions. Model it
explicitly, in its own module, with no Tk and no hardware - then it is testable
by stepping a fake clock.

### Thread or no thread?

**No thread** when phases are driven only by time and operator clicks, and
nothing must be commanded at a boundary. Tick it from the drain loop. Simpler,
and there is nothing to deadlock.

**Its own thread** when the machine must command hardware at phase boundaries
(ramp a voltage, open a shutter, hold a temperature). It then needs to be able
to shut that hardware down even if the UI wedges, which means it cannot depend
on the Tk loop running.

### Shape

```python
class Phase(Enum):
    IDLE = "idle"; BASELINE = "baseline"; ...; COMPLETE = "complete"; ABORTED = "aborted"

class Run:
    def __init__(self, protocol, now=None):
        self._now = now or datetime.now      # injectable clock => testable
        self.transitions = []                # [(phase_value, entry_ts)]

    def start(self, ts=None): ...            # each returns a list of events
    def advance(self, ts=None): ...          # for the GUI to render
    def abort(self, reason, ts=None): ...
    def tick(self, now=None): ...            # time-driven transitions only
    def classify_sample(self, sample): ...   # -> dict of per-sample fields
```

Methods return event lists rather than touching the GUI, so the machine has no
idea a GUI exists. The GUI renders them.

**Refuse out-of-order operations rather than reordering them.** `advance()`
called in the wrong phase returns `[]` and changes nothing. A state machine
that helpfully does what it guesses you meant is one that silently records the
wrong thing.

**Keep a transition list and classify from it**, not from `self.phase`.
`self.phase` is "now"; a sample that arrived 300 ms ago may belong to the
previous phase. Classify both ends of the integration window:

```python
def classify_sample(self, sample):
    phase_start, entry_ts = self._phase_at(sample.ts_start)
    phase_end, _ = self._phase_at(sample.ts_end)
    return {
        "phase": phase_start,
        "t_since_run_start_s": (sample.ts_start - self.start_ts).total_seconds(),
        "t_since_phase_start_s": (sample.ts_start - entry_ts).total_seconds(),
        "boundary_contaminated": phase_end != phase_start,
    }
```

That last flag is worth its weight. A sample whose exposure straddles the
moment the protocol changed is part one thing and part another, and analysis
should drop it rather than average across the discontinuity.

**Record both ends of every sample.** Have the worker capture `ts_start` just
before the blocking read and `ts_end` just after. A single timestamp is
ambiguous by one integration time, which is exactly the resolution a phase
boundary needs.

## Composing a large window

Past about a thousand lines a window class becomes unnavigable. Split by
concern using mixins that share one window:

```python
class ExperimentGUI(StatusHeaderMixin, SequencerPanelMixin, AnalysisTabMixin):
    def __init__(self, root):
        self._build_ui()
        self._init_status_header()     # from StatusHeaderMixin
        self._init_sequencer_panel()   # from SequencerPanelMixin
```

Each mixin owns its widgets and its handlers, and they share `self`. This is
inheritance used for file organisation rather than polymorphism, which is a
fair trade when the alternative is one unreadable file. Keep the shared
surface small and documented: if every mixin reaches into every attribute,
nothing has been separated.

Collapsible sections keep a dense control column usable. Collapse by
`pack_forget()`, never by destroying widgets - headless tests set Tk variables
directly, and those must exist whether or not their section is visible.

## Theming

Apply once at startup, before any widget or figure exists, never in the redraw
path. On Windows the native ttk themes ignore custom background and foreground
colours on frames and buttons; `style.theme_use("clam")` is the one that obeys
them cross-platform. Set Matplotlib `rcParams` at the same point, so every
figure created later inherits them and no drawing code needs to know about
theming.
