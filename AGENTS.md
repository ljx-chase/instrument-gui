# AGENTS.md

## Purpose

This repository contains **instrument-gui**, a reusable instruction set for
writing Python applications that drive laboratory instruments and run timed
measurement protocols, plus a scaffold that generates a runnable skeleton.

This file is the entrypoint for agents: how to install the skill, and how to
behave once it is loaded. The specification itself is
`instrument-gui/SKILL.md`.

## Installation

If a user has asked you to install this skill, do the following. The skill
root is the directory `instrument-gui/`, which contains `SKILL.md` at its top
level plus `references/`, `scripts/` and `evals/`. Keep that structure
intact; `SKILL.md` refers to the other files relative to its own location.

Pick the case that matches your environment.

**Claude Code.** Copy `instrument-gui/` into `~/.claude/skills/` for all
projects, or into `.claude/skills/` in the current repository for this
project only. Equivalent one-liner if `npx` is available:

```bash
npx skills add ljx-chase/instrument-gui -g
```

**Claude web, Desktop, or Cowork.** These do not read `~/.claude/skills/`.
Tell the user to download the zip from the repository's releases and upload
it under Customize, then Skills. The zip must contain a single folder named
`instrument-gui` with `SKILL.md` inside it, not a bare `SKILL.md` and not
the whole repository.

**Codex or another repository-aware agent.** Clone this repository into the
workspace and keep this `AGENTS.md` at the workspace root. You will load
`SKILL.md` when the entrypoint below applies.

**Cursor, OpenCode, Gemini CLI, or any other agent with a skills
directory.** Copy `instrument-gui/` into whatever directory that agent reads
skills from, preserving the folder structure.

**Anything else.** Use `instrument-gui/SKILL.md` directly as the instruction
file. The scaffold needs a Python interpreter; without one, follow the
architecture in `references/architecture.md` by hand.

After installing, verify with a request that should trigger it, for example
"write me a GUI to control our spectrometer with live plotting". A correct
response runs `scripts/scaffold.py`, confirms the generated app runs in
simulate mode and its headless test passes, and only then turns to the real
driver. If it starts writing a window class from a blank file, the skill did
not load.

## Agent entrypoint

When a request involves building, extending or debugging software that
controls lab hardware or records a timed measurement protocol:

1. Read `instrument-gui/SKILL.md` first.
2. **Check scope.** The skill is for software-timed (millisecond and up),
   open-loop measurement apps with one or a few instruments. For
   hardware-timed acquisition, feedback control, or a large multi-instrument
   facility, say so and build on a framework (bluesky, PyMoDAQ, pymeasure)
   while keeping the skill's non-negotiables.
3. **Scaffold before hand-writing.** Run `scripts/scaffold.py`, confirm
   `--simulate` runs and the generated test passes, then replace the `TODO`
   stubs with the named instrument's real protocol, citing its
   documentation. Do not ship the placeholder driver or simulator as done.
4. Load a file from `instrument-gui/references/` when building that part,
   not all up front. `SKILL.md` carries the index.
5. Work through the "Before calling it done" checklist in `SKILL.md`
   before reporting completion, and state explicitly anything on it that
   is not satisfied.
6. Preserve the user's language unless they request another.
