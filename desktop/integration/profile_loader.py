"""Open the existing five-source app and load the authorized profile only.

This launcher does not call a receiver, status endpoint, or video endpoint.
It keeps the existing GUI source and its normal explicit-connect workflow.
"""
from pathlib import Path
from datetime import datetime
import hashlib
import json
from PySide6.QtCore import QTimer
import sprint_app
from pose_display_bridge import display_class

PROFILE = None
EXPECTED_SHA = None
EVIDENCE = Path(__file__).resolve().parent / "state" / "profile_loaded.json"
BaseMonitor = display_class()


class ConfigMonitor(BaseMonitor):
    def __init__(self):
        super().__init__()
        self.profile_load_attempts = 0
        QTimer.singleShot(500, self.load_authorized_profile)

    def load_authorized_profile(self):
        try:
            # Let the existing local snapshot loaders finish before calling
            # the app's guard, which rejects profile changes during any input.
            self.profile_load_attempts += 1
            for tile in self.slots.values():
                if tile.spec.kind == "snapshot" and tile.receiver.active:
                    tile.receiver.disconnect()
            if any(tile.receiver.active for tile in self.slots.values()):
                if self.profile_load_attempts < 20:
                    QTimer.singleShot(250, self.load_authorized_profile)
                    return
                raise ValueError("Local snapshot loader did not become idle")
            digest = hashlib.sha256(PROFILE.read_bytes()).hexdigest()
            if digest != EXPECTED_SHA:
                raise ValueError("Configuration SHA256 mismatch")
            self.load_profiles(PROFILE)
            self.choose("gray")
            assert not self.started_inputs
            assert not self.slots["gray"].receiver.active
            self.setWindowTitle(self.windowTitle() + " [CONFIG LOADED " + self.profiles["gray"]["session_id"][:8] + "]")
            evidence = {
                "result": "CONFIG LOADED",
                "loaded_at": datetime.now().astimezone().isoformat(),
                "config_path": str(PROFILE),
                "config_sha256": digest,
                "profile": self.profiles["gray"],
                "started_inputs": list(self.started_inputs),
                "gray_receiver_active": self.slots["gray"].receiver.active,
                "video_gets_by_this_launcher": 0,
                "status_gets_by_this_launcher": 0,
                "waiting_for_board_start": True,
            }
            EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
            EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(evidence, ensure_ascii=False), flush=True)
        except Exception as exc:
            self.log_message("CONFIG LOAD FAILED: " + str(exc))
            print("CONFIG LOAD FAILED: " + repr(exc), flush=True)


if __name__ == "__main__":
    sprint_app.SprintMonitor = ConfigMonitor
    raise SystemExit(sprint_app.main())
