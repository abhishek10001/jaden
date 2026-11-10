from langgraph.graph import StateGraph, END
from app.state import AgentState
from app.agents import (
    architect_node,
    coder_node,
    qa_tester_node,
    preview_release_node,
    human_approval_node,
    production_release_node
)


MAX_RETRIES = 4


def qa_router(state: AgentState) -> str:
    """Routes QA results: pass → preview_release, fail with retries left → coder, exhausted → END."""
    if state.get("qa_passed"):
        return "preview_release"
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return END
    return "coder"


def approval_router(state: AgentState) -> str:
    """Routes human approval decision: approved → production_release, rejected → coder retry, timeout/other → END."""
    status = state.get("approval_status")
    if status == "approved":
        return "production_release"
    elif status == "rejected":
        if state.get("retry_count", 0) >= MAX_RETRIES:
            return END
        return "coder"
    return END


def build_sdlc_graph():
    """Builds and compiles the deterministic SDLC state machine with Human-in-the-Loop Release Gate.

    Flow: architect → coder → qa_tester → [preview_release → human_approval] → (production_release | coder retry | END)
    """
    workflow = StateGraph(AgentState)
    workflow.add_node("architect", architect_node)
    workflow.add_node("coder", coder_node)
    workflow.add_node("qa_tester", qa_tester_node)
    workflow.add_node("preview_release", preview_release_node)
    workflow.add_node("human_approval", human_approval_node)
    workflow.add_node("production_release", production_release_node)

    workflow.set_entry_point("architect")
    workflow.add_edge("architect", "coder")
    workflow.add_edge("coder", "qa_tester")

    workflow.add_conditional_edges("qa_tester", qa_router, {
        "coder": "coder",
        "preview_release": "preview_release",
        END: END
    })

    workflow.add_edge("preview_release", "human_approval")

    workflow.add_conditional_edges("human_approval", approval_router, {
        "production_release": "production_release",
        "coder": "coder",
        END: END
    })

    workflow.add_edge("production_release", END)
    return workflow.compile()
