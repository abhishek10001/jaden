#  Jaden — Autonomous Multi-Agent SDLC Platform

**Jaden** is an end-to-end autonomous software development lifecycle (SDLC) platform powered by **LangGraph**, **FastAPI**, **Docker**, and **Next.js**. Given a natural language prompt, Jaden coordinates a team of specialized AI agents to plan, scaffold, code, test, visually validate in a headless browser, self-heal, and automatically ship production-ready pull requests to GitHub.

---

##  Key Features

- **Multi-Agent State Machine**: Deterministic orchestration via **LangGraph** with explicit typed state management (`AgentState`).
- **Self-Healing Code Loop**: The QA agent executes builds, lints, and test suites. On failure, error telemetry routes back to the Coder agent for iterative repair (up to 4 automated retry cycles).
- **Isolated Docker Sandboxes**: Ephemeral containerized environments (`playwright:v1.48.0`) safe for running terminal commands, dependencies installation, and code scaffolding.
- **Visual & Browser QA Testing**: Headless browser automation via Playwright inside the sandbox to take screenshots, inspect console errors, check responsive layouts, and validate UI rendering.
- **Automated GitOps & Releases**: Automatically creates feature branches, commits generated code, pushes to GitHub, opens Pull Requests, and outputs live deployment diffs.
- **Multi-Model Provider Flexibility**: Compatible with Google Gemini, Groq (Llama 3.3, Qwen), and OpenRouter models.
- **Real-Time Telemetry Streaming**: Event-driven architecture streaming terminal logs, agent decisions, and screenshots directly to a Next.js web console using **Server-Sent Events (SSE)**.

---

##  Architecture

```
                       +-----------------------+
                       |      User Prompt      |
                       +-----------+-----------+
                                   |
                                   v
                       +-----------------------+
                       |    Architect Agent    |  (Reads repo, designs plan & files)
                       +-----------+-----------+
                                   |
                                   v
             +-------->+-----------------------+
             |         |      Coder Agent      |  (Generates code & executes writes)
             |         +-----------+-----------+
             |                     |
     Failure |                     v
   (Max 4x)  |         +-----------------------+
             +---------+    QA Tester Agent    |  (Runs build, linter, tests & Playwright)
                       +-----------+-----------+
                                   | Pass
                                   v
                       +-----------------------+
                       |     Release Agent     |  (Pushes git branch, opens GitHub PR)
                       +-----------------------+
```

---

##  Repository Structure

```text
Jaden/
├── backend/
│   ├── app/
│   │   ├── agents.py           # Architect, Coder, QA, and Release agent logic
│   │   ├── github_ops.py       # GitHub API integration (branches, PRs, GitOps)
│   │   ├── graph.py            # LangGraph state machine & conditional routing
│   │   ├── llm_manager.py      # LLM provider abstractions (Gemini, Groq, OpenRouter)
│   │   ├── sandbox_manager.py  # Docker container lifecycle & safe exec sandbox
│   │   ├── server.py           # FastAPI application & SSE event stream endpoints
│   │   └── state.py            # Typed state schema for the agent graph
│   └── .env                    # Backend environment configuration
├── frontend/                   # Modern Next.js (App Router) & Tailwind CSS web dashboard
├── sandbox/
│   └── Dockerfile              # Playwright-enabled base container for isolated execution
└── README.md                   # Project documentation
```

---

##  Tech Stack

- **Orchestration**: [LangGraph](https://github.com/langchain-ai/langgraph)
- **Backend API**: [FastAPI](https://fastapi.tiangolo.com/), [Pydantic](https://docs.pydantic.dev/), SSE-Starlette
- **Frontend Dashboard**: [Next.js](https://nextjs.org/) (React 19), [Tailwind CSS](https://tailwindcss.com/), Lucide Icons
- **Execution Sandbox**: [Docker](https://www.docker.com/), [Playwright](https://playwright.dev/)
- **VCS & GitOps**: [PyGithub](https://github.com/PyGithub/PyGithub), Git CLI
- **LLM Integrations**: Google Gemini, Groq, OpenRouter via LangChain chat model adapters

---

##  Getting Started

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** & npm
- **Docker Desktop** installed and running
- **GitHub Personal Access Token** with repository management permissions

---

### 1. Build the Docker Sandbox Image

Jaden executes agent code in an isolated Docker container with pre-installed Playwright dependencies.

```bash
cd sandbox
docker build -t agent-sandbox:latest .
```

---

### 2. Configure Backend Environment

Navigate to the `backend` directory and configure your `.env` file:

```bash
cd ../backend
```

Create or edit `.env`:

```env
# GitHub Configuration
GITHUB_TOKEN=ghp_your_github_personal_access_token
GITHUB_USERNAME=your-github-username

# LLM Providers (Configure at least one)
GEMINI_API_KEY=your_gemini_api_key
GROQ_API_KEY=your_groq_api_key
OPENROUTER_API_KEY=your_openrouter_api_key

# Optional: Vercel Token for automated live deployments
VERCEL_TOKEN=your_vercel_token
```

Install backend dependencies and start the FastAPI server:

```bash
python -m venv venv
# On Windows:
.\venv\Scripts\activate
# On Linux/macOS:
source venv/bin/activate

pip install -r requirements.txt  # or install fastapi uvicorn langgraph langchain-google-genai langchain-groq langchain-openai pygithub docker sse-starlette python-dotenv
uvicorn app.server:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.

---

### 3. Start the Frontend Dashboard

In a separate terminal, navigate to the `frontend` directory:

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

---

##  How It Works

1. **Prompt Entry**: Input a repository name and prompt (e.g., *"Build a modern SaaS pricing page with monthly/yearly toggles"*).
2. **Docker Provisioning**: Jaden spawns an isolated container and clones the repository or scaffolds a greenfield project.
3. **Architect Planning**: The Architect agent analyzes the workspace tree and outputs an architectural specification.
4. **Code Generation & Write**: The Coder agent writes components, styles, and configurations directly to the sandbox container.
5. **QA & Browser Validation**: Playwright checks build outputs, component linting, and visually inspects the page.
6. **Self-Healing Loop**: If errors occur, logs are fed back into the Coder for targeted patching (up to 4 cycles).
7. **Pull Request Dispatch**: Upon passing validation, the Release agent pushes the feature branch and opens a GitHub Pull Request with a breakdown of changes.
