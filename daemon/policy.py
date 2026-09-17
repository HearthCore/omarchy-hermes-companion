"""Decides whether an unsolicited remark may be spoken right now."""
from __future__ import annotations

import time
from dataclasses import dataclass

from perception import Frame, mic_in_use, screen_shared


@dataclass
class PolicyConfig:
    min_gap_seconds: float = 300.0     # floor between unprompted remarks (non-urgent)
    urgent_gap_seconds: float = 60.0   # floor for urgent remarks
    max_per_hour: int = 8
    quiet_when_idle_seconds: float = 600.0  # user away -> do not talk to an empty room


class SpeechPolicy:
    def __init__(self, cfg: PolicyConfig | None = None):
        self.cfg = cfg or PolicyConfig()
        self._history: list[float] = []

    def may_speak(self, frame: Frame, urgency: str, muted: bool) -> tuple[bool, str]:
        now = time.time()
        if muted:
            return False, "muted"
        if frame.window.fullscreen:
            return False, "fullscreen"
        if frame.idle_seconds > self.cfg.quiet_when_idle_seconds:
            return False, "user idle"
        if mic_in_use():
            return False, "mic in use (call/meeting)"
        if screen_shared():
            return False, "screen shared"
        self._history = [t for t in self._history if now - t < 3600]
        if len(self._history) >= self.cfg.max_per_hour and urgency != "urgent":
            return False, "hourly cap"
        last = self._history[-1] if self._history else 0.0
        gap = self.cfg.urgent_gap_seconds if urgency == "urgent" else self.cfg.min_gap_seconds
        if now - last < gap:
            return False, f"cooldown ({int(gap - (now - last))}s left)"
        return True, "ok"

    def record(self):
        self._history.append(time.time())
