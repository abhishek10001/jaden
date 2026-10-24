from typing import TypedDict, List, Optional, Dict, Any


class FileEdit(TypedDict):
    path: str
    content: str


class AgentState(TypedDict):
    user_prompt: str
    mode: str                                  # "scratch" or "existing"
    repo_full_name: str                        # e.g., "username/my-portfolio"
    branch_name: str                           # e.g., "agent/feat-a1b2c3"
    sandbox: Any                               # DockerSandbox instance
    file_tree: str
    relevant_files_content: Dict[str, str]
    plan_spec: str
    files_to_edit: List[FileEdit]
    qa_passed: bool
    error_logs: Optional[str]
    retry_count: int
    pr_number: Optional[int]
    pr_url: Optional[str]
    production_merged: bool
    live_url: Optional[str]
    emit_event: Optional[Any]
    screenshot_base64: Optional[str]
    browser_metrics: Optional[Dict[str, Any]]
    visual_validation: Optional[Dict[str, Any]]
    qa_type: Optional[str]                     # "frontend", "backend", "fullstack"
    target_route: Optional[str]                # e.g., "/pricing", "/api/contact"
    api_test_results: Optional[List[Dict[str, Any]]]
