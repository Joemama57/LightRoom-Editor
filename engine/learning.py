"""Self-learning: what Match Look learns from every run, kept in ~/.matchlook/learning.json.

Two things are learned.

1. How Lightroom's sliders move the measurements (sensitivities). Every render
   pair in a run is an observation: "these sliders changed by du, the
   measurements changed by dm". They're pooled per file type and per camera
   into a ridge regression (pulled toward the built-in guess when there's
   little data), with older runs slowly forgotten. The next run starts its
   solver from these learned sensitivities instead of the guess, so it
   converges in fewer passes.

2. Your taste (preferences). When a matched photo is adjusted afterwards
   (by you in Lightroom, or by a nudge during review), the difference between
   what the engine chose and what was kept is a correction. Corrections are
   averaged per camera, file type and kind of light (warm / daylight / cool),
   and once a pattern has repeated, future matches apply it automatically,
   scaled by how consistent it has been.

    python3 -m engine.learning show
    python3 -m engine.learning reset [--preferences]
    python3 -m engine.learning restore --run ~/.matchlook/runs/<time>

Every run backs up learning.json to <run>/learning_before.json before it
changes it, so one run's learning can be undone with `restore`.
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import numpy as np

from .solver import CORRECTIVE, SLIDER_SCALE, _to_internal, _to_sliders, _metric_vector, default_prior

N = len(CORRECTIVE)
# Ridge strength: how many observations' worth of pull toward the built-in guess.
RIDGE = 3.0
# Each new run multiplies older evidence by this, so the model follows changes
# (a Lightroom update, a new camera profile) instead of averaging forever.
FORGET = 0.97
# Observations needed before learned sensitivities replace the guess.
MIN_SAMPLES = 6
# Ignore render pairs where nothing moved (in scaled slider units).
MIN_STEP = 0.02

# Preferences
PREF_ALPHA = 0.3  # weight of the newest correction in the running average
PREF_MIN_COUNT = 2  # corrections needed before a preference is applied
# Corrections bigger than this aren't taste, they're a different edit
# (a reset, a creative change): don't learn from them. Internal units.
PREF_MAX = np.array([60.0, 25.0, 1.5, 60.0, 60.0, 60.0, 60.0])
# Below this nothing was really changed. Internal units.
PREF_MIN = np.array([3.0, 1.0, 0.05, 3.0, 3.0, 3.0, 3.0])


def default_path():
    root = Path(os.environ.get("MATCHLOOK_HOME", Path.home() / ".matchlook"))
    return root / "learning.json"


def same_light(a, b, is_raw):
    """True when two sets of corrective sliders differ by no more than noise."""
    return not np.any(np.abs(_to_internal(a, is_raw) - _to_internal(b, is_raw)) >= PREF_MIN)


def light_bucket(sliders, is_raw):
    """Rough kind of light, from the photo's solved white balance."""
    if not is_raw:
        return "jpeg"
    k = float(sliders.get("Temperature", 5500))
    if k < 4000:
        return "warm light"
    if k > 6500:
        return "cool light"
    return "daylight"


def _fmt(is_raw):
    return "raw" if is_raw else "jpeg"


class Learner:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self.data = {"version": 1, "sensitivity": {}, "preference": {}, "runs": 0, "corrections": 0}
        if self.path.exists():
            try:
                self.data.update(json.loads(self.path.read_text()))
            except (OSError, ValueError):
                pass  # a corrupt file just means starting fresh

    def save(self, backup_to=None):
        """Write learning.json. backup_to: a file to copy the previous
        learning.json to first (once: an existing backup is kept), so this
        run's learning can be undone with `restore`."""
        if backup_to is not None and self.path.exists() and not Path(backup_to).exists():
            Path(backup_to).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.path, backup_to)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1))
        tmp.replace(self.path)

    def reset(self, preferences_only=False):
        """Forget everything, or (preferences_only) only the learned taste,
        keeping how Lightroom's sliders respond."""
        if preferences_only:
            self.data["preference"] = {}
            self.data["corrections"] = 0
        else:
            self.data = {"version": 1, "sensitivity": {}, "preference": {}, "runs": 0, "corrections": 0}
        self.save()

    def restore(self, backup):
        """Put a run's learning_before.json back; the current file is kept as
        learning.json.bak."""
        backup = Path(backup)
        json.loads(backup.read_text())  # refuse a corrupt backup
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_suffix(".json.bak"))
        shutil.copy2(backup, self.path)
        self.__init__(self.path)

    # -- sensitivities ---------------------------------------------------

    def _keys(self, camera, is_raw):
        return [f"{_fmt(is_raw)}|{camera or 'unknown'}", f"{_fmt(is_raw)}|*"]

    def observe_history(self, camera, is_raw, history):
        """Learn from one photo's renders: [{"sliders", "metrics"}, ...] in order."""
        xs = [_to_internal(h["sliders"], is_raw) / SLIDER_SCALE for h in history]
        ms = [_metric_vector(h["metrics"]) for h in history]
        pairs = [(xs[i] - xs[i - 1], ms[i] - ms[i - 1]) for i in range(1, len(xs))]
        pairs = [(du, dm) for du, dm in pairs if np.abs(du).max() >= MIN_STEP and np.all(np.isfinite(dm))]
        for key in self._keys(camera, is_raw):
            entry = self.data["sensitivity"].setdefault(
                key, {"A": np.zeros((N, N)).tolist(), "B": np.zeros((N, N)).tolist(), "samples": 0.0})
            A, B = np.array(entry["A"]), np.array(entry["B"])
            for du, dm in pairs:
                A += np.outer(du, du)
                B += np.outer(dm, du)
            entry["A"], entry["B"] = A.tolist(), B.tolist()
            entry["samples"] = entry["samples"] + len(pairs)
        return len(pairs)

    def end_run(self):
        """Fade older evidence a little; call once per run after observing it."""
        for entry in self.data["sensitivity"].values():
            entry["A"] = (np.array(entry["A"]) * FORGET).tolist()
            entry["B"] = (np.array(entry["B"]) * FORGET).tolist()
            entry["samples"] *= FORGET
        self.data["runs"] += 1

    def prior(self, camera, is_raw):
        """Learned sensitivities (internal units) for this camera/file type, or
        None while there isn't enough data. Returns (matrix, source)."""
        for key in self._keys(camera, is_raw):
            entry = self.data["sensitivity"].get(key)
            if entry and entry["samples"] >= MIN_SAMPLES:
                A, B = np.array(entry["A"]), np.array(entry["B"])
                P = default_prior(is_raw) * SLIDER_SCALE  # per scaled unit
                J = (B + RIDGE * P) @ np.linalg.inv(A + RIDGE * np.eye(N))
                return J / SLIDER_SCALE, key
        return None, None

    # -- preferences -------------------------------------------------------

    def _pref_key(self, camera, is_raw, bucket):
        return f"{_fmt(is_raw)}|{camera or 'unknown'}|{bucket}"

    def observe_correction(self, camera, is_raw, bucket, engine_sliders, kept_sliders,
                           source="lightroom", allow_zero=False):
        """Record that `engine_sliders` (what the match chose) became `kept_sliders`.

        allow_zero: also count "kept as is" when a preference already exists for
        this camera and light, so a preference you stop agreeing with fades.
        Returns "correction" (you changed it), "kept" (counted as agreement) or None.
        """
        delta = _to_internal(kept_sliders, is_raw) - _to_internal(engine_sliders, is_raw)
        key = self._pref_key(camera, is_raw, bucket)
        unchanged = not np.any(np.abs(delta) >= PREF_MIN)
        if np.any(np.abs(delta) > PREF_MAX):
            return None
        if unchanged and not (allow_zero and key in self.data["preference"]):
            return None
        entry = self.data["preference"].setdefault(key, {"mean": [0.0] * N, "spread": [0.0] * N, "count": 0})
        mean, spread = np.array(entry["mean"]), np.array(entry["spread"])
        if entry["count"] == 0:
            mean = delta
        else:
            spread = (1 - PREF_ALPHA) * spread + PREF_ALPHA * np.abs(delta - mean)
            mean = (1 - PREF_ALPHA) * mean + PREF_ALPHA * delta
        entry.update(mean=mean.tolist(), spread=spread.tolist(), count=entry["count"] + 1,
                     last=time.strftime("%Y-%m-%d"), last_source=source)
        if unchanged:
            return "kept"
        self.data["corrections"] += 1
        return "correction"

    def preference(self, camera, is_raw, bucket):
        """Learned correction (internal units) to apply, scaled by confidence, or None."""
        entry = self.data["preference"].get(self._pref_key(camera, is_raw, bucket))
        if not entry or entry["count"] < PREF_MIN_COUNT:
            return None
        mean, spread = np.array(entry["mean"]), np.array(entry["spread"])
        # More corrections and more consistent ones -> more confidence.
        confidence = entry["count"] / (entry["count"] + 2)
        consistency = 1.0 / (1.0 + spread / np.maximum(np.abs(mean), 1e-6))
        return mean * confidence * consistency

    def apply_preference(self, sliders, camera, is_raw, bucket):
        """Sliders with the learned preference added; (sliders, applied_delta_or_None)."""
        pref = self.preference(camera, is_raw, bucket)
        if pref is None or not np.any(np.abs(pref) >= PREF_MIN / 2):
            return sliders, None
        x = _to_internal(sliders, is_raw) + pref
        out = _to_sliders(x, is_raw)
        applied = {k: round(out[k] - float(sliders[k]), 2) for k in CORRECTIVE if abs(out[k] - float(sliders[k])) > 1e-9}
        return out, applied

    def summary(self):
        sens = {k: round(v["samples"], 1) for k, v in self.data["sensitivity"].items()}
        prefs = {}
        for k, v in self.data["preference"].items():
            fmt = k.split("|")[0]
            readable = {}
            for i, name in enumerate(CORRECTIVE):
                val = v["mean"][i]
                if abs(val) >= PREF_MIN[i] / 2:
                    if name == "Temperature" and fmt == "raw":
                        # mired: negative = warmer
                        readable[name] = f"{'warmer' if val < 0 else 'cooler'} by ~{abs(val):.0f} mired"
                    else:
                        readable[name] = round(val, 2)
            prefs[k] = {"corrections": v["count"], "average_change": readable,
                        "applied": v["count"] >= PREF_MIN_COUNT}
        return {"file": str(self.path), "runs_learned_from": self.data["runs"],
                "corrections_learned": self.data["corrections"],
                "sensitivity_samples": sens, "preferences": prefs}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Show or reset what Match Look has learned.")
    parser.add_argument("command", choices=["show", "reset", "restore"])
    parser.add_argument("--preferences", action="store_true", help="reset: forget only learned taste")
    parser.add_argument("--run", help="restore: the run folder whose learning to undo")
    args = parser.parse_args(argv)
    learner = Learner()
    if args.command == "reset":
        learner.reset(preferences_only=args.preferences)
        print(f"Reset {'preferences in ' if args.preferences else ''}{learner.path}")
    elif args.command == "restore":
        if not args.run:
            parser.error("restore needs --run")
        backup = Path(args.run).expanduser() / "learning_before.json"
        if not backup.exists():
            print(f"No backup in {args.run} (that run didn't change what was learned)", file=sys.stderr)
            return 1
        learner.restore(backup)
        print(f"Restored {learner.path} from {backup} (previous kept as {learner.path.with_suffix('.json.bak')})")
    else:
        print(json.dumps(learner.summary(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
