"""Client for the Match Look Lightroom plugin.

The plugin and this client talk through JSON files in a shared folder:
requests go into `inbox/`, replies come back in `outbox/` under the same name.
Files are written to a temp name and renamed, so neither side ever reads a
half-written file.

    python3 -m engine.bridge ping
    python3 -m engine.bridge selection
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

DEFAULT_DIR = Path.home() / ".matchlook" / "bridge"


class BridgeError(RuntimeError):
    pass


class Bridge:
    def __init__(self, root=None, timeout=120.0, poll=0.1):
        self.root = Path(root or os.environ.get("MATCHLOOK_BRIDGE_DIR", DEFAULT_DIR))
        self.inbox = self.root / "inbox"
        self.outbox = self.root / "outbox"
        self.timeout = timeout
        self.poll = poll

    def call(self, command, timeout=None, **params):
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.outbox.mkdir(parents=True, exist_ok=True)
        req_id = uuid.uuid4().hex
        tmp = self.inbox / f"{req_id}.tmp"
        tmp.write_text(json.dumps({"id": req_id, "command": command, "params": params}))
        tmp.rename(self.inbox / f"{req_id}.json")

        reply_path = self.outbox / f"{req_id}.json"
        deadline = time.monotonic() + (timeout or self.timeout)
        while time.monotonic() < deadline:
            if reply_path.exists():
                reply = json.loads(reply_path.read_text())
                reply_path.unlink()
                if not reply.get("ok"):
                    raise BridgeError(f"{command} failed in Lightroom: {reply.get('error')}")
                return reply.get("result")
            time.sleep(self.poll)

        # Don't leave a stale request for the plugin to run later.
        (self.inbox / f"{req_id}.json").unlink(missing_ok=True)
        raise BridgeError(
            f"No reply from Lightroom to '{command}' after {timeout or self.timeout:.0f}s. "
            "Is Lightroom Classic open with the Match Look plugin enabled? "
            "(Library > Plug-in Extras > Match Look Bridge Status)"
        )

    # Thin wrappers so callers (and the fake Lightroom in tests) share one interface.
    def ping(self):
        return self.call("ping", timeout=5)

    def get_selection(self):
        return self.call("get_selection")

    def apply_settings(self, items):
        """items: [{"id": ..., "settings": {...}}]"""
        return self.call("apply_settings", items=items)

    def render(self, items, size=1024):
        """items: [{"id": ..., "path": "/abs/out.jpg"}]; returns the same paths."""
        return self.call("render", items=items, size=size, timeout=60 + 10 * len(items))

    def snapshot(self, items):
        """items: [{"id": ..., "name": ...}]"""
        return self.call("snapshot", items=items)

    def set_label(self, items):
        """items: [{"id": ..., "label": "yellow"}]"""
        return self.call("set_label", items=items)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Talk to the Match Look Lightroom plugin.")
    parser.add_argument("command", choices=["ping", "selection"])
    args = parser.parse_args(argv)
    bridge = Bridge()
    try:
        result = bridge.ping() if args.command == "ping" else bridge.get_selection()
    except BridgeError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
