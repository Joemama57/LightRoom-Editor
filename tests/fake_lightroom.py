"""An in-memory stand-in for the Lightroom bridge, rendering with the simulator."""

import numpy as np
from PIL import Image

from tests.simulator import render


class FakeLightroom:
    def __init__(self, photos, active):
        """photos: {id: {"raw": array, "fileName", "fileFormat", "cameraModel", "settings"}}"""
        self.photos = photos
        self.active = active
        self.snapshots = []
        self.labels = {}
        self.renders = 0
        self.rejected_keys = set()  # keys this "Lightroom" silently ignores
        self.mask_calls = []

    def ping(self):
        return {"version": "fake"}

    def get_selection(self):
        return {
            "active": self.active,
            "photos": [
                {"id": pid, **{k: v for k, v in p.items() if k != "raw"}, "settings": dict(p["settings"])}
                for pid, p in self.photos.items()
            ],
        }

    def apply_settings(self, items):
        not_taken = []
        for item in items:
            settings = self.photos[item["id"]]["settings"]
            for key, value in item["settings"].items():
                if key in self.rejected_keys:
                    not_taken.append({"id": item["id"], "key": key, "wanted": value, "got": settings.get(key)})
                else:
                    settings[key] = value
        return {"applied": len(items), "not_taken": not_taken}

    def render(self, items, size=1024):
        for item in items:
            p = self.photos[item["id"]]
            settings = p["settings"]
            if p.get("fileFormat") not in ("RAW", "DNG"):
                # JPEG Temperature is a -100..100 offset (+ = warmer), roughly a mired each.
                settings = dict(settings, Temperature=1e6 / (1e6 / 5500 - float(settings.get("Temperature", 0.0))))
            img = render(p["raw"], settings)
            Image.fromarray((img * 255).round().astype(np.uint8)).save(item["path"], quality=95)
            self.renders += 1
        return [item["path"] for item in items]

    def get_settings(self, items):
        return [{"id": i["id"], "fileName": self.photos[i["id"]].get("fileName"),
                 "settings": dict(self.photos[i["id"]]["settings"])}
                for i in items if i["id"] in self.photos]

    def mask_adjust(self, photo_id, kind, values):
        settings = self.photos[photo_id]["settings"]
        corrections = settings.setdefault("MaskGroupBasedCorrections", [])
        name = f"Match Look {kind}"
        ours = next((c for c in corrections if c.get("CorrectionName") == name), None)
        created = ours is None
        if created:
            ours = {"CorrectionName": name}
            corrections.append(ours)
        for key, value in values.items():
            ours[key] = value if key == "LocalExposure2012" else value / 100
        self.mask_calls.append((photo_id, kind, dict(values)))
        return {"created": created, "found": True, "values": dict(values)}

    def snapshot(self, items):
        self.snapshots.extend((i["id"], i["name"], dict(self.photos[i["id"]]["settings"])) for i in items)
        return {"created": len(items)}

    def set_label(self, items):
        for item in items:
            self.labels[item["id"]] = item["label"]
        return {"labeled": len(items)}
