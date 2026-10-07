# Instrument drivers and their simulators

## The contract

Driver and simulator are duck-typed twins. Same method names, same argument
names, same return types. The GUI picks between them with one `if` at connect
time and never mentions the difference again.

```python
class SpectrometerDriver:
    def connect(self): ...
    def configure(self, **settings): ...     # integration time, gain, range
    def read(self, **kw): ...                # blocking; returns arrays
    def close(self): ...                     # leaves hardware SAFE

class SimulatedSpectrometer:                 # same five methods
    ...
```

Keep vendor specifics (ctypes handles, SCPI strings, status bit masks) inside
the driver. The worker and the GUI should not know whether the device speaks
VISA, a serial protocol, or a DLL.

### What goes inside the driver

Pick the transport library by how the device talks, and keep the import
inside the driver module so the rest of the app has no dependency on it:

| Device speaks | Use | Notes |
|---|---|---|
| SCPI over USB-TMC, GPIB, Ethernet, serial | `pyvisa` (+ `pyvisa-py` if no NI-VISA) | `inst.query("MEAS:VOLT?")`; set `inst.timeout` explicitly, the default is short |
| Plain serial with a vendor ASCII protocol | `pyserial` | read until a terminator; never `read(n)` and hope |
| A vendor DLL / .so | `ctypes` or `cffi` | wrap each call, check the status code, raise a Python exception |
| NI DAQ cards | `nidaqmx` | tasks are resources: create once, `close()` in `close()` |
| Cameras | vendor SDK (`pypylon`, `pyspin`, `pymmcore`) | long exposures are the classic Tk-thread blocker |

`pymeasure` already ships drivers for many SCPI instruments (Keithley, SRS,
Agilent); if one exists for your device, wrap it rather than rewriting the
command set, and keep the five-method contract above as the surface the
worker sees.

Cite the source for every magic number in a comment - the datasheet page, the
programming manual section, the status bit name from the vendor header. A bare
`0x0010` is unmaintainable; `_STATUS_SCAN_TRANSFER = 0x0010  # from TLCCS.h`
is not.

## Simulators that are worth having

A simulator that returns a canned array tests nothing but plumbing. Model the
measurement chain, so the derived quantities the app computes are exercised:

```python
# Good: model the chain the instrument actually measures
counts = lamp(wl) * 10 ** (-absorbance(wl)) * exposure + dark + noise

# Weak: emit the answer directly
absorbance = gaussian(wl, center, width)
```

With the first form, dark subtraction, referencing against a blank, and the
log conversion are all genuinely tested, including their edge cases - the
blue end where the lamp delivers nothing really does produce an undefined
absorbance, and the app's handling of that gets exercised.

Give the simulator hooks for the states the operator is asked to create:

```python
sim.lamp_blocked = True    # what "cover the input" means, for a dark frame
sim.blank_mode = True      # what "put the blank in the beam" means
```

The app sets these for the duration of a simulated calibration, so the
calibration path is the same code in both modes.

Let the simulator respond to the protocol. A callback reporting "seconds since
the operator marked the event" lets the simulated sample change on cue, so a
rehearsal shows a real kinetic trace rather than a flat line. That is what
makes simulate mode useful for practising a protocol, not just for smoke tests.

Add a deliberate failure hook so the error path is testable:

```python
self._simulate_error = False   # when True, read() raises
```

Seed the RNG for reproducibility, and cap simulated delays (`time.sleep(min(t,
0.15))`) so tests do not take as long as real exposures.

## Acquisition patterns

### Discard the first sample after any reconfiguration

Most instruments return one stale or mis-exposed sample after a settings
change - a buffer filled under the old settings, or a detector that has not
settled. Drop it explicitly and say so on the status queue, rather than letting
one wrong sample into the data:

```python
if int_time != self._last_int_time:
    self.driver.configure(integration_time=int_time)
    self._last_int_time = int_time
    first_sample = True
...
if first_sample:
    first_sample = False
    self.status_queue.put(("info", "Discarded warm-up sample."))
    continue
```

### Prefer continuous mode when the device has one

A naive "start acquisition, wait, read" loop makes every sample cost two
integration times on devices that must finish the current integration before
starting the one you asked for. If the device supports a free-running mode,
start it once and read completed samples as they appear - often a 2x
throughput difference for one call.

### Always have a timeout

Poll for completion with a deadline and raise on expiry. A read that waits
forever on a device that has stopped responding will hang the worker, and the
only visible symptom is that the plot stopped updating.

```python
deadline = time.time() + max(int_time * 2.0 + 0.5, 1.0)
while not self._complete():
    if time.time() > deadline:
        raise RuntimeError("Acquisition timeout")
    time.sleep(min(0.01, max(0.0005, int_time / 20.0)))
```

Poll fast relative to the expected duration, but cap the rate so polling does
not become its own load.

### Watchdog from the outside

The worker publishes `last_sample_ts` on every iteration. A supervisor - the
protocol machine, or the drain loop - checks that it is advancing and aborts if
it stalls. A worker that is blocked cannot report that it is blocked; only
something outside it can notice.

## Connection UX

Enumerate ports or resources into a dropdown with a refresh button, and
pre-select the likely one by matching vendor/product id or a known string. The
operator should not have to type `USB0::0x1313::0x8089::M01012309::RAW`.

Report the mode prominently once connected - `Connected (SIMULATED)` versus
`Connected (HARDWARE)`. Someone will eventually record a session in simulate
mode and only notice at analysis time; make that hard.

Keep a raw-command console behind a collapsed section for serial and SCPI
instruments. When the device misbehaves at 2 a.m., being able to send one
command and see the reply is worth more than any amount of UI polish.

## Dangerous outputs

Anything that heats, energises, or moves needs more than a checkbox:

- A software clamp on the settable range, with the limit itself an editable
  field so raising it is a deliberate act.
- A prominent indicator of actual state, driven by reading the device back,
  not by what was last commanded.
- An always-visible emergency-off that does not depend on the protocol
  machine, the queue, or anything else still working.
- `close()` that switches the output off before disconnecting, so quitting the
  app cannot leave a sample energised.
