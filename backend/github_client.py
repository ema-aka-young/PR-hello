import os
import asyncio
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
REPO = os.getenv("GITHUB_REPO", "RedHatQE/openshift-virtualization-tests")
LGTM_TARGET = 2


def _headers() -> dict[str, str]:
    token = os.getenv("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _get_all_pages(client: httpx.AsyncClient, url: str, params: dict | None = None) -> list[dict]:
    params = params or {}
    params.setdefault("per_page", "100")
    results: list[dict] = []
    page = 1
    while True:
        params["page"] = str(page)
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        results.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return results



async def _fetch_commit_status(client: httpx.AsyncClient, sha: str) -> dict:
    url = f"{GITHUB_API}/repos/{REPO}/commits/{sha}/status"
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


async def _fetch_check_runs(client: httpx.AsyncClient, sha: str) -> list[dict]:
    url = f"{GITHUB_API}/repos/{REPO}/commits/{sha}/check-runs"
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.json().get("check_runs", [])


LGTM_BOTS = {"coderabbitai[bot]", "qodo-code-review[bot]", "polarion-jenkins", "cnv-qe-bot1"}


def _count_lgtm_labels(labels: list[dict]) -> int:
    count = 0
    for lb in labels:
        name = lb["name"].lower()
        if name == "lgtm":
            count += 1
        elif name.startswith("lgtm-"):
            user = name[5:]
            if user not in LGTM_BOTS:
                count += 1
    return count


def _has_maintainer_approval(missing_approvers: list[str], labels: list[dict]) -> bool:
    if missing_approvers:
        return False
    return any(lb["name"].lower().startswith("approved-") for lb in labels)


META_CHECKS = {"can-be-merged"}


def _resolve_ci_state(combined_status: dict, check_runs: list[dict]) -> str:
    status_state = combined_status.get("state", "pending")

    ci_runs = [cr for cr in check_runs if cr.get("name", "").lower() not in META_CHECKS]

    if ci_runs:
        completed = [cr for cr in ci_runs if cr.get("status") == "completed"]
        any_failure = any(
            cr.get("conclusion") in ("failure", "cancelled", "timed_out")
            for cr in completed
        )
        any_pending = any(cr.get("status") != "completed" for cr in ci_runs)
        all_ok = all(
            cr.get("conclusion") in ("success", "skipped", "neutral")
            for cr in completed
        )

        if any_failure:
            return "failure"
        if any_pending:
            return "pending"
        if all_ok and completed:
            return "success"

    return status_state


def _extract_missing_approvers(check_runs: list[dict]) -> list[str]:
    for cr in check_runs:
        if cr.get("name", "").lower() != "can-be-merged":
            continue
        text = (cr.get("output") or {}).get("text") or ""
        if "missing approved from approvers:" in text.lower():
            names_part = text.lower().split("missing approved from approvers:")[-1]
            return [n.strip() for n in names_part.split(",") if n.strip()]
    return []


BLOCKER_LABELS = {"wip", "hold", "has-conflicts"}


def _compute_blockers(
    lgtm_count: int,
    approved: bool,
    ci_state: str,
    missing_approvers: list[str],
    label_names: list[str],
    mergeable: bool,
) -> list[dict[str, str]]:
    blockers = []

    for lbl in label_names:
        if lbl.lower() in BLOCKER_LABELS:
            tag = lbl.lower()
            if tag == "wip":
                blockers.append({"key": "wip", "label": "Work in Progress", "severity": "gray"})
            elif tag == "hold":
                blockers.append({"key": "hold", "label": "On Hold", "severity": "gray"})
            elif tag == "has-conflicts":
                blockers.append({"key": "has_conflicts", "label": "Has Conflicts", "severity": "red"})

    if not mergeable:
        if not any(b["key"] == "has_conflicts" for b in blockers):
            blockers.append({"key": "has_conflicts", "label": "Has Conflicts", "severity": "red"})

    if ci_state == "failure":
        blockers.append({"key": "ci_failing", "label": "CI Failing", "severity": "red"})
    elif ci_state == "pending":
        blockers.append({"key": "ci_pending", "label": "CI Pending", "severity": "orange"})

    if lgtm_count < LGTM_TARGET:
        needed = LGTM_TARGET - lgtm_count
        word = "LGTM" if needed == 1 else "LGTMs"
        blockers.append({"key": "needs_lgtm", "label": f"Needs {needed} more {word}", "severity": "yellow"})

    if not approved:
        if missing_approvers:
            names = ", ".join(missing_approvers)
            label = f"Needs approval from: {names}"
        else:
            label = "Needs Maintainer Approval"
        blockers.append({"key": "needs_approval", "label": label, "severity": "blue"})

    if "verified" not in [l.lower() for l in label_names]:
        blockers.append({"key": "needs_verified", "label": "Not Verified", "severity": "purple"})

    return blockers


def _extract_sigs(labels: list[dict]) -> list[str]:
    return sorted(lb["name"] for lb in labels if lb["name"].lower().startswith(("sig-", "sig/")))


async def _enrich_pr(client: httpx.AsyncClient, pr: dict) -> dict[str, Any]:
    pr_number = pr["number"]
    sha = pr["head"]["sha"]

    combined_status, check_runs = await asyncio.gather(
        _fetch_commit_status(client, sha),
        _fetch_check_runs(client, sha),
    )

    labels = pr.get("labels", [])
    label_names = [lb["name"] for lb in labels]
    lgtm_count = _count_lgtm_labels(labels)
    ci_state = _resolve_ci_state(combined_status, check_runs)
    missing_approvers = _extract_missing_approvers(check_runs)
    approved = _has_maintainer_approval(missing_approvers, labels)
    mergeable = pr.get("mergeable", True) is not False
    blockers = _compute_blockers(
        lgtm_count, approved, ci_state, missing_approvers, label_names, mergeable,
    )
    sigs = _extract_sigs(labels)
    can_be_merged = "can-be-merged" in label_names

    return {
        "number": pr_number,
        "title": pr["title"],
        "url": pr["html_url"],
        "author": pr["user"]["login"],
        "author_avatar": pr["user"]["avatar_url"],
        "created_at": pr["created_at"],
        "updated_at": pr["updated_at"],
        "draft": pr.get("draft", False),
        "labels": label_names,
        "sigs": sigs if sigs else ["Unassigned SIG"],
        "lgtm_count": lgtm_count,
        "approved": approved,
        "ci_state": ci_state,
        "blockers": [] if can_be_merged else blockers,
        "can_be_merged": can_be_merged,
    }


async def fetch_dashboard_data() -> dict[str, Any]:
    url = f"{GITHUB_API}/repos/{REPO}/pulls"
    params = {"state": "open", "sort": "updated", "direction": "desc"}

    async with httpx.AsyncClient(headers=_headers(), timeout=30.0) as client:
        open_prs = await _get_all_pages(client, url, params)

        semaphore = asyncio.Semaphore(10)

        async def bounded_enrich(pr: dict) -> dict:
            async with semaphore:
                return await _enrich_pr(client, pr)

        enriched = await asyncio.gather(*(bounded_enrich(pr) for pr in open_prs))

    sig_groups: dict[str, list] = {}
    for pr_data in enriched:
        for sig in pr_data["sigs"]:
            sig_groups.setdefault(sig, []).append(pr_data)

    for prs in sig_groups.values():
        prs.sort(key=lambda p: (not p["can_be_merged"], len(p["blockers"]), p["updated_at"]), reverse=True)

    return {
        "repo": REPO,
        "total_open": len(open_prs),
        "sig_groups": dict(sorted(sig_groups.items())),
    }
