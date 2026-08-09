# uoft-timetable-mcp

Unofficial MCP server that gives an LLM current University of Toronto course and timetable data from the public Timetable Builder API.

The server is a **data and validation layer**. The LLM proposes schedules and explains trade-offs. Exact time overlap detection is handled by the deterministic `check_conflicts` tool so the model does not need to do time arithmetic itself.

## Disclaimer

This project is **not affiliated with, endorsed by, or supported by the University of Toronto**.

It uses the public Timetable Builder HTTP API with browser-like `Origin` / `Referer` headers. That is a common pattern for unofficial integrations, but it is **not an officially supported API**. U of T may change, rate-limit, or block access at any time. Use at your own risk and respect upstream terms of use.

## What it does

- Discover current sessions, divisions, campuses, delivery modes, and course levels
- Search courses by code or title with pagination
- Fetch normalized section, meeting, instructor, and enrolment details
- Deterministically check selected sections for time overlaps and transition gaps
- Optionally call Quercus (Canvas) with a personal access token (`quercus_whoami`, `quercus_list_courses`)

## What it does not do

- ACORN login, cookie/session scraping, or collecting UTORid/password
- Course enrolment or waitlist changes
- Degree / prerequisite / eligibility decisions
- Exam schedules
- Saved timetables, accounts, or persistence
- Automatic schedule generation inside the server
- HTML scraping when the API is unavailable

## Privacy

Timetable tools call the public Timetable Builder API only — no U of T credentials required. Optional Quercus tools use a **personal access token you create** in Quercus and place in the MCP server environment; the token is never logged or returned in tool output. Do not commit or share it.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

## Installation

```bash
git clone <repo-url> uoft-timetable-mcp
cd uoft-timetable-mcp
uv sync
```

## Run over stdio

```bash
uv run uoft-timetable-mcp
# or
uv run python -m uoft_timetable_mcp
```

## Cursor MCP configuration

Add something like this to your Cursor MCP settings (adjust the absolute path):

```json
{
  "mcpServers": {
    "uoft-timetable": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/uoft-timetable-mcp",
        "run",
        "uoft-timetable-mcp"
      ],
      "env": {
        "QUERCUS_ACCESS_TOKEN": "your-quercus-personal-access-token"
      }
    }
  }
}
```

`QUERCUS_ACCESS_TOKEN` is optional. Without it, timetable tools still work; Quercus tools return `quercus_auth_missing`.

After restarting MCP, Cursor should discover these tools:

1. `get_reference_data`
2. `search_courses`
3. `get_course_details`
4. `check_conflicts`
5. `quercus_whoami` (requires token)
6. `quercus_list_courses` (requires token)

## Tools

### `get_reference_data`

Return currently valid filter values. Sessions are **not hard-coded**.

Input:

```json
{}
```

### `search_courses`

Search by course code or title. Requires at least one session and one division. Results are concise summaries with pagination (`page` is one-based; `page_size` max 50).

Prefer **full** UofT course codes when known (e.g. `CSC108H1`, `CSCA08H3`). Students often omit the campus suffix (`CSCA08` vs `CSCA08H3`). Commonly, the final `H`/`Y` is course weight and the final digit is campus — usually `1` St. George, `3` UTSC, `5` UTM. Pick the matching division from `get_reference_data` (`ARTSC` / `APSC` St. George, `SCAR` UTSC, `ERIN` UTM). Short code-like queries are expanded using that division and are never treated as title searches.

Example input:

```json
{
  "query": "CSC108H1",
  "sessions": ["20269"],
  "divisions": ["ARTSC"],
  "page": 1,
  "page_size": 10
}
```

UTSC short-code example:

```json
{
  "query": "CSCA08",
  "sessions": ["20269"],
  "divisions": ["SCAR"],
  "page": 1,
  "page_size": 10
}
```

### `get_course_details`

Fetch normalized course/section/meeting details for a course code in a required session. Optional `section_code` is the term half (`F`, `S`, `Y`), not a LEC/TUT name. Short codes without a campus suffix are expanded across common `H1`/`Y1`/`H3`/`Y3`/`H5`/`Y5` forms when needed.

Example input:

```json
{
  "course_code": "CSC148H1",
  "session": "20269",
  "section_code": null
}
```

### `check_conflicts`

Resolve selected section meeting times from current timetable data and report overlaps / transition gaps.

**Verification rule for the LLM:** before telling a student a schedule is verified, call this tool with every selected section. Only claim verification when **all** of these are true:

- `has_conflicts` is `false`
- `transition_violations` is empty
- `is_complete` is `true`

If `is_complete` is `false`, say the schedule could not be fully verified.

Example input:

```json
{
  "session": "20269",
  "selections": [
    {
      "course_code": "CSC108H1",
      "section_names": ["LEC0201", "TUT0101"]
    },
    {
      "course_code": "MAT137Y1",
      "section_names": ["LEC0501", "TUT0401"]
    }
  ],
  "minimum_transition_minutes": 0
}
```

## Example prompts

- “Find Fall 2026 Arts & Science sections of CSC108H1.”
- “Show enrolment and meeting details for CSC148H1 in session 20269.”
- “Check whether these selected lecture and tutorial sections overlap.”
- “Create a schedule from these courses, avoid Fridays, and use the conflict tool to verify the final choice.”

## Optional Quercus tools

Quercus support is **optional** and **unofficial**. This project is not affiliated with the University of Toronto.

1. In Quercus, create a personal access token: **Account → Settings → New Access Token** (wording may vary; see [Canvas personal access tokens](https://community.canvaslms.com/t5/Canvas-Basics-Guide/How-do-I-manage-API-access-tokens-as-an-account-admin/ta-p/615312) / your campus Quercus help).
2. Set `QUERCUS_ACCESS_TOKEN` in the MCP server environment (for example the Cursor `mcp.json` `env` block above).
3. Phase 1 tools:
   - `quercus_whoami` — verify the token and return the current user
   - `quercus_list_courses` — list courses with Canvas `id`, `name`, and `course_code`

A personal access token is equivalent to account access. Do not commit it, share it, or paste it into chat logs.

## Data source and freshness

- Upstream API: `https://api.easi.utoronto.ca/ttb`
- Reference data is cached in memory for 15 minutes (`TIMETABLE_REFERENCE_CACHE_TTL_SECONDS`)
- Search / details / conflicts fetch current upstream data (conflicts always re-resolve selected sections)
- Enrolment numbers and meeting rooms can change; treat responses as point-in-time

### Optional environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `TIMETABLE_BASE_URL` | `https://api.easi.utoronto.ca/ttb` | Upstream API root |
| `TIMETABLE_CONNECT_TIMEOUT_SECONDS` | `5` | Connect timeout |
| `TIMETABLE_READ_TIMEOUT_SECONDS` | `20` | Read timeout |
| `TIMETABLE_REFERENCE_CACHE_TTL_SECONDS` | `900` | Reference-data cache TTL |
| `TIMETABLE_MAX_PAGE_SIZE` | `50` | Max search page size |
| `TIMETABLE_MAX_CONCURRENCY` | `5` | Max concurrent course lookups |
| `QUERCUS_ACCESS_TOKEN` | _(unset)_ | Quercus personal access token |
| `QUERCUS_BASE_URL` | `https://q.utoronto.ca` | Quercus site root |
| `QUERCUS_CONNECT_TIMEOUT_SECONDS` | `5` | Quercus connect timeout |
| `QUERCUS_READ_TIMEOUT_SECONDS` | `20` | Quercus read timeout |
| `QUERCUS_MAX_ATTEMPTS` | `3` | Quercus retry attempts |
| `QUERCUS_MAX_PAGE_SIZE` | `100` | Canvas `per_page` cap |

## Development

```bash
uv sync
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
```

Optional live API smoke tests (excluded by default):

```bash
uv run pytest -m live
uv run pytest -m live_quercus
```

## Troubleshooting

### Timeouts / upstream unavailable

- Retry later; transient `429` / `5xx` / network timeouts are retried a few times
- Check network access to `api.easi.utoronto.ca`
- Increase `TIMETABLE_READ_TIMEOUT_SECONDS` if needed

### Upstream format changes

- Tool errors use stable codes such as `upstream_error` without stack traces
- If normalization breaks after an API change, update fixtures under `tests/fixtures/` and adjust `timetable/normalize.py` deliberately

### No search results

- Confirm session and division values via `get_reference_data` (do not guess old term codes)
- Try a broader title query, or a full course code like `CSC108H1`
- Empty results (`courses: []`, `total: 0`) are valid — not an error

## License

See [LICENSE](LICENSE).
