# Live plotting

Drawing is almost always the most expensive thing an instrument GUI does per
sample. Treat it as a budget to be spent deliberately.

## Separate the data rate from the draw rate

Drain the queue often so nothing backs up; redraw rarely because nobody can
read a plot refreshing at 100 Hz:

```python
POLL_INTERVAL_MS = 50     # drain + log + update buffers
PLOT_INTERVAL_MS = 250    # redraw

if samples and (time.time() - self._last_plot_t) * 1000 >= self.PLOT_INTERVAL_MS:
    self._last_plot_t = time.time()
    self._refresh_plots(samples[-1])      # the latest sample, not each one
```

Update the buffers for every sample; draw only the most recent.

## Draw only what is on screen

With a tabbed plot area, only one tab is visible. Rendering all of them costs
several hundred milliseconds per pass to draw figures nobody is looking at.

```python
def _visible_canvas(self):
    idx = self.notebook.index(self.notebook.select())
    return self._canvases[idx] if 0 <= idx < len(self._canvases) else None

def _draw(self, canvas):
    if canvas is self._visible_canvas():
        canvas.draw_idle()

self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
```

Keep every buffer current regardless, so switching tabs renders immediately
from data already in hand. For a genuinely expensive panel - a large image -
skip building its array at all unless its tab is visible.

This is not micro-optimisation. Four figures at ~150 ms each is 600 ms per
redraw; at a 250 ms target the loop never catches up, the queue grows, and the
app degrades into a freeze.

## Keep artists, set data

Create lines and images once; afterwards call `set_data`. Clearing and
replotting rebuilds every artist each frame and leaks memory through
accumulated references.

```python
(self.line,) = self.ax.plot([], [], lw=1.0)      # once, at build time
...
self.line.set_data(x, y)                          # per redraw
self.ax.relim(); self.ax.autoscale_view()
```

For images, `set_data` plus `set_extent` plus `set_clim`.

Reserve room for a title you set later. `tight_layout()` at build time runs
before any title exists, so a title added per redraw gets clipped. Use explicit
margins instead - `subplots_adjust(top=0.90, ...)` - rather than re-running
`tight_layout()` on every frame.

## Decimate

A line with 100 000 points renders slowly and shows nothing a few thousand
would not:

```python
@staticmethod
def _decimate(x, y, limit=3000):
    if len(x) <= limit:
        return x, y
    step = int(np.ceil(len(x) / limit))
    return x[::step], y[::step]
```

Same for the other axis of a waterfall: subsample a 3648-point spectrum to
~900 columns for display. The full-resolution data is on disk untouched; this
affects only pixels.

Keep rolling buffers bounded (`MAX_ROWS = 600`) and say so in the UI, so nobody
believes the plot is the dataset.

## Colour scales

Use percentiles, not min/max:

```python
vmin, vmax = np.nanpercentile(arr, [1.0, 99.5])
if not (vmax > vmin):
    vmin, vmax = np.nanmin(arr), np.nanmax(arr)
```

A handful of pixels where a calibration divisor is near zero can read several
orders of magnitude high. On a min/max scale those pixels own the entire range
and flatten the real signal into one shade - a plot that looks broken for a
reason that has nothing to do with the sample.

## NaN is the right answer sometimes

Where a derived quantity is undefined - dividing by a reference pixel with no
signal, the log of a non-positive number - propagate NaN rather than clipping
to a sentinel:

```python
out = np.full(shape, np.nan)
valid = denom > MIN_SIGNAL
out[valid] = numer[valid] / denom[valid]
```

Clipping invents a feature exactly where the instrument has the least
information, and it is indistinguishable from data downstream. NaN is honest
and Matplotlib renders it as a gap.

Consequences to handle: use `np.nanmean`/`np.nanmax` throughout; expect
`RuntimeWarning: Mean of empty slice` from all-NaN regions and suppress it
locally with a comment explaining which region it is; and compute autoscale
limits from finite values within the visible range only.

## Autoscale on what is shown

Autoscaling on the full array when the view is zoomed to a subrange gives
limits driven by data off screen:

```python
lo, hi = self.ax.get_xlim()
m = (x >= lo) & (x <= hi) & np.isfinite(y)
if m.any():
    ymin, ymax = float(np.min(y[m])), float(np.max(y[m]))
    pad = 0.06 * (ymax - ymin) if ymax > ymin else max(abs(ymax), 1.0) * 0.1
    self.ax.set_ylim(ymin - pad, ymax + pad)
```

## Mark events on the time axis

Vertical lines at phase boundaries and operator marks turn a trace into a
story. Add the artist once when the mark happens, not on every redraw, and
keep references so they can be cleared:

```python
self._marker_artists.append(ax.axvline(t, color=c, ls="--", lw=1.0))
```

Stagger labels in rotation (three offsets is usually enough) - marks seconds
apart otherwise print on top of each other, which is exactly when they matter.
On an image whose time axis is vertical, use `axhline`.

## Give the operator the numbers

A plot answers "is it changing". A monospaced readout answers "by how much":

```
lambda_peak =  524.71 nm   amplitude = 0.2431   shift +0.62 nm
```

Include the shift from the pre-event baseline. That is the quantity the
experiment is about, and seeing it live is what tells the operator whether the
run is working.

## Be explicit about live-versus-final numbers

A live readout is usually a fast approximation; the publishable number comes
from a fit done offline. Say so in the UI and in the documentation, quantify
the difference if you can measure it against a simulated ground truth, and name
which one belongs in a paper. A live estimate quietly treated as the
measurement is a real way to publish a wrong number.
