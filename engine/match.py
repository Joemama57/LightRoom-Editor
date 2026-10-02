"""Command-line entry point the Lightroom plugin calls once per loop iteration.

    python3 -m engine.match --job job.json --out result.json

job.json:
    {
      "reference": {"preview": "ref.jpg"}            # or {"metrics": {...}} from a previous result
      "options": {"tolerance": 2.0, "max_iterations": 6},
      "targets": [
        {
          "id": "photo-uuid",
          "preview": "t1.jpg",                       # rendered with "sliders" below
          "sliders": {"Temperature": 5500, ...},     # corrective sliders used for that render
          "is_raw": true,
          "history": []                              # pass back the history from the last result
        }
      ]
    }

result.json has the reference metrics and, per target, the next "sliders" to
render (or the final ones when "done" is true), the updated "history",
"residual" (match error of this render), "best_residual" and "flags".
"""

import argparse
import json
import sys

from .guards import flags_for
from .measure import Metrics, measure_file
from .solver import propose


def run_job(job):
    ref_spec = job["reference"]
    if "metrics" in ref_spec:
        ref = Metrics.from_dict(ref_spec["metrics"])
    else:
        ref = measure_file(ref_spec["preview"])
    options = job.get("options", {})
    tolerance = float(options.get("tolerance", 2.0))
    max_iterations = int(options.get("max_iterations", 6))

    results = []
    for target in job["targets"]:
        metrics = measure_file(target["preview"])
        history = list(target.get("history", []))
        history.append({"sliders": target["sliders"], "metrics": metrics.to_dict()})
        p = propose(
            ref,
            history,
            is_raw=bool(target.get("is_raw", True)),
            tolerance=tolerance,
            max_iterations=max_iterations,
        )
        results.append(
            {
                "id": target["id"],
                "sliders": p.sliders,
                "done": p.done,
                "residual": round(p.residual, 3),
                "best_residual": round(p.best_residual, 3),
                "iterations": p.iterations,
                "flags": flags_for(metrics, p, tolerance),
                "history": history,
            }
        )
    return {"reference": {"metrics": ref.to_dict()}, "targets": results}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Match corrective sliders to a reference photo.")
    parser.add_argument("--job", required=True, help="path to job.json")
    parser.add_argument("--out", help="where to write result.json (default: stdout)")
    args = parser.parse_args(argv)

    with open(args.job) as f:
        job = json.load(f)
    result = run_job(job)
    text = json.dumps(result, indent=2)
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
