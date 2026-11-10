import json
import uuid
import os
import asyncio
import threading
from typing import Dict, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from dotenv import load_dotenv
from app.sandbox_manager import DockerSandbox
from app.github_ops import GitOpsReleaseManager
from app.graph import build_sdlc_graph
from app.approval_manager import approval_manager
from app.db_manager import db_manager

load_dotenv()

app = FastAPI(title="Autonomous Multi-Agent SDLC API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active run subscriber queues for multi-tab / browser-refresh reconnection
active_subscribers: Dict[str, List[asyncio.Queue]] = {}
subscribers_lock = threading.Lock()

# Active run sandbox and halt events registry
active_sandboxes: Dict[str, DockerSandbox] = {}
active_halt_events: Dict[str, threading.Event] = {}
active_runs_lock = threading.Lock()


@app.get("/")
async def root():
    return {
        "status": "online",
        "message": "Jaden Backend API is running.",
        "database_connected": db_manager.is_connected,
        "documentation": "/docs",
        "frontend_url": "http://localhost:3000"
    }


@app.get("/api/runs")
async def list_runs():
    """Lists persisted SDLC runs from MongoDB."""
    return {"runs": db_manager.list_runs(limit=50)}


@app.get("/api/runs/{run_id}")
async def get_run_details(run_id: str):
    """Retrieves checkpoints, artifacts, and execution state for a specific run."""
    run = db_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    return run


@app.get("/api/runs/{run_id}/events")
async def get_run_events(run_id: str):
    """Returns all recorded telemetry events for replaying a run."""
    events = db_manager.get_run_events(run_id)
    return {"run_id": run_id, "events": events}


class RunRequest(BaseModel):
    prompt: str
    mode: str = "scratch"
    repo_name: str


class ApprovalRequest(BaseModel):
    approved: bool
    feedback: str = ""


@app.post("/api/runs/{run_id}/approval")
async def handle_human_approval(run_id: str, payload: ApprovalRequest):
    """Processes human approval decision to unblock production merge or request changes."""
    success = approval_manager.submit_decision(
        run_id=run_id,
        approved=payload.approved,
        feedback=payload.feedback
    )
    if not success:
        return {
            "status": "not_found",
            "message": f"Run '{run_id}' not found or no longer awaiting human approval."
        }

    db_manager.create_or_update_run(run_id, {
        "approval_decision": {
            "approved": payload.approved,
            "feedback": payload.feedback
        },
        "status": "approved" if payload.approved else "rejected"
    })

    return {
        "status": "success",
        "run_id": run_id,
        "approved": payload.approved,
        "feedback": payload.feedback
    }


@app.post("/api/runs/{run_id}/halt")
async def halt_agent_run(run_id: str):
    """Immediately halts an active agent run, terminates container, and releases waiting gates."""
    with active_runs_lock:
        halt_event = active_halt_events.get(run_id)
        if halt_event:
            halt_event.set()
        sandbox = active_sandboxes.get(run_id)

    # Immediately halt and terminate Docker container
    if sandbox:
        try:
            sandbox.halt()
        except Exception:
            pass

    # Unblock any pending approval gate
    approval_manager.submit_decision(run_id, approved=False, feedback="Halted by operator.")

    db_manager.create_or_update_run(run_id, {
        "status": "halted",
        "error_message": "Pipeline halted by operator."
    })

    halt_data = {
        "agent": "system",
        "status": "error",
        "action": "OPERATOR OVERRIDE // Pipeline halted immediately."
    }
    db_manager.append_event(run_id, "agent_activity", halt_data)
    db_manager.append_event(run_id, "done", {"status": "halted"})

    with subscribers_lock:
        subs = list(active_subscribers.get(run_id, []))
    for q in subs:
        try:
            q.put_nowait({"event": "agent_activity", "data": json.dumps(halt_data)})
            q.put_nowait({"event": "done", "data": json.dumps({"status": "halted"})})
        except Exception:
            pass

    return {"status": "halted", "run_id": run_id, "message": "Pipeline halted successfully."}


@app.get("/api/runs/{run_id}/stream")
async def reconnect_agent_run_stream(run_id: str):
    """Replays historical telemetry from MongoDB and attaches to ongoing live run events upon browser refresh."""
    run = db_manager.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    async def replay_and_follow():
        # 1. Replay historical events
        past_events = db_manager.get_run_events(run_id)
        for ev in past_events:
            yield {"event": ev.get("event", "status"), "data": json.dumps(ev.get("data", {}))}

        # 2. If run already finished, exit
        if run.get("status") in ["completed", "failed"]:
            return

        # 3. Otherwise subscribe to live events
        sub_q = asyncio.Queue()
        with subscribers_lock:
            if run_id not in active_subscribers:
                active_subscribers[run_id] = []
            active_subscribers[run_id].append(sub_q)

        try:
            while True:
                item = await sub_q.get()
                yield item
                if item.get("event") in ("done", "error"):
                    break
        finally:
            with subscribers_lock:
                if run_id in active_subscribers and sub_q in active_subscribers[run_id]:
                    active_subscribers[run_id].remove(sub_q)
                    if not active_subscribers[run_id]:
                        del active_subscribers[run_id]

    return EventSourceResponse(replay_and_follow())


@app.post("/api/runs/stream")
async def stream_agent_run(req: RunRequest):
    """Streams real-time agent execution progress via Server-Sent Events and persists state to MongoDB."""
    load_dotenv(override=True)

    async def event_generator():
        event_q = asyncio.Queue()
        loop = asyncio.get_running_loop()

        run_id = f"run_{uuid.uuid4().hex[:8]}"
        approval_manager.register_run(run_id)

        branch_id = f"agent/feat-{uuid.uuid4().hex[:6]}"
        gh_user = os.getenv("GITHUB_USERNAME")
        gh_token = os.getenv("GITHUB_TOKEN")
        full_repo = f"{gh_user}/{req.repo_name}" if "/" not in req.repo_name else req.repo_name

        # Initialize run in database
        db_manager.create_or_update_run(run_id, {
            "prompt": req.prompt,
            "mode": req.mode,
            "repo_name": full_repo,
            "branch_name": branch_id,
            "status": "running",
            "current_step": "sandbox_init"
        })

        with subscribers_lock:
            if run_id not in active_subscribers:
                active_subscribers[run_id] = []
            active_subscribers[run_id].append(event_q)

        def emit(event_type: str, data: dict):
            # Persist event to MongoDB for replay upon browser refresh
            db_manager.append_event(run_id, event_type, data)

            msg = {"event": event_type, "data": json.dumps(data)}
            with subscribers_lock:
                subs = list(active_subscribers.get(run_id, []))
            for q in subs:
                loop.call_soon_threadsafe(q.put_nowait, msg)

        def worker():
            sandbox = None
            halt_event = threading.Event()
            with active_runs_lock:
                active_halt_events[run_id] = halt_event
            try:
                emit("status", {
                    "step": "sandbox_init",
                    "run_id": run_id,
                    "runId": run_id,
                    "message": f"Provisioning Docker Sandbox for {full_repo}...",
                    "branch_name": branch_id,
                    "branchName": branch_id
                })
                emit("agent_activity", {
                    "agent": "sandbox",
                    "status": "active",
                    "action": f"Spawning isolated Docker sandbox for {full_repo}..."
                })

                gitops_mgr = GitOpsReleaseManager(
                    token=gh_token,
                    repo_full_name=full_repo,
                    create_if_missing=(req.mode == "scratch")
                )
                author_name, author_email = gitops_mgr.get_commit_author()

                sandbox = DockerSandbox(
                    repo_full_name=full_repo,
                    github_token=gh_token,
                    branch_name=branch_id,
                    git_name=author_name,
                    git_email=author_email
                )
                with active_runs_lock:
                    active_sandboxes[run_id] = sandbox

                # Protect existing repositories from being accidentally overwritten by scratch mode
                is_existing_with_commits = False
                if req.mode == "scratch":
                    try:
                        commits = gitops_mgr.repo.get_commits()
                        if commits.totalCount > 0:
                            is_existing_with_commits = True
                    except Exception:
                        is_existing_with_commits = False

                if req.mode == "scratch" and not is_existing_with_commits:
                    emit("agent_activity", {
                        "agent": "sandbox",
                        "status": "active",
                        "action": "Scaffolding Next.js 15 + Tailwind + TypeScript in Docker container..."
                    })
                    sandbox.init_from_scratch()
                else:
                    action_msg = (
                        f"Detected existing repository {full_repo} with active commits. Auto-switching to Update mode to protect code history..."
                        if is_existing_with_commits
                        else f"Cloning repository {full_repo} and creating branch {branch_id}..."
                    )
                    emit("agent_activity", {
                        "agent": "sandbox",
                        "status": "active",
                        "action": action_msg
                    })
                    sandbox.start_and_clone()

                emit("agent_activity", {
                    "agent": "sandbox",
                    "status": "done",
                    "action": f"Docker sandbox ready on branch {branch_id}"
                })

                graph = build_sdlc_graph()
                initial_state = {
                    "run_id": run_id,
                    "user_prompt": req.prompt,
                    "mode": "existing" if is_existing_with_commits else req.mode,
                    "repo_full_name": full_repo,
                    "branch_name": branch_id,
                    "sandbox": sandbox,
                    "retry_count": 0,
                    "qa_passed": False,
                    "preview_url": None,
                    "approval_status": None,
                    "human_feedback": None,
                    "relevant_files_content": {},
                    "screenshot_base64": None,
                    "browser_metrics": None,
                    "visual_validation": None,
                    "qa_type": None,
                    "target_route": None,
                    "api_test_results": None,
                    "emit_event": emit
                }

                run_succeeded = False
                for event in graph.stream(initial_state):
                    if halt_event.is_set():
                        emit("agent_activity", {
                            "agent": "sandbox",
                            "status": "error",
                            "action": "PIPELINE TERMINATED // Execution halted by operator."
                        })
                        break
                    for node_name, state_update in event.items():
                        if state_update.get("qa_passed") or state_update.get("pr_url") or node_name in ("production_release", "preview_release"):
                            run_succeeded = True
                        db_manager.save_checkpoint(run_id, node_name, state_update)
                        payload = {
                            "node": node_name,
                            "qa_passed": state_update.get("qa_passed"),
                            "retry_count": state_update.get("retry_count"),
                            "pr_url": state_update.get("pr_url"),
                            "preview_url": state_update.get("preview_url"),
                            "live_url": state_update.get("live_url"),
                            "approval_status": state_update.get("approval_status"),
                            "error_preview": (state_update.get("error_logs") or "")[:500]
                        }
                        emit("node_update", payload)
            except Exception as e:
                emit("error", {"error": str(e)})
            finally:
                approval_manager.cleanup_run(run_id)
                with active_runs_lock:
                    active_sandboxes.pop(run_id, None)
                    halt_ev = active_halt_events.pop(run_id, None)
                    was_halted = halt_ev.is_set() if halt_ev else False
                if sandbox:
                    try:
                        sandbox.cleanup()
                    except Exception:
                        pass
                
                final_status = "halted" if was_halted else ("completed" if run_succeeded else "failed")
                if final_status == "failed":
                    db_manager.create_or_update_run(run_id, {
                        "status": "failed",
                        "error_message": "Quality gates could not be satisfied after retries."
                    })
                emit("done", {"status": final_status})

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        try:
            while True:
                item = await event_q.get()
                yield item
                if item.get("event") in ("done", "error"):
                    while not event_q.empty():
                        drain = event_q.get_nowait()
                        yield drain
                    break
        finally:
            with subscribers_lock:
                if run_id in active_subscribers and event_q in active_subscribers[run_id]:
                    active_subscribers[run_id].remove(event_q)
                    if not active_subscribers[run_id]:
                        del active_subscribers[run_id]

    return EventSourceResponse(event_generator())

