from langgraph.graph import StateGraph, END
from app.state import AgentState
from app.agents import architect_node, coder_node, qa_tester_node, release_node


MAX_RETRIES = 4


def qa_router(state: AgentState) -> str:
    """Routes QA results: pass → release, fail with retries left → coder, exhausted → END."""
    if state.get("qa_passed"):
        return "release"
    if state.get("retry_count", 0) >= MAX_RETRIES:
        return END
    return "coder"


def build_sdlc_graph():
    """Builds and compiles the deterministic SDLC state machine.

    Flow: architect → coder → qa_tester → (release | coder retry | END)
    """
    workflow = StateGraph(AgentState)
    workflow.add_node("architect", architect_node)
    workflow.add_node("coder", coder_node)
    workflow.add_node("qa_tester", qa_tester_node)
    workflow.add_node("release", release_node)

    workflow.set_entry_point("architect")
    workflow.add_edge("architect", "coder")
    workflow.add_edge("coder", "qa_tester")
    workflow.add_conditional_edges("qa_tester", qa_router, {
        "coder": "coder",
        "release": "release",
        END: END
    })
    workflow.add_edge("release", END)
    return workflow.compile()
