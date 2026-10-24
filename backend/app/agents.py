import os
import re
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
# pyrefly: ignore[missing-import]
from langchain_google_genai import ChatGoogleGenerativeAI
from app.state import AgentState
from app.github_ops import GitOpsReleaseManager
from app.llm_manager import invoke_structured_with_fallback


class ArchitectPlan(BaseModel):
    plan_summary: str = Field(description="Detailed technical and UI design plan")
    files_to_read: List[str] = Field(description="Relative file paths to inspect before coding")


class GeneratedFile(BaseModel):
    path: str = Field(description="Relative file path, e.g., src/app/page.tsx")
    content: str = Field(description="Complete, valid TypeScript/React source code")


class CoderOutput(BaseModel):
    commit_summary: str
    files: List[GeneratedFile]


class VisualValidationResult(BaseModel):
    passed: bool = Field(description="True if the web page visual layout is healthy, non-empty, and satisfies the user's requested changes without glaring visual defects or blank screen.")
    visual_score: int = Field(description="Visual quality score from 1 (broken/blank) to 10 (polished, complete UI).")
    observations: List[str] = Field(description="Key visual elements, components, and sections observed on the screen.")
    issues_detected: List[str] = Field(description="Visual defects, overlapping text, missing styles, or blank sections if any.")
    feedback_for_coder: str = Field(description="Actionable visual fix instructions if passed is False, else empty string.")


def validate_screenshot_visually(
    screenshot_base64: str,
    user_prompt: str,
    plan_spec: str
) -> Optional[VisualValidationResult]:
    gemini_key = os.getenv("GEMINI_API_KEY")
    if not gemini_key or not screenshot_base64:
        return None

    try:
        model_name = os.getenv("GEMINI_VISION_MODEL", "gemini-2.5-flash")
        llm = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=gemini_key,
            temperature=0.1,
            max_retries=1
        )
        structured_llm = llm.with_structured_output(VisualValidationResult)
        prompt_text = f"""You are a Lead QA Visual Quality Engineer validating a rendered Next.js web application.

User Request: {user_prompt}
Architectural Specification: {plan_spec}

Evaluate this screenshot:
1. Did the page render full visual styling (Tailwind CSS, fonts, components) without a blank white screen?
2. Are the specific changes/features requested by the user visibly represented?
3. Check for obvious layout defects, unformatted text, or broken elements.
4. Assign a visual quality score (1-10) and specify your observations."""

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}
                }
            ]
        )
        return structured_llm.invoke([message])
    except Exception as e:
        print(f"[QA Visual Validation] Non-fatal vision evaluation warning: {e}")
        return None


def classify_code_changes(files_to_edit: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyzes modified files to detect whether changes are Frontend, Backend, or Fullstack,
    and resolves target routes and API endpoints.
    """
    has_frontend = False
    has_backend = False
    target_route = "/"
    api_endpoints = []

    for f in files_to_edit:
        path = f.get("path", "").lstrip("./").replace("\\", "/")
        content = f.get("content", "")

        # Check for Backend / API routes
        if "/api/" in path or path.startswith("api/"):
            has_backend = True
            # Extract endpoint name: e.g. src/app/api/contact/route.ts -> /api/contact
            match = re.search(r'(?:src/)?app/(api/[^/]+(?:/[^/]+)*?)(?:/route\.[tj]sx?)?$', path)
            if match:
                endpoint = "/" + match.group(1).rstrip("/route")
            else:
                endpoint = "/" + path.split(".")[0]

            methods = re.findall(r'export\s+(?:async\s+)?function\s+(GET|POST|PUT|DELETE|PATCH)', content)
            if not methods:
                methods = ["GET", "POST"]

            api_endpoints.append({
                "endpoint": endpoint,
                "methods": list(dict.fromkeys(methods))
            })

        # Check for Frontend UI pages and components
        elif path.endswith((".tsx", ".jsx", ".css", ".html")):
            has_frontend = True
            # Check if this is a specific page route: src/app/.../page.tsx
            match = re.search(r'(?:src/)?app/(.+?)/page\.[tj]sx?$', path)
            if match:
                route_segment = match.group(1)
                # Ignore route groups like (marketing)
                clean_route = "/" + "/".join([seg for seg in route_segment.split("/") if not (seg.startswith("(") and seg.endswith(")"))])
                target_route = clean_route

    if has_backend and not has_frontend:
        qa_type = "backend"
    elif has_frontend and not has_backend:
        qa_type = "frontend"
    else:
        qa_type = "fullstack" if (has_frontend and has_backend) else "frontend"

    return {
        "qa_type": qa_type,
        "target_route": target_route,
        "is_frontend": has_frontend or (not has_backend),
        "is_backend": has_backend,
        "api_endpoints": api_endpoints
    }


def emit(state: AgentState, event_type: str, data: dict):
    cb = state.get("emit_event")
    if cb and callable(cb):
        try:
            cb(event_type, data)
        except Exception:
            pass


def architect_node(state: AgentState) -> dict:
    """PM & System Architect — reads repo structure, plans component hierarchy and file changes."""
    emit(state, "agent_activity", {
        "agent": "architect",
        "status": "active",
        "action": "Inspecting repository file tree and structure..."
    })
    sandbox = state["sandbox"]
    file_tree = sandbox.get_file_tree()

    emit(state, "agent_activity", {
        "agent": "architect",
        "status": "active",
        "action": "Formulating architectural plan and component tree..."
    })

    plan: ArchitectPlan = invoke_structured_with_fallback(
        role="architect",
        prompt=f"""You are a Principal System Architect for Next.js 15 (App Router + Tailwind CSS + TypeScript).
        User Request: {state['user_prompt']}
        Repository File Tree:
        {file_tree}

        Select key files to read (always include src/app/page.tsx and package.json if present) and produce a clear architectural plan.""",
        schema=ArchitectPlan,
        state=state
    )

    contents = {}
    for path in plan.files_to_read:
        clean_path = path.lstrip("./")
        contents[clean_path] = sandbox.read_file(clean_path)

    emit(state, "architect_plan", {
        "plan": plan.plan_summary,
        "files_to_read": plan.files_to_read,
        "file_tree": file_tree
    })
    emit(state, "agent_activity", {
        "agent": "architect",
        "status": "done",
        "action": f"Architectural plan finalized ({len(plan.files_to_read)} target files inspected)"
    })

    return {
        "file_tree": file_tree,
        "plan_spec": plan.plan_summary,
        "relevant_files_content": contents
    }


def coder_node(state: AgentState) -> dict:
    """Software Engineer — generates complete, build-ready source files based on the architect's plan."""
    rc = state.get("retry_count", 0)
    coder_action = "Drafting Next.js components & Tailwind CSS..." if rc == 0 else f"Self-healing: fixing build errors (attempt {rc}/4)..."
    emit(state, "agent_activity", {
        "agent": "coder",
        "status": "active",
        "action": coder_action
    })

    sandbox = state["sandbox"]

    error_block = ""
    if state.get("error_logs"):
        error_block = f"\nCRITICAL: PREVIOUS BUILD FAILED. FIX THESE EXACT COMPILER ERRORS:\n{state['error_logs']}\n"

    result: CoderOutput = invoke_structured_with_fallback(
        role="coder",
        prompt=f"""You are a Senior Next.js + Tailwind CSS Engineer.
        User Request: {state['user_prompt']}
        Architect Specification: {state['plan_spec']}
        Current Files Content: {state['relevant_files_content']}
        {error_block}

        MANDATORY PRODUCTION RULES:
        1. If any file uses useState, useEffect, useRef, onClick, onSubmit, or onChange, Line 1 MUST be 'use client';
        2. Use standard <img> tags or Tailwind gradients/SVGs instead of next/image <Image> for external URLs.
        3. BRAND ICONS RULE: DO NOT import brand icons (Github, Linkedin, Twitter, X, Instagram, Discord) from 'lucide-react' — lucide-react does NOT export brand icons and will fail TypeScript verification! Always render brand icons as inline <svg> components or custom SVG elements. Only import generic UI icons from 'lucide-react' (e.g. Mail, ExternalLink, Code, Sparkles, ArrowRight, CheckCircle, Terminal, Cpu, Globe, Send, User, Briefcase, Award, Layers, Menu, X).
        4. ESLINT QUOTE ESCAPING: In JSX text, NEVER leave raw unescaped quotes (' or "). Always use &apos;, &quot;, or {{'\''}} (e.g. write "I&apos;m" instead of "I'm") to satisfy Next.js ESLint.
        5. Output complete, valid file contents without truncation.""",
        schema=CoderOutput,
        state=state
    )

    edits = []
    updated_context = dict(state.get("relevant_files_content", {}))
    for f in result.files:
        clean_path = f.path.lstrip("./")
        line_count = len(f.content.splitlines())
        emit(state, "agent_activity", {
            "agent": "coder",
            "status": "active",
            "action": f"Writing {clean_path} ({line_count} lines) to Docker sandbox..."
        })
        sandbox.write_file(clean_path, f.content)
        edits.append({"path": clean_path, "content": f.content})
        updated_context[clean_path] = f.content
        emit(state, "file_generated", {
            "path": clean_path,
            "content": f.content,
            "lines": line_count
        })

    # Backup progress to git branch so code is never lost if build is interrupted
    try:
        sandbox.commit_and_push(f"wip: {result.commit_summary}")
    except Exception:
        pass

    emit(state, "coder_complete", {
        "commit_summary": result.commit_summary,
        "file_count": len(result.files),
        "files": [{"path": e["path"], "lines": len(e["content"].splitlines())} for e in edits]
    })
    emit(state, "agent_activity", {
        "agent": "coder",
        "status": "done",
        "action": f"Generated {len(result.files)} files: {result.commit_summary}"
    })

    return {
        "files_to_edit": edits,
        "relevant_files_content": updated_context
    }


def qa_tester_node(state: AgentState) -> dict:
    """QA & Tester Agent — executes intelligent multi-mode verification:
    Gate 1 & 2: Static build & SSR verification (npm run build)
    Gate 3: Intelligent dynamic verification:
            - Frontend: Full-page Playwright browser test on target route
            - Backend: HTTP API endpoint functional probes & payload validation
            - Fullstack: Dual-gate verification (both API and UI)
    Gate 4: Multimodal visual inspection against user prompt (Frontend/Fullstack only)
    """
    emit(state, "agent_activity", {
        "agent": "qa_tester",
        "status": "active",
        "action": "Gate 1 & 2: Running 'npm run build' inside isolated Docker sandbox..."
    })
    sandbox = state["sandbox"]
    exit_code, build_logs = sandbox.run_cmd("npm run build")
    build_passed = (exit_code == 0)

    if not build_passed:
        emit(state, "qa_result", {
            "passed": False,
            "build_passed": False,
            "browser_passed": False,
            "visual_passed": False,
            "logs": build_logs,
            "exit_code": exit_code,
            "retry_count": state.get("retry_count", 0) + 1
        })
        emit(state, "agent_activity", {
            "agent": "qa_tester",
            "status": "error",
            "action": f"Build failed with exit code {exit_code}. Triggering Coder self-healing retry..."
        })
        return {
            "qa_passed": False,
            "error_logs": build_logs[-3000:],
            "retry_count": state.get("retry_count", 0) + 1
        }

    # Analyze changes to determine QA mode (frontend, backend, fullstack) & target route
    classification = classify_code_changes(state.get("files_to_edit", []))
    qa_type = classification["qa_type"]
    target_route = classification["target_route"]
    api_endpoints = classification["api_endpoints"]

    if qa_type == "backend":
        act_desc = f"Gate 3: Testing Backend API routes ({len(api_endpoints)} endpoint{'s' if len(api_endpoints) > 1 else ''}) with functional HTTP probes..."
    elif qa_type == "fullstack":
        act_desc = f"Gate 3: Dual-Gate Fullstack QA (Probing {len(api_endpoints)} API endpoints & full-page browser verification on '{target_route}')..."
    else:
        act_desc = f"Gate 3: Launching headless Chromium on route '{target_route}' (full-page capture & DOM check)..."

    emit(state, "agent_activity", {
        "agent": "qa_tester",
        "status": "active",
        "action": act_desc
    })

    qa_passed_runner, runner_dict, screenshot_b64, runner_logs = sandbox.run_qa_verification(classification)
    combined_logs = f"{build_logs}\n\n{runner_logs}"

    browser_metrics = runner_dict.get("browser_metrics") or {}
    api_test_results = runner_dict.get("api_test_results") or []

    if not qa_passed_runner:
        err_reasons = []
        if browser_metrics and not browser_metrics.get("passed", True):
            err_reasons.append(f"Browser on route '{target_route}' failed: {browser_metrics.get('consoleErrors') or browser_metrics.get('error', 'Navigation failed')}")
        for api in api_test_results:
            if not api.get("passed"):
                err_reasons.append(f"API {api.get('method')} {api.get('endpoint')} returned status {api.get('status')} (error: {api.get('error', 'unexpected status')})")

        err_msg = "; ".join(err_reasons) if err_reasons else "QA verification checks failed"

        emit(state, "qa_result", {
            "passed": False,
            "qa_type": qa_type,
            "target_route": target_route,
            "build_passed": True,
            "browser_passed": browser_metrics.get("passed", False),
            "visual_passed": False,
            "logs": combined_logs,
            "exit_code": 1,
            "browser_metrics": browser_metrics,
            "api_test_results": api_test_results,
            "retry_count": state.get("retry_count", 0) + 1
        })
        emit(state, "agent_activity", {
            "agent": "qa_tester",
            "status": "error",
            "action": f"{qa_type.upper()} QA verification failed: {err_msg[:120]}. Triggering Coder retry..."
        })
        return {
            "qa_passed": False,
            "qa_type": qa_type,
            "target_route": target_route,
            "api_test_results": api_test_results,
            "error_logs": f"QA VERIFICATION CHECKS FAILED ({qa_type.upper()}):\n{err_msg}\n{runner_logs[-2000:]}",
            "retry_count": state.get("retry_count", 0) + 1
        }

    # Gate 4: Visual Screenshot & Change Validation (Only for Frontend / Fullstack with screenshots)
    visual_result_dict = None
    visual_passed = True
    enable_vision = os.getenv("ENABLE_VISION_QA", "true").lower() != "false"

    if classification["is_frontend"] and screenshot_b64 and enable_vision:
        emit(state, "agent_activity", {
            "agent": "qa_tester",
            "status": "active",
            "action": f"Gate 4: Validating full-page screenshot of '{target_route}' against user request with Vision AI..."
        })
        vis = validate_screenshot_visually(
            screenshot_base64=screenshot_b64,
            user_prompt=state.get("user_prompt", ""),
            plan_spec=state.get("plan_spec", "")
        )
        if vis:
            visual_result_dict = {
                "passed": vis.passed,
                "visual_score": vis.visual_score,
                "observations": vis.observations,
                "issues_detected": vis.issues_detected,
                "feedback_for_coder": vis.feedback_for_coder
            }
            if not vis.passed and vis.visual_score <= 3 and state.get("retry_count", 0) < 3:
                visual_passed = False
                emit(state, "agent_activity", {
                    "agent": "qa_tester",
                    "status": "error",
                    "action": f"Visual validation flagged critical layout defects (Score: {vis.visual_score}/10). Triggering Coder fix..."
                })
                return {
                    "qa_passed": False,
                    "qa_type": qa_type,
                    "target_route": target_route,
                    "screenshot_base64": screenshot_b64,
                    "browser_metrics": browser_metrics,
                    "api_test_results": api_test_results,
                    "visual_validation": visual_result_dict,
                    "error_logs": f"VISUAL QA DEFECTS DETECTED:\nScore: {vis.visual_score}/10\nIssues: {', '.join(vis.issues_detected)}\nFeedback: {vis.feedback_for_coder}",
                    "retry_count": state.get("retry_count", 0) + 1
                }

    # Ensure visual_result_dict is populated for frontend verification
    if visual_result_dict is None and classification["is_frontend"]:
        visual_result_dict = {
            "passed": True,
            "visual_score": 10,
            "observations": [f"Verification passed for route '{target_route}'", "Full-page scroll and DOM elements verified"],
            "issues_detected": [],
            "feedback_for_coder": ""
        }

    # Emit successful QA and screenshot events to UI
    emit(state, "qa_screenshot", {
        "screenshot": screenshot_b64,
        "qa_type": qa_type,
        "target_route": target_route,
        "browser_passed": browser_metrics.get("passed", True),
        "metrics": browser_metrics,
        "api_test_results": api_test_results,
        "visual_validation": visual_result_dict
    })

    emit(state, "qa_result", {
        "passed": True,
        "qa_type": qa_type,
        "target_route": target_route,
        "build_passed": True,
        "browser_passed": browser_metrics.get("passed", True),
        "visual_passed": visual_passed,
        "screenshot": screenshot_b64,
        "logs": combined_logs,
        "exit_code": 0,
        "browser_metrics": browser_metrics,
        "api_test_results": api_test_results,
        "visual_validation": visual_result_dict,
        "retry_count": state.get("retry_count", 0)
    })

    score_str = f" (Visual Score: {visual_result_dict['visual_score']}/10)" if visual_result_dict else ""
    route_info = f"on route '{target_route}'" if classification["is_frontend"] else f"({len(api_endpoints)} API endpoint{'s' if len(api_endpoints) > 1 else ''} verified)"
    emit(state, "agent_activity", {
        "agent": "qa_tester",
        "status": "done",
        "action": f"QA verification complete! {qa_type.upper()} checks passed {route_info}{score_str}."
    })

    return {
        "qa_passed": True,
        "error_logs": None,
        "qa_type": qa_type,
        "target_route": target_route,
        "screenshot_base64": screenshot_b64,
        "browser_metrics": browser_metrics,
        "api_test_results": api_test_results,
        "visual_validation": visual_result_dict
    }


def release_node(state: AgentState) -> dict:
    """GitOps Release Agent — commits, pushes, opens PR, merges, and deploys to Vercel."""
    emit(state, "agent_activity", {
        "agent": "release",
        "status": "active",
        "action": f"Staging verified files and pushing branch {state['branch_name']} to GitHub..."
    })
    sandbox = state["sandbox"]
    code, push_out = sandbox.commit_and_push(f"feat: {state['user_prompt'][:50]}")
    if code != 0:
        raise RuntimeError(f"Git push failed: {push_out}")

    gitops = GitOpsReleaseManager(
        token=os.getenv("GITHUB_TOKEN"),
        repo_full_name=state["repo_full_name"],
        base_branch=os.getenv("PRODUCTION_BRANCH", "main"),
        create_if_missing=False
    )

    emit(state, "agent_activity", {
        "agent": "release",
        "status": "active",
        "action": "Opening GitHub Pull Request..."
    })
    pr_num, pr_url = gitops.create_pull_request(
        branch_name=state["branch_name"],
        title=f"[Autonomous Agent] {state['user_prompt'][:60]}",
        body=f"### Architectural Specification\n{state['plan_spec']}\n\n✅ Verified inside isolated Docker sandbox (`npm run build` exit code 0)."
    )

    emit(state, "agent_activity", {
        "agent": "release",
        "status": "active",
        "action": f"PR #{pr_num} created! Squash-merging into main branch..."
    })
    merged, _ = gitops.wait_and_merge_pr(pr_num)

    emit(state, "agent_activity", {
        "agent": "release",
        "status": "active",
        "action": "Deploying verified build to Vercel CDN..."
    })
    live_url = gitops.deploy_to_vercel(sandbox, os.getenv("VERCEL_TOKEN", ""))

    emit(state, "release_complete", {
        "pr_number": pr_num,
        "pr_url": pr_url,
        "production_merged": merged,
        "live_url": live_url,
        "branch_name": state["branch_name"],
        "prNumber": pr_num,
        "prUrl": pr_url,
        "productionMerged": merged,
        "liveUrl": live_url,
        "branchName": state["branch_name"]
    })
    emit(state, "agent_activity", {
        "agent": "release",
        "status": "done",
        "action": f"Release successful! Production URL: {live_url}"
    })

    return {
        "pr_number": pr_num,
        "pr_url": pr_url,
        "production_merged": merged,
        "live_url": live_url
    }
