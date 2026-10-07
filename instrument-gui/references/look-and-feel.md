# Look and feel

An instrument panel is looked at for hours, often from arm's length, often in
a dim room. Legibility and calm beat decoration. Most "ugly Tkinter" is four
specific defects, all cheap to fix.

## The four defects and their fixes

**1. Stock ttk widgets.** The built-in themes (`clam`, `vista`, `aqua`) draw
check boxes as `X` in a square, tabs as grey slabs and buttons with 1990s
bevels. Fix: install `sv-ttk` (Sun Valley, Fluent-style assets, dark and
light, pure Python, one call). `gui/theme.py` from the scaffold uses it when
importable and falls back to a hand-styled `clam` when not, so the app still
runs on a machine without it. Alternatives with the same shape:
`ttkbootstrap` (Bootstrap palettes, many colours), `customtkinter` (not ttk;
its own widget set, so the scaffold's ttk code does not transfer).

```python
import sv_ttk
sv_ttk.set_theme("dark")        # before any widget is created
```

**2. Fonts that do not exist on this machine.** `("Segoe UI", 9)` is fine on
Windows and silently becomes Tk's default on Linux and macOS. Pick per
platform (`sys.platform`), as the scaffold's theme does, and keep one UI face
and one monospace face. Monospace for anything numeric that updates live
(readouts, elapsed time): proportional digits make a ticking counter jitter.

**3. The matplotlib toolbar.** `NavigationToolbar2Tk` is plain `tk`, ignores
ttk styles, and arrives light grey with black icons on your dark panel. It
has to be recoloured by hand, and the icon colour is taken from each button's
`foreground` at the moment matplotlib renders the icon, so set the colours and
then call `_set_image_for_button` again. `gui/widgets.py::style_mpl_toolbar`
does exactly this.

**4. No spacing system.** Widgets packed with the default `padx=0, pady=0`
look crowded; widgets with random padding look messy. Pick a small scale (2, 4, 8 px, as the
scaffold does) and use nothing off it. Group with a labelled section
header, not with `LabelFrame` borders, which add visual noise on a dark theme.

## Colour rules that matter for operators

- One accent colour for "the thing that is happening now" (recording,
  current phase). One red, reserved for danger: output energised, heater on,
  abort. If everything is coloured, nothing is.
- Disabled state must be visibly different from enabled, and frozen inputs
  during a run should look frozen, not merely refuse keystrokes.
- Contrast: body text at least 4.5:1 against its panel (the scaffold's
  `#e8ecf1` on `#1a212b` is about 12:1). Secondary text can drop to ~4:1.
- Plot background matches the panel so the figure does not look pasted in.
  `apply_mpl_theme()` sets the rcParams once; nothing in the redraw path
  should touch colours.

## Layout rules for a measurement window

- Status header across the top: state word, elapsed clocks, the one next
  action button, the abort button at the far right. Readable from across
  the room.
- Controls in a narrow left column, collapsible by section; plots take the
  rest. Plots are what the operator watches; controls are what they set
  once.
- The window must not change shape during a run. No widgets appearing or
  disappearing when a phase changes: change text and colour instead.

## When Tk is not enough

If the requirement is dockable panels, many simultaneous fast plots, or
image streams above ~10 fps, Tk + matplotlib is the wrong toolkit and no
theme will fix it. See `references/qt.md`.
