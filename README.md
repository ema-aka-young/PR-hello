# PR Dashboard

A lightweight dashboard for tracking open PRs in `RedHatQE/openshift-virtualization-tests`, grouped by SIG labels, showing what's blocking each PR from merge.

## Quick Start

```bash
# 1. Install dependencies
cd backend
pip install -r requirements.txt

# 2. Set your GitHub token
cp ../.env.example ../.env
# Edit .env and add your token

# 3. Run
export $(grep -v '^#' ../.env | xargs)
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000 in your browser.

## Merge Requirements Tracked

| Requirement | Target | Badge |
|-------------|--------|-------|
| LGTM labels | 2 | Yellow — "Needs N more LGTM(s)" |
| Maintainer approval | 1 | Blue — "Needs Approver" |
| CI checks | All green | Red — "CI Failing" / Orange — "CI Pending" |
| All met | — | Green — "Ready to Merge" |

## API

- `GET /api/dashboard` — Returns enriched PR data grouped by SIG
- `GET /api/health` — Health check
- `GET /` — Serves the frontend

## Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `GITHUB_TOKEN` | _(none)_ | GitHub PAT for API auth |
| `GITHUB_REPO` | `RedHatQE/openshift-virtualization-tests` | Target repository |
| `CACHE_TTL_SECONDS` | `120` | API response cache duration |
