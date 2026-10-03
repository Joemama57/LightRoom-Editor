"""Replay a saved run on its own photos, without Lightroom.

Every run folder keeps the previews Lightroom rendered while solving (iter_0,
iter_1, ... and nudges/), with the sliders each was rendered at (the report's
`trace`). From those, each photo gets an *emulator*: a per-pixel model of how
its preview changes with Temperature, Tint, Exposure and the tone sliders,
fitted to the renders Lightroom actually made. The current engine can then be
run again on the same photos (`ReplayBridge` stands in for Lightroom), so a
change to the engine is measured on real photos, not only on the simulator.

    python -m engine.replay check RUN      how well the emulator reproduces Lightroom (leave-one-out)
    python -m engine.replay rerun RUN      run today's engine on RUN's photos; writes RUN/replay/<time>/
    python -m engine.replay answers RUN    save the photos' current Lightroom settings as RUN/answers.json
                                           (after fixing a few of them by hand: the answer key)
    python -m engine.replay bench RUN...   score the original run and a replay against the answer keys

Limits: only white balance, exposure, the four tone sliders and Vibrance/
Saturation (roughly) are emulated, and only within about the range the run's
renders covered. Learning is never used or changed by a replay.
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .colorspace import delta_e_2000, linear_to_srgb, srgb_to_lab, srgb_to_linear
from .solver import CORRECTIVE, SLIDER_SCALE, _to_internal, _to_sliders

EMU_EDGE = 512  # long edge of emulated previews (the engine measures at 512 too)
EPS = 1e-3  # keeps log() finite in the blacks
# Pull of each slope toward the pooled one (in renders' worth): with 5-7 renders
# per photo, a slider that barely moved is taken mostly from the other photos.
RIDGE = 0.5
LOO_STRIDE = 4  # every 4th pixel when scoring
# Fallback slopes of log(linear RGB) per scaled slider unit (SLIDER_SCALE) for
# a slider no photo of the run moved. Raw: a higher mired is a cooler setting.
_DEFAULT_SLOPES_RAW = {
    "Temperature": np.array([-0.0035, 0.0, 0.005]) * SLIDER_SCALE[0],
    "Tint": np.array([0.0, -0.004, 0.0]) * SLIDER_SCALE[1],
    "Exposure2012": np.full(3, np.log(2.0)) * SLIDER_SCALE[2],
}
_DEFAULT_SLOPES_JPEG = dict(_DEFAULT_SLOPES_RAW, Temperature=-_DEFAULT_SLOPES_RAW["Temperature"])
EMULATED = set(CORRECTIVE) | {"Vibrance", "Saturation"}


def _local(run_dir, path):
    """A path written on another machine, inside this run folder."""
    p = Path(path)
    if p.exists():
        return p
    parts = p.parts
    if run_dir.name in parts:
        i = len(parts) - 1 - parts[::-1].index(run_dir.name)
        return run_dir.joinpath(*parts[i + 1:])
    return run_dir / p.name


def _safe(p):
    stem = Path(p.get("fileName") or p["id"]).stem
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in stem) + f"_{p['id']}"


def load_report(run_dir):
    return json.loads((Path(run_dir) / "report.json").read_text())


def photo_renders(run_dir, p):
    """[(sliders, path)] for every saved preview of photo `p` whose sliders are known."""
    run_dir = Path(run_dir)
    out = []
    for k, row in enumerate(p.get("trace") or []):
        path = run_dir / f"iter_{k}" / f"{_safe(p)}.jpg"
        if path.exists():
            out.append(({**p["start"], **{key: row[key] for key in CORRECTIVE if key in row}}, path))
    nudges = p.get("nudges") or []
    if nudges and all(set(n) <= set(CORRECTIVE) for n in nudges):
        files = sorted((run_dir / "nudges").glob(f"*_photo_{_safe(p)}.jpg"))
        if len(files) == len(nudges):
            total = {k: sum(float(n.get(k, 0.0)) for n in nudges) for k in CORRECTIVE}
            sliders = {k: float(p["final"][k]) - total[k] for k in CORRECTIVE}
            for n, path in zip(nudges, files):
                sliders = {k: sliders[k] + float(n.get(k, 0.0)) for k in CORRECTIVE}
                out.append((dict(sliders), path))
    return out


def _load(path, size=None):
    with Image.open(path) as im:
        im = im.convert("RGB")
        if size is None:
            im.thumbnail((EMU_EDGE, EMU_EDGE))
        else:
            im = im.resize(size, Image.BILINEAR)
        return np.asarray(im, dtype=np.float64) / 255.0


class Emulator:
    """How one photo's preview responds to the corrective sliders, fitted to its renders.

    log(linear RGB) of every pixel is modelled as linear in the sliders
    (mired for raw Temperature): exact for exposure before the tone curve and
    for white balance as channel gains, and close enough after them within
    the range the renders cover.
    """

    def __init__(self, renders, is_raw, prior=None, ridge=RIDGE):
        if not renders:
            raise ValueError("no saved previews for this photo")
        self.is_raw = is_raw
        first = _load(renders[0][1])
        self.shape = first.shape
        size = (first.shape[1], first.shape[0])
        imgs = [first] + [_load(path, size) for _, path in renders[1:]]
        self.x0 = _to_internal(renders[0][0], is_raw)
        self.X = np.array([(_to_internal(s, is_raw) - self.x0) / SLIDER_SCALE for s, _ in renders])
        self.Y = np.log(srgb_to_linear(np.stack(imgs)).reshape(len(imgs), -1, 3) + EPS)
        self.varied = np.ptp(self.X, axis=0) > 1e-6 if len(renders) > 1 else np.zeros(len(CORRECTIVE), bool)
        self.fit(prior, ridge)

    def fit(self, prior=None, ridge=RIDGE, rows=None):
        rows = np.arange(len(self.X)) if rows is None else np.asarray(rows)
        prior = np.zeros((len(CORRECTIVE), 3)) if prior is None else np.asarray(prior, dtype=float)
        D = np.hstack([np.ones((len(rows), 1)), self.X[rows]])
        lam = np.diag([0.0] + [ridge] * len(CORRECTIVE))
        M = np.linalg.pinv(D.T @ D + lam)
        self.beta = np.empty((1 + len(CORRECTIVE), self.Y.shape[1], 3))
        for c in range(3):
            target = D.T @ self.Y[rows, :, c] + lam[:, 1:] @ np.outer(prior[:, c], np.ones(self.Y.shape[1]))
            self.beta[:, :, c] = M @ target
        return self

    def slopes(self):
        """Mean slope per slider and channel (rows CORRECTIVE)."""
        return self.beta[1:].mean(axis=1)

    def predict(self, sliders):
        x = (_to_internal({**_to_sliders(self.x0, self.is_raw), **{k: v for k, v in sliders.items()
                                                                  if k in CORRECTIVE}}, self.is_raw) - self.x0)
        d = np.concatenate([[1.0], x / SLIDER_SCALE])
        log_lin = np.tensordot(d, self.beta, axes=(0, 0))
        lin = np.clip(np.exp(log_lin) - EPS, 0.0, 1.0)
        return linear_to_srgb(lin).reshape(self.shape)

    def leave_one_out(self, prior=None, ridge=RIDGE):
        """Mean ΔE00 of each render predicted from the others (render 0 stays as the base)."""
        errors = []
        for k in range(1, len(self.X)):
            keep = [i for i in range(len(self.X)) if i != k]
            self.fit(prior, ridge, rows=keep)
            pred = self.predict(_to_sliders(self.x0 + self.X[k] * SLIDER_SCALE, self.is_raw))
            actual = linear_to_srgb(np.exp(self.Y[k]) - EPS).reshape(self.shape)
            errors.append(image_delta_e(pred, actual))
        self.fit(prior, ridge)
        return errors


def image_delta_e(a, b, stride=LOO_STRIDE):
    """Mean CIEDE2000 between two same-size sRGB images (pixel by pixel)."""
    la = srgb_to_lab(np.asarray(a).reshape(-1, 3)[::stride])
    lb = srgb_to_lab(np.asarray(b).reshape(-1, 3)[::stride])
    return float(np.mean(delta_e_2000(la, lb)))


def _default_prior(is_raw):
    slopes = _DEFAULT_SLOPES_RAW if is_raw else _DEFAULT_SLOPES_JPEG
    return np.array([slopes.get(k, np.zeros(3)) for k in CORRECTIVE])


def build_emulators(run_dir, report):
    """{photo id: Emulator} for every photo with saved previews. Each slider's
    slope is pulled toward the run's pooled slope (same file type), so a slider
    that one photo never moved is still emulated sensibly."""
    run_dir = Path(run_dir)
    raw = {p["id"]: Emulator(r, p["is_raw"]) for p in report["photos"] if (r := photo_renders(run_dir, p))}
    by_id = {p["id"]: p for p in report["photos"]}
    for is_raw in (True, False):
        group = [e for pid, e in raw.items() if by_id[pid]["is_raw"] == is_raw]
        prior = _default_prior(is_raw)
        for j in range(len(CORRECTIVE)):
            seen = [e.slopes()[j] for e in group if e.varied[j]]
            if seen:
                prior[j] = np.median(seen, axis=0)
        for e in group:
            e.prior = prior
            e.fit(prior)
    return raw


def _chroma(img, d_vibrance, d_saturation):
    """Rough Vibrance/Saturation change in Lab (as tests/simulator.py renders it)."""
    if not d_vibrance and not d_saturation:
        return img
    from .colorspace import lab_to_srgb
    lab = srgb_to_lab(img.reshape(-1, 3))
    c = np.hypot(lab[:, 1], lab[:, 2])
    f = (1 + d_vibrance / 100 * 0.8 * np.exp(-c / 40)) * (1 + d_saturation / 100 * 0.6)
    lab[:, 1:] *= f[:, None]
    return lab_to_srgb(lab).reshape(img.shape)


def _infer_reference_corrective(report):
    ref = report["reference"]
    if ref.get("corrective"):
        return {k: v for k, v in ref["corrective"].items() if v is not None}
    for p in report["photos"]:
        if not p["wb_from_camera"] and "wb_offset_from_reference" not in p["flags"]:
            return dict(p["start"])
    return dict(report["photos"][0]["start"])


def _camera_wb(report, p):
    """The As Shot reading a photo started from (its start, less the reference's offset)."""
    if "wb_offset_from_reference" in p["flags"] and report.get("reference_wb_offset"):
        off = report["reference_wb_offset"]
        x = _to_internal(p["start"], True)
        x[0], x[1] = x[0] - off["mired"], x[1] - off["tint"]
        s = _to_sliders(x, True)
        return {"Temperature": s["Temperature"], "Tint": s["Tint"]}
    return {"Temperature": p["start"]["Temperature"], "Tint": p["start"]["Tint"]}


class ReplayBridge:
    """Stands in for Lightroom: the photos of a saved run, rendered by their emulators."""

    def __init__(self, run_dir, report, emulators=None):
        self.run_dir = Path(run_dir)
        self.report = report
        self.emulators = emulators if emulators is not None else build_emulators(self.run_dir, report)
        ref = report["reference"]
        self.ref_id = ref["id"]
        self.ref_preview = _local(self.run_dir, ref["preview"])
        look = dict(ref.get("creative_look") or {})
        self.look = look
        cameras = [p["camera"] for p in report["photos"] if "same_shoot" in p["flags"]] or \
                  [p["camera"] for p in report["photos"]]
        ref_camera = ref.get("cameraModel") or max(set(cameras), key=cameras.count)
        ref_raw = Path(ref["fileName"]).suffix.lower() not in (".jpg", ".jpeg", ".heic", ".png", ".tif", ".tiff")
        self.photos = {self.ref_id: {
            "id": self.ref_id, "fileName": ref["fileName"], "fileFormat": "RAW" if ref_raw else "JPG",
            "cameraModel": ref_camera, "captureTime": ref.get("captureTime", 0.0),
            "settings": {**look, **_infer_reference_corrective(report),
                         "WhiteBalance": ref.get("white_balance") or "Custom"},
        }}
        for p in report["photos"]:
            if p["id"] not in self.emulators:
                continue
            settings = {**look, **{k: float(v) for k, v in p["before"].items()}, "WhiteBalance": "Custom"}
            if p["wb_from_camera"]:
                settings.update(_camera_wb(report, p), WhiteBalance="As Shot")
            when = p.get("captureTime")
            if when is None:
                when = 0.0 if "same_shoot" in p["flags"] else None
            self.photos[p["id"]] = {
                "id": p["id"], "fileName": p["fileName"], "fileFormat": "RAW" if p["is_raw"] else "JPG",
                "cameraModel": p["camera"], "captureTime": when, "settings": settings,
            }
        self.renders = 0

    def ping(self):
        return {"version": "replay"}

    def get_selection(self):
        return {"active": self.ref_id,
                "photos": [{**p, "settings": dict(p["settings"])} for p in self.photos.values()]}

    def apply_settings(self, items):
        for item in items:
            self.photos[item["id"]]["settings"].update(item["settings"])
        return {"applied": len(items), "not_taken": []}

    def render(self, items, size=1024):
        for item in items:
            if item["id"] == self.ref_id:
                shutil.copyfile(self.ref_preview, item["path"])
                continue
            s = self.photos[item["id"]]["settings"]
            img = self.emulators[item["id"]].predict(s)
            img = _chroma(img, float(s.get("Vibrance", 0) or 0) - float(self.look.get("Vibrance", 0) or 0),
                          float(s.get("Saturation", 0) or 0) - float(self.look.get("Saturation", 0) or 0))
            Image.fromarray((np.clip(img, 0, 1) * 255).round().astype(np.uint8)).save(item["path"], quality=95)
            self.renders += 1
        return [item["path"] for item in items]

    def get_settings(self, items):
        return [{"id": i["id"], "fileName": self.photos[i["id"]]["fileName"],
                 "settings": dict(self.photos[i["id"]]["settings"])} for i in items if i["id"] in self.photos]

    def snapshot(self, items):
        return {"created": len(items)}

    def set_label(self, items):
        return {"labelled": len(items)}

    def mask_adjust(self, photo_id, kind, values):
        return {"created": False, "found": False, "values": dict(values)}


def rerun(run_dir, out_dir=None, emulators=None, log=lambda m: None, **options):
    """Run today's engine on a saved run's photos. Options default to the run's own."""
    from .workflow import run_match

    run_dir = Path(run_dir)
    report = load_report(run_dir)
    opts = report.get("options") or {}
    if report.get("baked_reference") or report.get("grade_fit") or report.get("style_grade"):
        raise ValueError("this run fitted or read a grade; a replay can only emulate a copied look")
    bridge = ReplayBridge(run_dir, report, emulators)
    out_dir = Path(out_dir) if out_dir else run_dir / "replay" / time.strftime("%Y%m%d-%H%M%S")
    kwargs = dict(skin=opts.get("skin", False), color_only=opts.get("color_only", False),
                  look=opts.get("look", True), strength=report.get("strength", 1.0),
                  tolerance=report.get("tolerance", 2.0), size=report.get("size", 1024),
                  **{k: v for k, v in (opts.get("subject") or {}).items()
                     if k in ("face_skin", "skin_error", "skin_wb")})
    kwargs.update(options)
    return run_match(bridge, out_dir, learner=None, log=log, label="", **kwargs), bridge


def answers(run_dir, report=None):
    """The answer key: {fileName: sliders} from RUN/answers.json (the user's own fixes),
    else from the review nudges (the final settings after them), marked by `source`."""
    run_dir = Path(run_dir)
    path = run_dir / "answers.json"
    if path.exists():
        return {k: v for k, v in json.loads(path.read_text()).items() if not k.startswith("_")}, "your own edits"
    report = report or load_report(run_dir)
    key = {p["fileName"]: dict(p["final"]) for p in report["photos"]
           if p.get("nudges") and all(set(n) <= set(CORRECTIVE) for n in p["nudges"])}
    return key, "review nudges"


def _before_nudges(p):
    nudges = p.get("nudges") or []
    return {k: float(p["final"][k]) - sum(float(n.get(k, 0.0)) for n in nudges) for k in CORRECTIVE}


def score(emulators, report, finals, key):
    """{fileName: ΔE00 of `finals[fileName]` against the answer key}, both emulated."""
    by_name = {p["fileName"]: p for p in report["photos"]}
    out = {}
    for name, answer in key.items():
        p = by_name.get(name)
        if not p or p["id"] not in emulators or name not in finals:
            continue
        e = emulators[p["id"]]
        out[name] = image_delta_e(e.predict({**p["final"], **finals[name]}), e.predict({**p["final"], **answer}))
    return out


def shoot_spread(report, finals):
    """Spread (SD, mired) of white balance across the run's same-shoot raws."""
    mireds = [1e6 / float(finals[p["fileName"]]["Temperature"]) for p in report["photos"]
              if p["is_raw"] and "same_shoot" in p["flags"] and p["fileName"] in finals]
    return float(np.std(mireds)) if len(mireds) >= 2 else None


def bench(run_dirs, log=print, **options):
    """Score each run as it ran and as today's engine re-runs it (with `options`,
    e.g. hold_shoot_wb=True), against its answer key."""
    rows = []
    for run_dir in map(Path, run_dirs):
        report = load_report(run_dir)
        emulators = build_emulators(run_dir, report)
        key, source = answers(run_dir, report)
        as_run = {p["fileName"]: (_before_nudges(p) if source == "review nudges" else p["final"])
                  for p in report["photos"]}
        replayed, _ = rerun(run_dir, run_dir / "replay" / "bench", emulators, **options)
        now = {p["fileName"]: p["final"] for p in replayed["photos"]}
        a, b = score(emulators, report, as_run, key), score(emulators, report, now, key)
        row = {"run": run_dir.name, "answers": source, "photos": len(key),
               "as_run": a, "replay": b,
               "as_run_mean": round(float(np.mean(list(a.values()))), 2) if a else None,
               "replay_mean": round(float(np.mean(list(b.values()))), 2) if b else None,
               "shoot_spread_mired": {"as_run": _round(shoot_spread(report, as_run)),
                                      "replay": _round(shoot_spread(report, now))}}
        rows.append(row)
        log(f"{run_dir.name}: ΔE to {source} ({len(key)} photos) {row['as_run_mean']} as run → "
            f"{row['replay_mean']} now; white-balance spread {row['shoot_spread_mired']['as_run']} → "
            f"{row['shoot_spread_mired']['replay']} mired")
    return rows


def _round(v, n=1):
    return None if v is None else round(v, n)


def check(run_dir):
    report = load_report(run_dir)
    emulators = build_emulators(run_dir, report)
    rows = []
    for p in report["photos"]:
        e = emulators.get(p["id"])
        if e is None:
            rows.append({"photo": p["fileName"], "renders": 0})
            continue
        loo = e.leave_one_out(getattr(e, "prior", None))
        rows.append({"photo": p["fileName"], "renders": len(e.X),
                     "moved": [k for k, v in zip(CORRECTIVE, e.varied) if v],
                     "loo_delta_e": [round(v, 2) for v in loo],
                     "loo_max": round(max(loo), 2) if loo else None})
    return rows


def save_answers(bridge, run_dir):
    """Write the run's photos' current Lightroom settings to RUN/answers.json."""
    run_dir = Path(run_dir)
    report = load_report(run_dir)
    names = {p["id"]: p["fileName"] for p in report["photos"]}
    current = bridge.get_settings([{"id": pid} for pid in names])
    key = {}
    for c in current:
        if c.get("fileName") != names.get(c["id"]):
            continue
        s = c["settings"]
        if s.get("IncrementalTemperature") is not None and not report_is_raw(report, c["id"]):
            s = {**s, "Temperature": s["IncrementalTemperature"], "Tint": s.get("IncrementalTint", s.get("Tint"))}
        key[names[c["id"]]] = {k: float(s[k]) for k in CORRECTIVE if s.get(k) is not None}
    key["_note"] = "Answer key: these photos as you finished them by hand. Delete a photo's line to leave it out."
    (run_dir / "answers.json").write_text(json.dumps(key, indent=1))
    return key


def report_is_raw(report, pid):
    return next((p["is_raw"] for p in report["photos"] if p["id"] == pid), True)


def _subject_args(parser):
    from .workflow import _subject_args as add
    add(parser)


def _subject_options(args):
    """Only the flags given, so a rerun otherwise keeps the run's own options."""
    from .workflow import subject_kwargs
    return {k: v for k, v in subject_kwargs(args).items() if v}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay saved Match Look runs without Lightroom.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, text in (("check", "how closely the emulator reproduces Lightroom's renders"),
                       ("rerun", "run today's engine on a run's photos"),
                       ("answers", "save the run's photos as they are now in Lightroom as its answer key")):
        sub.add_parser(name, help=text).add_argument("run")
    b = sub.add_parser("bench", help="score runs against their answer keys")
    b.add_argument("runs", nargs="+")
    for cmd in (b, sub.choices["rerun"]):
        cmd.add_argument("--hold-shoot-wb", action="store_true", help="try the shoot white-balance hold")
        cmd.add_argument("--even-shoot-tone", dest="even_shoot_tone", action="store_true", default=True,
                         help="even the shoot's brightness (the default)")
        cmd.add_argument("--no-even-shoot-tone", dest="even_shoot_tone", action="store_false",
                         help="replay without evening the shoot's brightness")
        cmd.add_argument("--face-tone", action="store_true",
                         help="try brightening shoot frames toward the reference's faces (turns on --face-skin)")
        _subject_args(cmd)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "check":
            print(json.dumps(check(Path(args.run)), indent=1))
        elif args.cmd == "rerun":
            run_dir = Path(args.run)
            before = load_report(run_dir)
            report, _ = rerun(run_dir, log=lambda m: print(m, file=sys.stderr), hold_shoot_wb=args.hold_shoot_wb,
                              even_shoot_tone=args.even_shoot_tone, face_tone=args.face_tone, **_subject_options(args))
            old = {p["fileName"]: p for p in before["photos"]}
            rows = [{"photo": p["fileName"],
                     "as_run": {k: old[p["fileName"]]["final"][k] for k in ("Temperature", "Tint", "Exposure2012")},
                     "now": {k: p["final"][k] for k in ("Temperature", "Tint", "Exposure2012")},
                     "flags_now": p["flags"]} for p in report["photos"]]
            print(json.dumps({"replay": str(Path(report["contact_sheet"]).parent), "photos": rows}, indent=1))
        elif args.cmd == "answers":
            from .bridge import Bridge
            print(json.dumps(save_answers(Bridge(), Path(args.run)), indent=1))
        else:
            print(json.dumps(bench(args.runs, log=lambda m: print(m, file=sys.stderr), hold_shoot_wb=args.hold_shoot_wb,
                                   even_shoot_tone=args.even_shoot_tone, face_tone=args.face_tone, **_subject_options(args)), indent=1))
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
