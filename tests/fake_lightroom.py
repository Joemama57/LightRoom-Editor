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
        for item in items:
            self.photos[item["id"]]["settings"].update(item["settings"])
        return {"applied": len(items)}

    def render(self, items, size=1024):
        for item in items:
            p = self.photos[item["id"]]
            img = render(p["raw"], p["settings"])
            Image.fromarray((img * 255).round().astype(np.uint8)).save(item["path"], quality=95)
            self.renders += 1
        return [item["path"] for item in items]

    def snapshot(self, items):
        self.snapshots.extend((i["id"], i["name"], dict(self.photos[i["id"]]["settings"])) for i in items)
        return {"created": len(items)}

    def set_label(self, items):
        for item in items:
            self.labels[item["id"]] = item["label"]
        return {"labeled": len(items)}
