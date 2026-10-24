import os
from typing import Type, Any, List, Dict
from dotenv import load_dotenv
from pydantic import BaseModel
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI


def get_model_spec(model_str: str) -> Dict[str, str]:
    """Parses a model string into provider and model identifier.

    Examples:
        'gemini-3.8-flash' -> {'provider': 'gemini', 'model': 'gemini-3.8-flash'}
        'groq/llama-3.3-70b-versatile' -> {'provider': 'groq', 'model': 'llama-3.3-70b-versatile'}
        'openrouter/deepseek/deepseek-chat' -> {'provider': 'openrouter', 'model': 'deepseek/deepseek-chat'}
    """
    cleaned = model_str.strip()
    if cleaned.startswith("groq/"):
        return {"provider": "groq", "model": cleaned[5:], "display": f"Groq ({cleaned[5:]})"}
    elif cleaned.startswith("openrouter/"):
        return {"provider": "openrouter", "model": cleaned[11:], "display": f"OpenRouter ({cleaned[11:]})"}
    elif cleaned.startswith("gemini/"):
        return {"provider": "gemini", "model": cleaned[7:], "display": f"Gemini ({cleaned[7:]})"}
    elif "llama" in cleaned.lower() or "qwen" in cleaned.lower() or "gemma" in cleaned.lower() and "/" not in cleaned:
        # If user passes Groq model without prefix
        return {"provider": "groq", "model": cleaned, "display": f"Groq ({cleaned})"}
    elif "/" in cleaned:
        # Multi-segment model string usually means OpenRouter (e.g. meta-llama/llama-3.3-70b-instruct)
        return {"provider": "openrouter", "model": cleaned, "display": f"OpenRouter ({cleaned})"}
    else:
        return {"provider": "gemini", "model": cleaned, "display": f"Gemini ({cleaned})"}


def create_llm_instance(spec: Dict[str, str], temperature: float = 0.1) -> BaseChatModel:
    """Instantiates the appropriate ChatModel based on the provider spec."""
    load_dotenv(override=True)
    provider = spec["provider"]
    model_name = spec["model"]

    if provider == "groq":
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise ValueError("GROQ_API_KEY not configured in backend/.env")
        return ChatGroq(
            model=model_name,
            api_key=api_key,
            temperature=temperature
        )

    elif provider == "openrouter":
        api_key = os.getenv("OPENROUTER_API_KEY")
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY not configured in backend/.env")
        return ChatOpenAI(
            model=model_name,
            api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            temperature=temperature,
            default_headers={
                "HTTP-Referer": "https://jaden-agent.local",
                "X-Title": "Jaden SDLC Agent",
            }
        )

    else:
        # Default: Google Gemini
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not configured in backend/.env")
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            temperature=temperature,
            max_retries=1
        )


def get_candidate_models(role: str) -> List[Dict[str, str]]:
    """Builds an ordered fallback list of models tailored for the given agent role."""
    load_dotenv(override=True)
    candidates = []

    # 1. Check explicit role-specific env overrides
    role_override = os.getenv(f"{role.upper()}_MODEL")
    if role_override:
        candidates.append(get_model_spec(role_override))

    # 2. Add defaults based on role and available API keys
    groq_key = os.getenv("GROQ_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    gemini_key = os.getenv("GEMINI_API_KEY")

    default_gemini = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
    default_openrouter = os.getenv("OPENROUTER_MODEL", "openrouter/nvidia/nemotron-3.5-lightning:free")
    default_groq = os.getenv("GROQ_MODEL", "groq/qwen/qwen3.8-27b")

    if role == "architect":
        # Architect needs high reasoning and large context
        if not role_override:
            candidates.append(get_model_spec(default_gemini))
        if gemini_key:
            candidates.append(get_model_spec("gemini-3.1-flash-lite"))
        if openrouter_key:
            candidates.append(get_model_spec(default_openrouter))
        if groq_key:
            candidates.append(get_model_spec(default_groq))

    elif role == "coder":
        # Coder benefits from code-specialized models with fast output
        if not role_override:
            candidates.append(get_model_spec(default_gemini))
        if gemini_key:
            candidates.append(get_model_spec("gemini-3.1-flash-lite"))
        if openrouter_key:
            candidates.append(get_model_spec(default_openrouter))
        if groq_key:
            candidates.append(get_model_spec(default_groq))

    # De-duplicate candidate specifications
    seen = set()
    unique_candidates = []
    for c in candidates:
        key = f"{c['provider']}:{c['model']}"
        if key not in seen:
            seen.add(key)
            unique_candidates.append(c)

    return unique_candidates


def invoke_structured_with_fallback(
    role: str,
    prompt: str,
    schema: Type[BaseModel],
    state: Dict[str, Any]
) -> BaseModel:
    """Executes a structured LLM invocation with automated multi-model fallback on quota/rate-limit errors."""
    candidates = get_candidate_models(role)
    errors = []

    emit_cb = state.get("emit_event")

    for i, spec in enumerate(candidates):
        is_fallback = (i > 0)
        display_name = spec["display"]

        if is_fallback and emit_cb:
            try:
                emit_cb("agent_activity", {
                    "agent": role,
                    "status": "active",
                    "action": f"[FALLBACK] Quota/error detected on primary model. Seamlessly switching to {display_name}..."
                })
            except Exception:
                pass

        try:
            llm = create_llm_instance(spec)
            structured_llm = llm.with_structured_output(schema)
            result = structured_llm.invoke(prompt)

            if result is not None:
                return result
            else:
                errors.append(f"{display_name} returned empty structured output")
        except Exception as e:
            err_str = str(e)
            print(f"[{role.upper()}] Candidate '{display_name}' error: {err_str[:300]}")
            errors.append(f"{display_name}: {err_str[:250]}")

    raise RuntimeError(
        f"All model candidates failed for {role} agent ({len(candidates)} tried):\n" +
        "\n".join(errors) +
        "\nTip: Check GEMINI_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY in backend/.env"
    )
