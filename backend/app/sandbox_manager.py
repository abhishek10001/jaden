import os
import base64
import docker


class DockerSandbox:
    """Manages an ephemeral Docker container for isolated code execution.

    Provides safe file I/O via Base64 encoding and git operations
    for both greenfield scaffolding and existing repo updates.
    """

    def __init__(self, repo_full_name: str, github_token: str, branch_name: str):
        self.client = docker.from_env(timeout=300)
        self.repo_full_name = repo_full_name
        self.github_token = github_token
        self.branch_name = branch_name
        self.container = None

    def _start_container(self):
        self.container = self.client.containers.run(
            image="agent-sandbox:latest",
            detach=True,
            mem_limit="2g",
            working_dir="/workspace"
        )

    def run_cmd(self, command: str, workdir: str = "/workspace/repo") -> tuple[int, str]:
        exit_code, output = self.container.exec_run(
            cmd=["bash", "-c", command],
            workdir=workdir
        )
        return exit_code, output.decode("utf-8", errors="replace")

    def init_from_scratch(self, base_branch: str = "main"):
        """Scaffolds a brand-new Next.js + Tailwind + Lucide project and pushes initial main branch."""
        self._start_container()
        scaffold_cmd = (
            "npx create-next-app@latest repo "
            "--ts --tailwind --eslint --app --src-dir "
            "--import-alias '@/*' --use-npm --yes"
        )
        code, out = self.run_cmd(scaffold_cmd, workdir="/workspace")
        if code != 0:
            raise RuntimeError(f"Next.js scaffolding failed:\n{out}")

        self.run_cmd("npm install lucide-react", workdir="/workspace/repo")

        # Ensure package.json name matches project name instead of generic "repo"
        project_name = self.repo_full_name.split("/")[-1].lower().replace("_", "-")
        self.run_cmd(
            f'node -e \'const p=require("./package.json"); p.name="{project_name}"; require("fs").writeFileSync("./package.json", JSON.stringify(p,null,2));\'',
            workdir="/workspace/repo"
        )

        auth_url = f"https://x-access-token:{self.github_token}@github.com/{self.repo_full_name}.git"
        self.run_cmd("git add -A")
        self.run_cmd('git commit -m "chore: initial Next.js scaffold" || true')
        self.run_cmd(f"git branch -M {base_branch}")
        self.run_cmd(f"git remote add origin {auth_url}")

        # Check if origin already has existing commits to prevent overwriting history
        check_code, remote_check = self.run_cmd(f"git ls-remote --heads origin {base_branch}")
        if check_code == 0 and remote_check.strip():
            # Repository already exists on GitHub with commit history
            # Attempt normal push without --force to preserve commits
            push_code, push_out = self.run_cmd(f"git push -u origin {base_branch}")
            if push_code != 0:
                raise RuntimeError(
                    f"Repository '{self.repo_full_name}' already contains commits on branch '{base_branch}'. "
                    f"To update this existing website, please select 'Update' mode in the UI so your code is preserved."
                )
        else:
            push_code, push_out = self.run_cmd(f"git push -u origin {base_branch}")
            if push_code != 0:
                raise RuntimeError(f"Initial push failed:\n{push_out}")

        self.run_cmd(f"git checkout -b {self.branch_name}")

    def start_and_clone(self):
        """Clones an existing repository and checks out a fresh feature branch."""
        self._start_container()
        auth_url = f"https://x-access-token:{self.github_token}@github.com/{self.repo_full_name}.git"
        code, out = self.run_cmd(f"git clone {auth_url} /workspace/repo", workdir="/workspace")
        if code != 0:
            raise RuntimeError(f"Git clone failed:\n{out}")
        self.run_cmd(f"git checkout -b {self.branch_name}")
        self.run_cmd("npm install --prefer-offline --no-audit")

    def get_file_tree(self) -> str:
        _, out = self.run_cmd(
            "find . -maxdepth 3 -not -path '*/.*' -not -path './node_modules*' -not -path './.next*'"
        )
        return out

    def read_file(self, rel_path: str) -> str:
        code, out = self.run_cmd(f"cat {rel_path}")
        return out if code == 0 else ""

    def write_file(self, rel_path: str, content: str):
        b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        dir_name = os.path.dirname(rel_path)
        if dir_name:
            self.run_cmd(f"mkdir -p {dir_name}")
        self.run_cmd(f"echo '{b64}' | base64 -d > {rel_path}")

    def write_file_abs(self, abs_path: str, content: str):
        b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        dir_name = os.path.dirname(abs_path)
        if dir_name:
            self.run_cmd(f"mkdir -p {dir_name}", workdir="/workspace")
        self.run_cmd(f"echo '{b64}' | base64 -d > {abs_path}", workdir="/workspace")

    def read_file_base64(self, abs_path: str) -> str:
        code, out = self.run_cmd(f"base64 -w 0 {abs_path}", workdir="/workspace")
        if code == 0 and out.strip():
            return out.strip()
        return ""

    def run_qa_verification(self, qa_config: dict = None) -> tuple[bool, dict, str, str]:
        """Runs intelligent multi-mode QA verification inside Docker:
        - Frontend mode: Launches headless Chromium, navigates to target_route, triggers hydration,
          auto-scrolls the entire document height, and captures a full-page screenshot.
        - Backend mode: Probes API endpoints, measures latency, validates HTTP status and response JSON.
        - Fullstack mode: Executes both API probes and browser full-page capture.

        Returns: (passed, result_dict, screenshot_base64, runner_logs)
        """
        import json
        if qa_config is None:
            qa_config = {
                "qa_type": "frontend",
                "target_route": "/",
                "is_frontend": True,
                "is_backend": False,
                "api_endpoints": []
            }

        # 1. Write the QA config into the container
        self.write_file_abs("/workspace/qa_config.json", json.dumps(qa_config, indent=2))

        # 2. Write the intelligent QA runner script
        runner_script = r'''const { chromium } = require('playwright');
const fs = require('fs');

const config = JSON.parse(fs.readFileSync('/workspace/qa_config.json', 'utf8'));

(async () => {
  const result = {
    passed: true,
    qa_type: config.qa_type,
    target_route: config.target_route || '/',
    browser_metrics: null,
    api_test_results: [],
    logs: []
  };

  // 1. Probing Backend / API endpoints if present
  if (config.is_backend && config.api_endpoints && config.api_endpoints.length > 0) {
    console.log('[QA Runner] Starting Backend API verification...');
    for (const api of config.api_endpoints) {
      const url = `http://127.0.0.1:3000${api.endpoint}`;
      for (const method of api.methods) {
        console.log(`[QA Runner] Testing ${method} ${url}...`);
        const startTime = Date.now();
        try {
          const fetchOptions = {
            method,
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }
          };
          if (['POST', 'PUT', 'PATCH'].includes(method)) {
            fetchOptions.body = JSON.stringify({
              test: true,
              email: 'qa-agent@autonomous-sdlc.local',
              name: 'QA Automated Agent',
              message: 'Automated functional endpoint probe'
            });
          }
          const res = await fetch(url, fetchOptions);
          const latencyMs = Date.now() - startTime;
          const status = res.status;
          const text = await res.text();
          let parsedBody;
          try {
            parsedBody = JSON.parse(text);
          } catch (_) {
            parsedBody = text.slice(0, 500);
          }
          const endpointPassed = (status >= 200 && status < 400);
          console.log(`[QA Runner] ${method} ${api.endpoint} -> HTTP ${status} (${latencyMs}ms) passed=${endpointPassed}`);
          result.api_test_results.push({
            endpoint: api.endpoint,
            method,
            status,
            latencyMs,
            responseBody: parsedBody,
            passed: endpointPassed
          });
          if (!endpointPassed) {
            result.passed = false;
          }
        } catch (err) {
          console.error(`[QA Runner] API test error for ${method} ${api.endpoint}:`, err.message);
          result.api_test_results.push({
            endpoint: api.endpoint,
            method,
            status: 0,
            latencyMs: Date.now() - startTime,
            error: err.message || String(err),
            passed: false
          });
          result.passed = false;
        }
      }
    }
  }

  // 2. Probing Frontend UI if present
  if (config.is_frontend) {
    let browser;
    const consoleErrors = [];
    try {
      console.log(`[QA Runner] Starting Frontend Browser verification on route: ${config.target_route}...`);
      browser = await chromium.launch({
        headless: true,
        args: [
          '--no-sandbox',
          '--disable-setuid-sandbox',
          '--disable-dev-shm-usage',
          '--disable-gpu',
          '--no-first-run',
          '--no-zygote',
          '--single-process'
        ]
      });

      const context = await browser.newContext({
        viewport: { width: 1280, height: 900 },
        deviceScaleFactor: 1
      });

      const page = await context.newPage();

      page.on('console', msg => {
        if (msg.type() === 'error') {
          consoleErrors.push(msg.text());
        }
      });

      page.on('pageerror', err => {
        consoleErrors.push(err.message || String(err));
      });

      const targetUrl = `http://127.0.0.1:3000${config.target_route || '/'}`;
      console.log(`[QA Runner] Navigating to ${targetUrl}...`);
      const response = await page.goto(targetUrl, {
        waitUntil: 'networkidle',
        timeout: 15000
      });

      const status = response ? response.status() : 0;
      console.log(`[QA Runner] Route ${config.target_route} HTTP status: ${status}`);

      // Allow 1.5s for initial React hydration & animations
      await page.waitForTimeout(1500);

      // Auto-scroll down the entire document to trigger lazy components, intersection observers & styling
      await page.evaluate(async () => {
        await new Promise((resolve) => {
          let totalHeight = 0;
          const distance = 400;
          const timer = setInterval(() => {
            const scrollHeight = document.body ? document.body.scrollHeight : 0;
            window.scrollBy(0, distance);
            totalHeight += distance;
            if (totalHeight >= scrollHeight) {
              clearInterval(timer);
              window.scrollTo(0, 0); // Scroll back to top for clean full-page screenshot
              setTimeout(resolve, 300);
            }
          }, 80);
        });
      });

      // Capture full-page screenshot (captures all sections top-to-bottom!)
      const screenshotPath = '/workspace/screenshot.png';
      await page.screenshot({ path: screenshotPath, fullPage: true });
      console.log(`[QA Runner] Full-page screenshot captured at ${screenshotPath}`);

      const title = await page.title();
      const bodyText = await page.evaluate(() => (document.body ? document.body.innerText.trim() : ''));

      const criticalErrors = consoleErrors.filter(err =>
        !err.includes('React DevTools') &&
        !err.includes('Fast Refresh') &&
        !err.includes('Download the React')
      );

      const browserPassed = (status === 200 || status === 304) && criticalErrors.length === 0 && bodyText.length > 0;

      result.browser_metrics = {
        passed: browserPassed,
        targetRoute: config.target_route,
        status,
        title,
        bodyLength: bodyText.length,
        consoleErrors: criticalErrors,
        screenshotTaken: fs.existsSync(screenshotPath)
      };

      if (!browserPassed) {
        result.passed = false;
      }
    } catch (err) {
      console.error('[QA Runner] Frontend Browser Error:', err.message || err);
      result.browser_metrics = {
        passed: false,
        targetRoute: config.target_route,
        error: err.message || String(err),
        consoleErrors
      };
      result.passed = false;
    } finally {
      if (browser) {
        await browser.close().catch(() => {});
      }
    }
  }

  fs.writeFileSync('/workspace/qa_runner_result.json', JSON.stringify(result, null, 2));
  console.log('QA_RUNNER_RESULT:' + JSON.stringify(result));
  process.exit(result.passed ? 0 : 1);
})();
'''
        self.write_file_abs("/workspace/qa_runner.cjs", runner_script)

        # 3. Check if playwright is importable
        check_code, _ = self.run_cmd(
            "NODE_PATH=/usr/local/lib/node_modules:/usr/lib/node_modules node -e \"require('playwright')\"",
            workdir="/workspace/repo"
        )
        if check_code != 0:
            self.run_cmd("npm install --no-save --prefer-offline playwright@1.48.0", workdir="/workspace/repo")

        # 4. Clean previous server instances
        self.run_cmd("pkill -f 'next start' || true", workdir="/workspace")
        self.run_cmd("pkill -f 'next-server' || true", workdir="/workspace")

        # 5. Start Next.js production server
        self.run_cmd("nohup npx next start -p 3000 > /workspace/next_server.log 2>&1 &", workdir="/workspace/repo")

        # 6. Wait for port 3000
        self.run_cmd(
            "for i in $(seq 1 30); do curl -s -f http://127.0.0.1:3000 > /dev/null && break || sleep 0.5; done",
            workdir="/workspace"
        )

        # 7. Execute runner
        qa_code, qa_out = self.run_cmd(
            "NODE_PATH=/usr/local/lib/node_modules:/usr/lib/node_modules:./node_modules node /workspace/qa_runner.cjs",
            workdir="/workspace/repo"
        )

        # 8. Clean up server
        self.run_cmd("pkill -f 'next start' || true", workdir="/workspace")
        self.run_cmd("pkill -f 'next-server' || true", workdir="/workspace")

        # 9. Extract outputs
        screenshot_b64 = self.read_file_base64("/workspace/screenshot.png") if qa_config.get("is_frontend") else ""
        raw_result = self.read_file("/workspace/qa_runner_result.json")

        runner_dict = {}
        if raw_result:
            try:
                runner_dict = json.loads(raw_result)
            except Exception:
                pass

        server_logs = self.read_file("/workspace/next_server.log")
        combined_logs = f"=== Intelligent QA Execution Output ({qa_config.get('qa_type', 'qa').upper()}) ===\n{qa_out}\n\n=== Next.js Server Output ===\n{server_logs[-1000:] if server_logs else 'None'}"

        passed = (qa_code == 0) and runner_dict.get("passed", False)
        return passed, runner_dict, screenshot_b64, combined_logs

    def run_playwright_smoke_test(self) -> tuple[bool, dict, str, str]:
        """Backwards compatibility helper."""
        passed, result_dict, screenshot_b64, logs = self.run_qa_verification()
        metrics = result_dict.get("browser_metrics") or {}
        metrics["passed"] = passed
        return passed, metrics, screenshot_b64, logs

    def commit_and_push(self, commit_message: str) -> tuple[int, str]:
        self.run_cmd("git add -A")
        self.run_cmd(f'git commit -m "{commit_message}" || true')
        # Check if the current branch has any commits ahead of the remote base branch.
        # If HEAD is identical to origin/main or origin/master, create an empty commit
        # so GitHub's Pull Request API won't fail with 422 "No commits between main and branch"
        _, diff_check = self.run_cmd("git rev-parse origin/main 2>/dev/null || git rev-parse origin/master 2>/dev/null || echo ''")
        base_sha = diff_check.strip()
        _, head_sha = self.run_cmd("git rev-parse HEAD")
        head_sha = head_sha.strip()

        commits_ahead = ""
        if base_sha:
            _, commits_ahead = self.run_cmd(f"git rev-list {base_sha}..HEAD --count")

        if not commits_ahead.strip() or commits_ahead.strip() == "0" or (base_sha and head_sha == base_sha):
            self.run_cmd(f'git commit --allow-empty -m "{commit_message} (verified state)"')

        return self.run_cmd(f"git push -u origin {self.branch_name}")

    def cleanup(self):
        if self.container:
            try:
                self.container.remove(force=True)
            except Exception:
                pass
