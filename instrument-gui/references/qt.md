# Building the same app in Qt

Everything in this skill except the widget code is toolkit-independent: the
threading contract, the driver/simulator twin, the phase machine, the session
writer, the headless test strategy. This file is the mapping from the Tk
patterns to PyQt6 / PySide6, for when Qt is the right choice.

## When Qt is the right choice

- Dockable or detachable panels, several plots the operator rearranges.
- Live image streams or more than a few fast traces: `pyqtgraph` draws
  from the GPU-friendly QGraphicsView and is an order of magnitude faster
  than matplotlib for live updates.
- The lab already has Qt apps and the users expect that look.
- Otherwise Tk wins on zero dependencies and the fact that it is already on
  every lab PC. "Nicer-looking" alone is not a reason: see
  `references/look-and-feel.md`.

## Pattern mapping

| Tk pattern (this skill) | Qt equivalent |
|---|---|
| Worker `threading.Thread` owning the driver | Same. `threading.Thread` is fine in Qt; `QThread` is optional and buys nothing for a blocking-driver loop |
| `queue.Queue` from worker to GUI | Keep the queue, or emit a `pyqtSignal` from the worker: Qt queues the emission onto the GUI thread (`Qt.QueuedConnection`, automatic across threads). The queue is easier to test headlessly; the signal is less code. Never touch a widget from the worker in either case |
| `root.after(interval, drain)` drain loop | `QTimer(interval)` connected to `drain()`. Same bounded-drain rule: pop at most N items per tick, drop frames rather than fall behind |
| `tk.StringVar` / `BooleanVar` | No equivalent. Read widget values directly; for headless tests expose setters on the window class |
| `ttk.Notebook` | `QTabWidget`; `QDockWidget` for detachable panels |
| `ttk.Style` + `sv_ttk` | A `.qss` stylesheet, or `qdarktheme` / `qt-material` packages. Dark theme is one `app.setStyleSheet(...)` |
| `FigureCanvasTkAgg` + `NavigationToolbar2Tk` | `FigureCanvasQTAgg` + `NavigationToolbar2QT` (same matplotlib API, toolbar inherits the stylesheet), or `pyqtgraph.PlotWidget` for speed |
| `root.update()` bounded pump in tests | `QApplication.processEvents()` in a bounded loop, or `QTest.qWait(ms)` |
| `xvfb-run` for headless CI | `QT_QPA_PLATFORM=offscreen` environment variable; no X server needed |
| Freezing inputs during a run | `widget.setEnabled(False)`; `QGroupBox.setEnabled` freezes a whole section |
| Keyboard shortcuts (F2 next step, F4 mark) | `QShortcut(QKeySequence("F2"), window, callback)` |
| Protocol / Logging / Math modules | Unchanged. They import neither Tk nor Qt and that is the point |

## Minimal worker-to-GUI wiring in Qt

```python
class Window(QMainWindow):
    def __init__(self, driver):
        super().__init__()
        self.data_q, self.status_q = queue.Queue(), queue.Queue()
        self.worker = AcquisitionWorker(driver, params, self.data_q, self.status_q)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain)
        self.timer.start(50)                      # ms; same drain budget as Tk

    def _drain(self):
        for _ in range(MAX_PER_TICK):
            try:
                sample = self.data_q.get_nowait()
            except queue.Empty:
                break
            self._handle(sample)                  # log, derive, update plot
```

This keeps the worker module byte-for-byte identical between the Tk and Qt
apps, which is the strongest argument for the queue over signals.

## Live plotting with pyqtgraph

```python
self.plot = pg.PlotWidget()
self.curve = self.plot.plot(pen=pg.mkPen("#4da3ff", width=1.5))
...
self.curve.setData(x, y)          # in _drain, throttled exactly as in Tk
```

`setData` on an existing curve is the Qt analogue of `line.set_data` plus
`draw_idle`: never create a new plot item per frame. For images use
`pg.ImageItem.setImage(arr, autoLevels=False)` and set levels from
percentiles yourself, for the same reason as the heat-map rule in
`references/live-plotting.md`.

## Headless test

```python
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
app = QApplication.instance() or QApplication([])
win = Window(SimulatedSpectrometer())
win.start_run(protocol)
deadline = time.monotonic() + 30
while win.run_active and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.01)
assert not win.run_active, "run did not finish"
assert (session_dir / "run_summary.json").exists()
```

The bounded loop is the same idiom as `references/testing.md`; an unbounded
`app.exec()` in a test hangs exactly like an unbounded `root.mainloop()`.

## Scaffold status

`scripts/scaffold.py` generates Tk only. The non-GUI files it writes
(`instruments/`, `*_protocol.py`, `*_logging.py`, `tests/` minus the pump)
are reusable as-is under Qt; write the Qt window against them using the
mapping above.
