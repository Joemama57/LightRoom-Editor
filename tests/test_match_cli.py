import json

import numpy as np
from PIL import Image

from engine.match import main
from tests.simulator import capture, make_scene, render

REF_SLIDERS = {"Temperature": 5500, "Tint": 0, "Exposure2012": 0, "Shadows2012": 0,
               "Highlights2012": 0, "Whites2012": 0, "Blacks2012": 0}


def save(img, path):
    Image.fromarray((img * 255).round().astype(np.uint8)).save(path)


def test_cli_loop_until_done(tmp_path):
    save(render(capture(make_scene(seed=0, size=128), 5500), REF_SLIDERS), tmp_path / "ref.png")
    raw = capture(make_scene(seed=1, size=128), 3200, 0, -1.0)

    sliders, history, ref = REF_SLIDERS, [], {"preview": str(tmp_path / "ref.png")}
    for step in range(10):
        preview = tmp_path / f"t_{step}.png"
        save(render(raw, sliders), preview)
        job = {
            "reference": ref,
            "options": {"tolerance": 2.0},
            "targets": [{"id": "t1", "preview": str(preview), "sliders": sliders,
                         "is_raw": True, "history": history}],
        }
        (tmp_path / "job.json").write_text(json.dumps(job))
        assert main(["--job", str(tmp_path / "job.json"), "--out", str(tmp_path / "out.json")]) == 0
        result = json.loads((tmp_path / "out.json").read_text())

        assert set(result["reference"]["metrics"]) >= {"a", "b", "L"}
        (t,) = result["targets"]
        assert set(t) == {"id", "sliders", "done", "residual", "best_residual",
                          "iterations", "flags", "history"}
        ref = result["reference"]  # reuse measured metrics instead of re-reading the file
        sliders, history = t["sliders"], t["history"]
        if t["done"]:
            break

    assert t["done"]
    assert t["best_residual"] < 2.0
    assert t["flags"] == []
    assert abs(t["sliders"]["Exposure2012"] - 1.0) < 0.3


def test_cli_flags_blown_out_photo(tmp_path, capsys):
    save(render(capture(make_scene(seed=0), 5500), REF_SLIDERS), tmp_path / "ref.png")
    save(np.ones((64, 64, 3)), tmp_path / "white.png")
    job = {"reference": {"preview": str(tmp_path / "ref.png")},
           "options": {"max_iterations": 0},
           "targets": [{"id": "w", "preview": str(tmp_path / "white.png"),
                        "sliders": REF_SLIDERS, "is_raw": True}]}
    (tmp_path / "job.json").write_text(json.dumps(job))
    main(["--job", str(tmp_path / "job.json")])
    (t,) = json.loads(capsys.readouterr().out)["targets"]
    assert t["done"]
    assert "mostly_clipped" in t["flags"]
    assert "not_converged" in t["flags"]
