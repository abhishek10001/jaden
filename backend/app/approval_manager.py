import threading
import time
from typing import Dict, Any, Optional, Callable


class ApprovalManager:
    """Thread-safe manager for orchestrating Human-in-the-Loop approval gates."""

    def __init__(self):
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def register_run(self, run_id: str):
        with self._lock:
            self._runs[run_id] = {
                "event": threading.Event(),
                "decision": None,  # Dict with 'approved': bool, 'feedback': Optional[str]
                "status": "pending",
                "registered_at": time.time(),
            }

    def wait_for_decision(
        self,
        run_id: str,
        timeout_sec: int = 900,
        on_tick: Optional[Callable[[float], None]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Blocks until a decision is submitted or timeout expires.

        Periodically calls on_tick (e.g. to emit heartbeats on the SSE stream).
        """
        event: Optional[threading.Event] = None
        with self._lock:
            if run_id in self._runs:
                event = self._runs[run_id]["event"]

        if not event:
            return None

        elapsed = 0.0
        tick_interval = 2.0
        while elapsed < timeout_sec:
            if event.wait(timeout=tick_interval):
                with self._lock:
                    run_info = self._runs.get(run_id)
                    return run_info.get("decision") if run_info else None

            elapsed += tick_interval
            if on_tick:
                try:
                    on_tick(elapsed)
                except Exception:
                    pass

        return None  # Timed out

    def submit_decision(self, run_id: str, approved: bool, feedback: Optional[str] = None) -> bool:
        """Called by the API endpoint when a user approves or requests changes."""
        with self._lock:
            run_info = self._runs.get(run_id)
            if not run_info:
                return False

            run_info["decision"] = {
                "approved": bool(approved),
                "feedback": feedback.strip() if feedback else None,
            }
            run_info["status"] = "approved" if approved else "rejected"
            run_info["event"].set()
            return True

    def reset_for_retry(self, run_id: str):
        """Resets the event for this run_id if looping back to coder after rejection."""
        with self._lock:
            if run_id in self._runs:
                self._runs[run_id]["event"].clear()
                self._runs[run_id]["decision"] = None
                self._runs[run_id]["status"] = "pending"

    def cleanup_run(self, run_id: str):
        with self._lock:
            self._runs.pop(run_id, None)


approval_manager = ApprovalManager()

