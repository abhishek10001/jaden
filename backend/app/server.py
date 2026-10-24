import json
import uuid
import os
import asyncio
import threading
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse
from dotenv import load_dotenv
from app.sandbox_manager import DockerSandbox
from app.github_ops import GitOpsReleaseManager
from app.graph import build_sdlc_graph

load_dotenv()

app = FastAPI(title="Autonomous Multi-Agent SDLC API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    prompt: str
    mode: str = "scratch"
    repo_name: str


@app.post("/api/runs/stream")
async def stream_agent_run(req: RunRequest):
    """Streams real-time agent execution progress via Server-Sent Events."""
    load_dotenv(override=True)

    async def event_generator():
        event_q = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def emit(event_type: str, data: dict):
            loop.call_soon_threadsafe(
                event_q.put_nowait,
                {"event": event_type, "data": json.dumps(data)}
            )

        branch_id = f"agent/feat-{uuid.uuid4().hex[:6]}"
        gh_user = os.getenv("GITHUB_USERNAME")
        gh_token = os.getenv("GITHUB_TOKEN")
        full_repo = f"{gh_user}/{req.repo_name}" if "/" not in req.repo_name else req.repo_name

        def worker():
            sandbox = None
            try:
                emit("status", {
                    "step": "sandbox_init",
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
                sandbox = DockerSandbox(
                    repo_full_name=full_repo,
                    github_token=gh_token,
                    branch_name=branch_id
                )

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
                    "user_prompt": req.prompt,
                    "mode": "existing" if is_existing_with_commits else req.mode,
                    "repo_full_name": full_repo,
                    "branch_name": branch_id,
                    "sandbox": sandbox,
                    "retry_count": 0,
                    "qa_passed": False,
                    "relevant_files_content": {},
                    "screenshot_base64": None,
                    "browser_metrics": None,
                    "visual_validation": None,
                    "qa_type": None,
                    "target_route": None,
                    "api_test_results": None,
                    "emit_event": emit
                }

                for event in graph.stream(initial_state):
                    for node_name, state_update in event.items():
                        payload = {
                            "node": node_name,
                            "qa_passed": state_update.get("qa_passed"),
                            "retry_count": state_update.get("retry_count"),
                            "pr_url": state_update.get("pr_url"),
                            "live_url": state_update.get("live_url"),
                            "error_preview": (state_update.get("error_logs") or "")[:500]
                        }
                        emit("node_update", payload)
            except Exception as e:
                emit("error", {"error": str(e)})
            finally:
                if sandbox:
                    try:
                        sandbox.cleanup()
                    except Exception:
                        pass
                emit("done", {"status": "completed"})

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            item = await event_q.get()
            yield item
            if item.get("event") in ("done", "error"):
                while not event_q.empty():
                    drain = event_q.get_nowait()
                    yield drain
                break

    return EventSourceResponse(event_generator())
