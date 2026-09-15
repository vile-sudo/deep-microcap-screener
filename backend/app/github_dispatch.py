"""
Fires a GitHub Actions workflow_dispatch from the live server, so an admin can
run "Sector research (by hand)" (sector-research.yml) with one click on the
dashboard instead of going to GitHub -- e.g. to refresh a sector's "Latest
developments" right after big news, without waiting for the 2 AM run.

Needs GH_DISPATCH_TOKEN: a fine-grained personal access token scoped to this
repo with "Actions: Read and write" permission (github.com/settings/tokens).
Nothing here runs Claude Code itself -- it only asks GitHub to start the
workflow, which does that (see .github/workflows/sector-research.yml).
"""
from __future__ import annotations

import requests

from .config import get_settings

API = "https://api.github.com"


class DispatchNotConfigured(Exception):
    pass


class DispatchError(Exception):
    """GitHub rejected the request; str(e) is safe to show the admin."""


def configured() -> bool:
    return bool(get_settings().gh_dispatch_token)


def dispatch(workflow: str, inputs: dict) -> None:
    s = get_settings()
    if not s.gh_dispatch_token:
        raise DispatchNotConfigured("GH_DISPATCH_TOKEN is not set on the server")
    url = f"{API}/repos/{s.github_repo}/actions/workflows/{workflow}/dispatches"
    try:
        r = requests.post(url, headers={
            "Authorization": f"Bearer {s.gh_dispatch_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }, json={"ref": s.github_branch, "inputs": inputs}, timeout=15)
    except requests.RequestException as e:
        raise DispatchError(f"could not reach GitHub: {e}") from e
    if r.status_code == 204:
        return
    if r.status_code in (401, 403):
        raise DispatchError("GitHub rejected the token (expired, wrong repo, or missing the Actions: Read and write permission)")
    if r.status_code == 404:
        raise DispatchError(f"workflow or repo not found ({s.github_repo}, {workflow}) -- check GITHUB_REPO/GITHUB_BRANCH")
    try:
        detail = r.json().get("message", "")
    except ValueError:
        detail = r.text[:200]
    raise DispatchError(f"GitHub returned HTTP {r.status_code}: {detail}")
