import os
import json
import time
import threading
from typing import Dict, Any, List, Optional
from datetime import datetime

try:
    import pymongo
    from pymongo.errors import PyMongoError
    PYMONGO_AVAILABLE = True
except ImportError:
    PYMONGO_AVAILABLE = False


class DatabaseManager:
    """Manages run history, checkpoints, and event streams with MongoDB persistence

    Features automatic fallback to an in-memory and local disk JSON cache when MongoDB
    is offline, guaranteeing zero downtime and seamless browser refresh survival.
    """

    def __init__(self):
        self.uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        self.db_name = os.getenv("MONGODB_DB_NAME", "jaden_sdlc")
        self.client = None
        self.db = None
        self.is_connected = False
        self._lock = threading.Lock()

        # In-memory and local disk fallback cache
        self._memory_runs: Dict[str, Dict[str, Any]] = {}
        self._memory_events: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".run_cache")
        self._cache_file = os.path.join(self._cache_dir, "runs.json")

        self._load_disk_cache()
        self._connect()

    def _connect(self):
        """Attempts connection to MongoDB."""
        if not PYMONGO_AVAILABLE:
            print("[Database] pymongo not installed; using persistent local cache.")
            return

        try:
            client = pymongo.MongoClient(self.uri, serverSelectionTimeoutMS=2000)
            client.admin.command("ping")
            self.client = client
            self.db = client[self.db_name]
            self.is_connected = True

            # Ensure indexes
            self.db.runs.create_index("run_id", unique=True)
            self.db.runs.create_index([("updated_at", pymongo.DESCENDING)])
            self.db.events.create_index([("run_id", pymongo.ASCENDING), ("timestamp", pymongo.ASCENDING)])
            print(f"[Database] Successfully connected to MongoDB at {self.uri.split('@')[-1]} (DB: {self.db_name})")
        except Exception as e:
            self.is_connected = False
            print(f"[Database] MongoDB not reachable ({e}). Operating in resilient disk-backed mode.")

    def _load_disk_cache(self):
        """Loads persistent runs from local JSON cache if present."""
        try:
            if os.path.exists(self._cache_file):
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._memory_runs = data.get("runs", {})
                    self._memory_events = data.get("events", {})
        except Exception as e:
            print(f"[Database] Warning reading disk cache: {e}")

    def _save_disk_cache(self):
        """Persists memory state to disk cache."""
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump({
                    "runs": self._memory_runs,
                    "events": self._memory_events
                }, f, default=str)
        except Exception:
            pass

    def create_or_update_run(self, run_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Initializes or updates a run record."""
        now = time.time()
        with self._lock:
            existing = self._memory_runs.get(run_id, {})
            merged = {**existing, **data, "run_id": run_id, "updated_at": now}
            if "created_at" not in merged:
                merged["created_at"] = now
            self._memory_runs[run_id] = merged
            self._save_disk_cache()

            if self.is_connected:
                try:
                    self.db.runs.update_one(
                        {"run_id": run_id},
                        {"$set": merged},
                        upsert=True
                    )
                except Exception as e:
                    print(f"[Database] MongoDB update error: {e}")

            return merged

    def append_event(self, run_id: str, event_type: str, data: Dict[str, Any]):
        """Records an SSE stream event for replay upon browser refresh."""
        now = time.time()
        record = {
            "run_id": run_id,
            "event": event_type,
            "data": data,
            "timestamp": now
        }
        with self._lock:
            if run_id not in self._memory_events:
                self._memory_events[run_id] = []
            self._memory_events[run_id].append(record)

            # Update run's current state and summary fields
            run = self._memory_runs.get(run_id, {})
            run["updated_at"] = now

            if event_type == "agent_activity":
                run["active_agent"] = data.get("agent")
                run["last_action"] = data.get("action")
            elif event_type == "architect_plan":
                run["architect_plan"] = data
            elif event_type == "files_generated":
                run["files"] = data.get("files", [])
            elif event_type == "qa_result":
                run["qa_result"] = data
            elif event_type == "preview_ready":
                run["preview_url"] = data.get("preview_url") or data.get("previewUrl")
                run["pr_url"] = data.get("pr_url") or data.get("prUrl")
                run["pr_number"] = data.get("pr_number") or data.get("prNumber")
            elif event_type == "approval_required":
                run["status"] = "awaiting_approval"
                run["approval_data"] = data
            elif event_type == "release_complete":
                run["status"] = "completed"
                run["live_url"] = data.get("live_url") or data.get("liveUrl")
                run["production_merged"] = data.get("production_merged") or data.get("productionMerged")
            elif event_type == "error":
                run["status"] = "failed"
                run["error_message"] = data.get("error")
            elif event_type == "done":
                if run.get("status") not in ["awaiting_approval", "failed"]:
                    run["status"] = "completed"

            self._memory_runs[run_id] = run
            self._save_disk_cache()

            if self.is_connected:
                try:
                    self.db.events.insert_one(record.copy())
                    self.db.runs.update_one(
                        {"run_id": run_id},
                        {"$set": run},
                        upsert=True
                    )
                except Exception as e:
                    print(f"[Database] MongoDB event append error: {e}")

    def save_checkpoint(self, run_id: str, node_name: str, state_delta: Dict[str, Any]):
        """Persists LangGraph state checkpoint after each node execution."""
        clean_delta = {k: v for k, v in state_delta.items() if k not in ["sandbox", "emit_event"]}
        update_data = {
            f"checkpoints.{node_name}": clean_delta,
            "last_node": node_name,
            "updated_at": time.time()
        }
        with self._lock:
            run = self._memory_runs.get(run_id, {})
            if "checkpoints" not in run:
                run["checkpoints"] = {}
            run["checkpoints"][node_name] = clean_delta
            run["last_node"] = node_name
            run["updated_at"] = time.time()
            self._memory_runs[run_id] = run
            self._save_disk_cache()

            if self.is_connected:
                try:
                    self.db.runs.update_one(
                        {"run_id": run_id},
                        {"$set": update_data},
                        upsert=True
                    )
                except Exception as e:
                    print(f"[Database] Checkpoint save error: {e}")

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single run record by ID."""
        with self._lock:
            if self.is_connected:
                try:
                    doc = self.db.runs.find_one({"run_id": run_id}, {"_id": 0})
                    if doc:
                        return doc
                except Exception:
                    pass
            return self._memory_runs.get(run_id)

    def get_run_events(self, run_id: str) -> List[Dict[str, Any]]:
        """Retrieves historical events for an active or completed run."""
        with self._lock:
            if self.is_connected:
                try:
                    cursor = self.db.events.find({"run_id": run_id}, {"_id": 0}).sort("timestamp", 1)
                    events = list(cursor)
                    if events:
                        return events
                except Exception:
                    pass
            return self._memory_events.get(run_id, [])

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Lists runs sorted by updated_at descending."""
        with self._lock:
            if self.is_connected:
                try:
                    cursor = self.db.runs.find({}, {"_id": 0}).sort("updated_at", -1).limit(limit)
                    return list(cursor)
                except Exception:
                    pass

            runs = list(self._memory_runs.values())
            runs.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
            return runs[:limit]


# Global singleton instance
db_manager = DatabaseManager()

