# Learnings

Judgement the colour-grading agent has picked up from real runs. It is reference
material for the agent (Claude running `/match-look`), not code: nothing in
`engine/` imports it, and the agent works as before if this folder is missing
or empty.

## Two stores, one system

| Store | Holds | Owner | How it is used |
|---|---|---|---|
| `~/.matchlook/learning.json` | Numbers: slider responses per camera and file type, and averaged preferences | The engine (`engine/learning.py`) | Applied automatically by the solver on every run |
| `learnings/` (this folder) | Judgement: what worked, what failed, edge cases, how far to trust a number | The agent | Read before a run, applied when choosing options, reviewing and reporting |

Rules that keep them consistent:

- A fact lives in one store only. Slider values the engine learned stay in
  `learning.json`; an entry here points at them (for example "see
  `learning.json` → `preference` → `raw|iPhone 16 Pro Max|cool light`") and
  never copies the numbers.
- When a finding explains or contradicts a learned value, the entry says so and
  names the key.
- `learning.json` is read only through `PY -m engine.learning show`. It is never
  edited directly and `engine.learning reset` is never run. The normal commands
  (match, nudge, learn) update it as designed.
- It is copied to `~/.matchlook/learning.json.bak` before each match.
- Stale, conflicting or wrong learned values are reported to the user with
  evidence. The agent does not resolve them.

## What may be recorded

Only findings backed by a measurable result (an error, a slider value, a
measured colour, a flag, a count) or by something the user said. An impression
that cannot be tied to either stays out.

## How the agent uses this folder

1. **Before a run:** read this file, always read [failures.md](failures.md),
   then open only the topic files that fit the job (the table below says when).
   Run `PY -m engine.learning show`.
2. **During the run:** let the relevant takeaways steer the options, the review
   and the nudges. They inform judgement inside the skill's steps; they do not
   replace any step or limit.
3. **After the run:** add new findings to the right topic file, newest first,
   update the table below if a file was added, and say in the report which
   entries were applied and added.

## Topic files

| File | Open it when |
|---|---|
| [failures.md](failures.md) | Always: process mistakes that corrupted results or learning |
| [grade-fit.md](grade-fit.md) | The reference is an exported copy with its edit baked in |
| [white-balance.md](white-balance.md) | The set has JPEGs, or warmth or tint looks off |
| [exposure.md](exposure.md) | A photo is flagged `tone_limited` or `mostly_clipped`, or `--color-only` is in play |
| [color-hsl.md](color-hsl.md) | Reviewing colour families: sky, walls, saturation |
| [mixed-scenes.md](mixed-scenes.md) | The selection mixes light (day and sunset, exterior and interior) or file types |
| [skin-tones.md](skin-tones.md) | People are in frame, or the report carries skin notes |
| [nudges.md](nudges.md) | Before making any review nudge |
| [evaluation.md](evaluation.md) | Reading error numbers and flags; writing the report |

## Entry format

```
## YYYY-MM-DD — Short title
Source: run folder and photo, session, or commit
Finding: what was measured or said, with the numbers that matter
Takeaway: what to do next time
```

Every entry is dated, cites its source run or test image, and ends with an
actionable takeaway. Run folders are under `~/.matchlook/runs/`. If a later run
contradicts an entry, edit the entry and say what changed; do not leave both.
