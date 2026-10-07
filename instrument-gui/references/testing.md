# Testing an instrument GUI

The simulator makes this tractable: a full protocol can run headlessly in
seconds, with no hardware and no display.

## Headless end-to-end

Build the real window, hide it, drive it by calling methods and setting Tk
variables - not by synthesising clicks, which is brittle and tests the toolkit
rather than the application.

```python
root = tk.Tk()
root.withdraw()                      # no visible window
app = ExperimentGUI(root, simulate=True)

app.int_time_var.set("0.02")
app.out_dir_var.set(str(tmp))
app._on_connect()
app._acquire_calibration("dark"); _pump(root, 2.0)
app._on_start_run()
_pump(root, 1.5)
app._on_primary_action()             # operator mark
_pump(root, 3.0)
assert app.run.phase is Phase.COMPLETE
```

Assert on the **output files**, not just in-memory state - that is what the
experiment actually produces:

```python
summary = json.loads((session / "run_summary.json").read_text())
assert summary["status"] == "complete"
assert summary["n_samples"] > 0
rows = list(csv.DictReader(open(session / "index.csv")))
assert {r["phase"] for r in rows} >= {"baseline", "relaxation"}
```

## The bounded pump

This is the single most important idiom, and the non-obvious one:

```python
def _pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.after(10, root.quit)
        root.mainloop()
```

Not `root.update()`. `update()` does not return until the event queue is
empty, and a recurring `after()` callback whose body takes longer than its own
interval keeps the queue permanently non-empty - the call never comes back and
the test appears to hang forever. Running one bounded turn of the real
mainloop keeps the deadline enforceable no matter how far behind the app is.

If a test hangs after a run starts, this is almost always why - and it is also
telling you the per-sample work exceeds the drain interval, which is a real
problem for the application, not only for the test. Profile it rather than
just fixing the test.

## Patch the modals

Dialogs block on a click that will never come:

```python
messagebox.askokcancel = lambda *a, **k: True
messagebox.showinfo = lambda *a, **k: "ok"
messagebox.showerror = lambda *a, **k: print(f"[dialog:error] {a}")
simpledialog.askstring = lambda *a, **k: "test annotation"

import gui.window as mod            # modules that did `from tkinter import messagebox`
mod.messagebox = messagebox          # hold their own reference - patch it too
```

Printing rather than swallowing errors matters: a silently stubbed
`showerror` turns a failing pre-flight into a mysteriously empty session.

## Isolate the settings file

If the app persists its form, redirect the path *before* importing the module
that binds it, or the test will read the operator's real settings and
overwrite them on close:

```python
import gui.window as mod
mod.SETTINGS_FILE = tmp / "test_settings.json"
```

## Tear down properly

```python
def _teardown(app, root):
    app._closing = True
    if app._after_id is not None:
        root.after_cancel(app._after_id)
    app._stop_worker()
    if app.writer is not None:
        app.writer.stop()
    if app.driver is not None:
        app.driver.close()
    root.destroy()
```

Without the `after_cancel`, the pending callback fires against a destroyed
interpreter and prints `invalid command name ..._drain_queues`, which buries
whatever the test was trying to show you.

Creating a second `Tk()` in one process after destroying the first provokes a
harmless `ttk::ThemeChanged` complaint on stderr. Known Tk quirk; ignore it.

## Test the physics against known truth

The highest-value test: drive the simulator with a known model, run the real
analysis, and assert it recovers the inputs.

```python
# simulator: band at 524 nm shifting to 535 nm with tau = 240 s
result = process(session_dir)
assert abs(result["lambda_pre"] - 524.0) < 1.0
assert abs(result["tau_s"] - 240.0) < 10.0
```

This tests the whole chain - acquisition, calibration, logging, reduction - in
one assertion, and it catches sign errors and unit mistakes that no unit test
on an individual function will.

## Pin the numbers you document

If the documentation claims a precision or a bias, assert it:

```python
found = [find_peak(noisy_spectrum()).wavelength_nm for _ in range(25)]
assert float(np.std(found)) < 0.60       # README quotes 0.35 nm
```

Documentation drifts from code silently; an assertion does not.

## Property tests for the pure modules

The no-Tk, no-hardware modules are cheap to test directly - edge cases first,
since those are where instrument data lives: all-NaN input, too few valid
points, a divisor at zero, a feature at the edge of its search window, an
out-of-order call to the state machine. Each should produce a defined result
rather than an exception.

For the state machine, inject the clock and step it:

```python
run = Run(protocol, now=lambda: t)
run.start(t)
assert run.tick(t + timedelta(seconds=600)) == []     # operator-gated: no auto-advance
run.advance(t + timedelta(seconds=190))
assert run.phase is Phase.REAGENT
```

## Layout check

Assertions do not catch a window where two panels overlap or a title is
clipped. Keep a script that drives the *visible* app through each state and
saves screenshots - run by hand, not in CI.

It needs an unlocked desktop: screen capture grabs whatever is actually on
screen, so a locked session silently yields pictures of the lock screen. If a
display is unavailable, `fig.savefig()` on each figure still verifies the plots
even though it says nothing about widget layout.

## Keep test output off the system drive

Sessions are large. Write to a path on the same volume as the project rather
than the OS temp directory, and use a fresh uniquely-named subdirectory per run
rather than deleting a fixed one - on Windows a previous interrupted run can
still hold a file open, and the resulting `PermissionError` fails the test for
a reason unrelated to the code.
