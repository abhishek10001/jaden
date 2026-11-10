import time
from github import Github, GithubException
import os


class GitOpsReleaseManager:
    """Handles GitHub repository creation, PR automation, and Vercel deployment."""

    def __init__(self, token: str, repo_full_name: str, base_branch: str = "main", create_if_missing: bool = False):
        self.gh = Github(token)
        self.base_branch = base_branch
        self.repo_full_name = repo_full_name
        self.repo = self._get_or_create_repo(repo_full_name, create_if_missing)

    def _get_or_create_repo(self, repo_full_name: str, create_if_missing: bool):
        try:
            return self.gh.get_repo(repo_full_name)
        except GithubException as e:
            if e.status == 404 and create_if_missing:
                short_name = repo_full_name.split("/")[-1]
                user = self.gh.get_user()
                return user.create_repo(
                    name=short_name,
                    description="Autonomously built and deployed by Multi-Agent GitOps Platform",
                    private=False,
                    auto_init=False
                )
            raise

    def get_commit_author(self) -> tuple[str, str]:
        """Resolves the git author name and a valid email associated with the GitHub account."""
        env_email = os.getenv("GIT_COMMIT_EMAIL")
        env_name = os.getenv("GIT_COMMIT_NAME")

        name = env_name
        email = env_email

        try:
            user = self.gh.get_user()
            if not name:
                name = user.name or user.login or "Autonomous SDLC Agent"
            if not email:
                email = user.email or f"{user.login}@users.noreply.github.com"
        except Exception:
            if not name:
                name = os.getenv("GITHUB_USERNAME") or "Autonomous SDLC Agent"
            if not email:
                gh_user = os.getenv("GITHUB_USERNAME") or "agent"
                email = f"{gh_user}@users.noreply.github.com"

        return name, email

    def create_pull_request(self, branch_name: str, title: str, body: str) -> tuple[int, str]:
        try:
            pr = self.repo.create_pull(
                title=title,
                body=body,
                head=branch_name,
                base=self.base_branch
            )
            return pr.number, pr.html_url
        except GithubException as e:
            if e.status == 422:
                err_str = str(e)
                if "No commits between" in err_str:
                    raise RuntimeError(
                        f"GitHub PR failed: The branch '{branch_name}' has 0 commits ahead of '{self.base_branch}'. "
                        f"The codebase was already in sync with production."
                    )
                # If PR already exists for this branch (e.g. from an earlier approval iteration), reuse it
                if "A pull request already exists" in err_str:
                    try:
                        owner = self.repo.owner.login
                        pulls = self.repo.get_pulls(state="open", head=f"{owner}:{branch_name}")
                        if pulls.totalCount > 0:
                            existing_pr = pulls[0]
                            existing_pr.edit(title=title, body=body)
                            return existing_pr.number, existing_pr.html_url
                    except Exception:
                        pass
            raise

    def wait_and_merge_pr(self, pr_number: int, timeout_sec: int = 90) -> tuple[bool, str]:
        pr = self.repo.get_pull(pr_number)
        elapsed = 0
        while elapsed < timeout_sec:
            pr.update()
            if pr.mergeable is not False:
                try:
                    status = pr.merge(
                        commit_message=f"Autonomous Production Release: PR #{pr_number}",
                        merge_method="squash"
                    )
                    if status.merged:
                        return True, status.message
                except GithubException:
                    pass
            time.sleep(5)
            elapsed += 5
        return False, "Timed out waiting for PR mergeability."

    def _extract_vercel_url(self, output: str, is_production: bool = False) -> str:
        import re

        # Look for explicit "Production: https://..." or "Preview: https://..."
        prefix = "Production:" if is_production else "(?:Preview|Production):"
        match = re.search(rf'{prefix}\s*(https://[^\s]+)', output)
        if match:
            return match.group(1).strip().rstrip(".")

        # Extract all live .vercel.app URLs (filtering out vercel.com inspect links)
        live_urls = []
        for line in output.splitlines():
            line_str = line.strip()
            matches = re.findall(r'https://[a-zA-Z0-9\-\._]+\.vercel\.app', line_str)
            for m in matches:
                if "vercel.com" not in m:
                    live_urls.append(m.rstrip("."))

        if live_urls:
            return live_urls[-1]

        fallback_urls = [
            line.strip()
            for line in output.splitlines()
            if line.strip().startswith("https://") and "vercel.com" not in line
        ]
        if fallback_urls:
            return fallback_urls[-1]

        return "https://vercel.com"

    def deploy_preview(self, sandbox, vercel_token: str) -> str:
        """Deploys an ephemeral preview environment from the current feature branch."""
        if not vercel_token or not vercel_token.strip():
            return ""

        try:
            # Security Hardening: Token is injected via container environment; no cleartext token in command line
            cmd = "CI=1 npx -y vercel deploy --yes"
            code, output = sandbox.run_cmd(cmd, timeout=25)
            if code != 0:
                print(f"[Vercel Preview] Deployment skipped or unlinked: {output[-200:]}")
                return ""
            return self._extract_vercel_url(output, is_production=False)
        except Exception as e:
            print(f"[Vercel Preview Exception] {e}")
            return ""

    def deploy_to_vercel(self, sandbox, vercel_token: str) -> str:
        """Deploys the production release after merging to main."""
        fallback_url = f"https://github.com/{self.repo_full_name}"
        if not vercel_token or not vercel_token.strip():
            return fallback_url

        try:
            sandbox.run_cmd(f"git checkout {self.base_branch} && git pull origin {self.base_branch}", timeout=25)
            # Security Hardening: Token is injected via container environment; no cleartext token in command line
            cmd = "CI=1 npx -y vercel deploy --prod --yes"
            code, output = sandbox.run_cmd(cmd, timeout=30)
            if code != 0:
                print(f"[Vercel Prod Deploy] CLI finished with exit {code}")
                return fallback_url
            extracted = self._extract_vercel_url(output, is_production=True)
            return extracted or fallback_url
        except Exception as e:
            print(f"[Vercel Prod Deploy Exception] {e}")
            return fallback_url


