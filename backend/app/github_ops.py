import time
from github import Github, GithubException


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
            if e.status == 422 and "No commits between" in str(e):
                raise RuntimeError(
                    f"GitHub PR failed: The branch '{branch_name}' has 0 commits ahead of '{self.base_branch}'. "
                    f"The codebase was already in sync with production."
                )
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

    def deploy_to_vercel(self, sandbox, vercel_token: str) -> str:
        if not vercel_token:
            return "https://vercel.com (Set VERCEL_TOKEN in .env for instant CLI URL)"
        sandbox.run_cmd(f"git checkout {self.base_branch} && git pull origin {self.base_branch}")

        # Deploy directly inside the repository
        cmd = f"npx vercel deploy --prod --yes --token={vercel_token}"
        code, output = sandbox.run_cmd(cmd)
        if code != 0:
            return f"Vercel CLI warning: {output[-300:]}"

        import re

        # 1. Look for explicit "Production: https://..." in Vercel CLI output
        prod_match = re.search(r'Production:\s*(https://[^\s]+)', output)
        if prod_match:
            return prod_match.group(1).strip().rstrip(".")

        # 2. Extract all live .vercel.app URLs from CLI output (filtering out vercel.com inspect links)
        live_urls = []
        for line in output.splitlines():
            line_str = line.strip()
            matches = re.findall(r'https://[a-zA-Z0-9\-\._]+\.vercel\.app', line_str)
            for m in matches:
                if "vercel.com" not in m:
                    live_urls.append(m.rstrip("."))

        if live_urls:
            # The last emitted vercel.app URL by Vercel CLI is always the production alias URL
            return live_urls[-1]

        # 3. Fallback: Any https:// line from output that isn't a dashboard inspect link
        fallback_urls = [
            line.strip()
            for line in output.splitlines()
            if line.strip().startswith("https://") and "vercel.com" not in line
        ]
        if fallback_urls:
            return fallback_urls[-1]

        return "https://vercel.com"

