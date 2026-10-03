import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from engine import replay
from engine.workflow import run_match, run_nudge
from tests.fake_lightroom import FakeLightroom
from tests.simulator import capture, make_scene, render
from tests.test_workflow import REF_SETTINGS, photo


def quiet(_):
    pass


LIGHTS = {"tung": (3200, 0, -0.5), "shade": (7500, 5, 0.3), "under": (5500, 0, -1.5)}


@pytest.fixture
def run(tmp_path):
    other = make_scene(seed=1)[4:, 2:]
    lr = FakeLightroom(
        {"ref": photo(capture(make_scene(seed=0), 5500), "DSC0001.ARW", settings=dict(REF_SETTINGS)),
         **{pid: photo(capture(other, *light), f"DSC000{n + 2}.ARW") for n, (pid, light) in enumerate(LIGHTS.items())}},
        active="ref",
    )
    run_dir = tmp_path / "20261003-202615"
    report = run_match(lr, run_dir, log=quiet)
    return lr, run_dir, report


def _truth(lr, pid, sliders, shape):
    img = render(lr.photos[pid]["raw"], {**lr.photos[pid]["settings"], **sliders})
    im = Image.fromarray((img * 255).round().astype(np.uint8)).resize((shape[1], shape[0]))
    return np.asarray(im, dtype=np.float64) / 255.0


def test_paths_written_on_the_mac_are_found_in_the_run_folder(tmp_path):
    run_dir = tmp_path / "20261003-202615"
    mac = "/Users/someone/.matchlook/runs/20261003-202615/iter_2/DSC00143_1.jpg"
    assert replay._local(run_dir, mac) == run_dir / "iter_2" / "DSC00143_1.jpg"


def test_the_emulator_reproduces_lightroom_at_the_solved_sliders(run):
    lr, run_dir, report = run
    emulators = replay.build_emulators(run_dir, report)
    assert set(emulators) == set(LIGHTS)
    for p in report["photos"]:
        e = emulators[p["id"]]
        assert replay.image_delta_e(e.predict(p["final"]), _truth(lr, p["id"], p["final"], e.shape)) < 3.0


def test_the_emulator_reports_how_well_it_predicts_renders_it_did_not_see(run):
    _, run_dir, _ = run
    rows = replay.check(run_dir)
    assert all(r["renders"] >= 2 and r["loo_max"] < 6.0 for r in rows)


def test_a_replay_runs_todays_engine_without_lightroom_or_learning(run, tmp_path, monkeypatch):
    monkeypatch.setenv("MATCHLOOK_HOME", str(tmp_path / "home"))
    _, run_dir, report = run
    saved = (run_dir / "report.json").read_text()
    again, bridge = replay.rerun(run_dir, out_dir=tmp_path / "replay")
    assert [p["fileName"] for p in again["photos"]] == [p["fileName"] for p in report["photos"]]
    assert (tmp_path / "replay" / "contact_sheet.jpg").exists() and bridge.renders > 0
    assert again["options"]["learning"] is False
    assert (run_dir / "report.json").read_text() == saved  # the original run is left as it was
    for old, new in zip(report["photos"], again["photos"]):
        assert abs(new["final"]["Exposure2012"] - old["final"]["Exposure2012"]) < 0.5


def test_the_bench_scores_runs_against_their_answer_key(run):
    lr, run_dir, report = run
    names = {p["id"]: p["fileName"] for p in report["photos"]}
    key = {names[pid]: {"Temperature": k, "Tint": t, "Exposure2012": -ev} for pid, (k, t, ev) in LIGHTS.items()}
    (run_dir / "answers.json").write_text(json.dumps({**key, "_note": "by hand"}))
    emulators = replay.build_emulators(run_dir, report)
    assert all(v < 1e-6 for v in replay.score(emulators, report, key, key).values())
    (row,) = replay.bench([run_dir], log=quiet)
    assert row["answers"] == "your own edits" and row["photos"] == 3
    assert row["as_run_mean"] is not None and row["replay_mean"] is not None


def test_review_nudges_are_a_fallback_answer_key_and_more_renders(run):
    lr, run_dir, report = run
    p = next(p for p in report["photos"] if p["id"] == "shade")
    before = len(replay.photo_renders(run_dir, p))
    run_nudge(lr, run_dir, "DSC0003.ARW", {"Temperature": 300.0})
    report = replay.load_report(run_dir)
    p = next(p for p in report["photos"] if p["id"] == "shade")
    key, source = replay.answers(run_dir, report)
    assert source == "review nudges" and key == {"DSC0003.ARW": p["final"]}
    renders = replay.photo_renders(run_dir, p)
    assert len(renders) == before + 1 and renders[-1][0]["Temperature"] == p["final"]["Temperature"]


def _beach(tmp_path, hold):
    """A shoot of three re-matched raws (no longer As Shot) under the reference's
    light, mostly sand: its own solve takes the sand for a cast."""
    photos = {"ref": photo(capture(make_scene(seed=0), 5500), "DSC0001.ARW", settings=dict(REF_SETTINGS))}
    photos["ref"]["captureTime"] = 0.0
    for n, share in enumerate((0.3, 0.5, 0.7)):
        scene = make_scene(seed=n + 1)[4:, 2:].copy()
        scene[int(scene.shape[0] * (1 - share)):] = [0.42, 0.36, 0.27]
        photos[f"t{n}"] = photo(capture(scene, 5500), f"DSC000{n + 2}.ARW",
                                settings={"WhiteBalance": "Custom", "Temperature": 4545, "Tint": 20, "Exposure2012": 0})
        photos[f"t{n}"]["captureTime"] = 600.0 * (n + 1)
    return run_match(FakeLightroom(photos, active="ref"), tmp_path / "beach", hold_shoot_wb=hold, log=quiet)


def test_the_bench_shows_the_shoot_hold_beating_the_old_solve_on_a_beach(tmp_path):
    report = _beach(tmp_path, hold=False)  # the run as the engine made it before the hold
    run_dir = tmp_path / "beach"
    truth = {"Temperature": 5500, "Tint": 0.0}  # the light the reference was set for
    (run_dir / "answers.json").write_text(json.dumps({p["fileName"]: dict(truth, Exposure2012=p["final"]["Exposure2012"])
                                                      for p in report["photos"]}))
    (row,) = replay.bench([run_dir], log=quiet, hold_shoot_wb=True)
    assert row["replay_mean"] < row["as_run_mean"]
    assert row["shoot_spread_mired"]["replay"] < 1.0
