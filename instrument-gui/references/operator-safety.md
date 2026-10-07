# Designing for the operator

The person using this has gloves on, a pipette in one hand, and a sample that
may be consumed by the measurement. Design for that, not for a developer at a
desk.

## Irreversible runs

Some experiments cannot be repeated: a reagent is added, a film is written, a
sample is bleached. For these, the cost of a mistake is the whole sample, so
the usual "let them fix it afterwards" assumption does not hold.

### Pre-flight, once, before committing

Collect everything questionable and present it in one dialog with an explicit
"start anyway?". Not a sequence of nags - one list, so the operator can judge
the whole picture:

- missing calibration (no dark frame, no reference)
- a setting that contradicts another (reference loaded but the display set to
  raw counts, so the recorded derived quantity will not be the one expected)
- estimated data volume against free disk space
- empty sample name - the folder will be the only record of what this was
- anything the protocol machine needs that is not connected

State plainly that the action cannot be undone, and make "cancel" the easy
path. The dialog is cheap; the sample is not.

### Freeze what defines the run

At run start, snapshot the settings into `protocol.json`, then disable the
widgets that produced them, and have the running code read the snapshot rather
than the widgets:

```python
def _active_params(self):
    if self.run is not None and self.run.is_running():
        p = self.run.protocol          # the snapshot
        return {...}
    return self._params_from_form()    # idle: follow the form live
```

Two mechanisms for one goal, deliberately: disabling the widgets explains the
rule to the operator, and reading from the snapshot enforces it. Either alone
leaves a gap - a widget can be re-enabled by a code path you forgot, and a
silent snapshot leaves the operator wondering why their change did nothing.

Things that typically must freeze: acquisition settings that calibration
depends on, output directory, the choice of derived quantity, analysis-window
parameters, anything that becomes a column header.

### Abort keeps the data

Abort must write everything recorded so far, close the session cleanly, and
mark `status: aborted` with a reason. The distinction from a completed run is
data, not a nicety - a run aborted mid-phase has a truncated curve, and the
analysis has to know the tail is missing rather than flat.

Route every failure through the same abort path: a dead acquisition thread, a
communications timeout, a safety trip, the operator closing the window. The
alternative - a protocol machine that keeps advancing phases while nothing is
recording - produces files that look complete and are not.

## Hands-busy controls

### One button that does the next thing

When the protocol is a sequence, a single large button whose label and action
follow the current phase beats a row of buttons. The operator never has to
find the right one, and there is no wrong one to hit.

```
BASELINE        ->  [ ADD REAGENT - START  (F2) ]
REAGENT ADDING  ->  [ ADD REAGENT - DONE   (F2) ]
RELAXATION      ->  [ FINISH RUN NOW           ]
```

Bind a function key to the same handler. A hand already on the keyboard is
faster and steadier than one moving to a mouse, and the timestamp is the
measurement.

### Do not confirm a timestamp

If the point of a click is to record *when* something happened, do not put a
confirmation dialog between the click and the record. The dialog's latency
goes straight into the number you are trying to measure.

Confirm the things that are irreversible or destructive - finishing a run,
aborting, overwriting - and leave the timestamp marks immediate. One exception
worth making: if the operator marks an event implausibly early (before a
configured minimum baseline), ask - that is a plausible misclick with an
unrecoverable cost, and the time pressure is lower because it happens before
the event rather than during it.

### Free-text event marks

Give the operator a way to timestamp an annotation at any time without
changing phase: "bubble in the beam", "lid opened", "stirred". These end up in
`events.csv` and explain the artefact that otherwise takes an hour to work out
during analysis.

### Extending a phase

If a phase has a configured duration, offer to extend it while it is running.
A relaxation whose time constant was guessed wrong should not lose its tail
because the timer expired. Rewrite `protocol.json` when it happens so the file
matches what ran.

## Status that is always visible

Put phase, elapsed time, time remaining, and sample count in a header outside
any scrolling or resizable area. During a run the operator should be able to
answer "what is it doing and how long until I need to be back" from across the
room.

Colour-code only genuine danger states - an output energised, a heater on, a
protocol waiting for the operator. If everything is red, nothing is.

## Minimum durations, not deadlines

A configured phase duration is usually best read as a minimum. If the
operator is not ready when the baseline timer expires, keep recording and wait
for them - a longer baseline costs nothing and is often better. Advancing a
phase automatically while the operator is still preparing produces a run with a
boundary in the wrong place.

The inverse holds for phases that only a timer can end: those should advance on
their own and say so clearly.

## Persist the form

Save the form to a settings file on close and restore it on launch. Re-typing
fifteen fields before every run invites typos, and typos in an irreversible
experiment are expensive.

Do not restore anything with a shelf life. Calibration frames must be
re-acquired each session: a dark frame from yesterday is not a dark frame.
Parse the restored values defensively - a corrupted settings file must not
greet the operator with an error dialog before the window is even up.
