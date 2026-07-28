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

## What it does not do

- ACORN / Quercus login or any U of T authentication
- Course enrolment or waitlist changes
- Degree / prerequisite / eligibility decisions
- Exam schedules
- Saved timetables, accounts, or persistence
- Automatic schedule generation inside the server
- HTML scraping when the API is unavailable

## Privacy

No U of T credentials, cookies, or student account data are required or collected. The server only calls the public Timetable Builder API with the filters you (or the LLM) supply.

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
      ]
    }
  }
}
```

After restarting MCP, Cursor should discover exactly these four tools:

1. `get_reference_data`
2. `search_courses`
3. `get_course_details`
4. `check_conflicts`

## Tools

### `get_reference_data`

Return currently valid filter values. Sessions are **not hard-coded**.

Input:

```json
{}
```

### `search_courses`

Search by course code or title. Requires at least one session and one division. Results are concise summaries with pagination (`page` is one-based; `page_size` max 50).

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

### `get_course_details`

Fetch normalized course/section/meeting details for a course code in a required session. Optional `section_code` is the term half (`F`, `S`, `Y`), not a LEC/TUT name.

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

## Development

```bash
uv sync
uv run ruff format .
uv run ruff check .
uv run pyright
uv run pytest
```

Optional live API smoke test (excluded by default):

```bash
uv run pytest -m live
```

## Troubleshooting

### Timeouts / upstream unavailable

- Retry later; transient `429` / `5xx` / network timeouts are retried a few times
- Check network access to `api.easi.utoronto.ca`
- Increase `TIMETABLE_READ_TIMEOUT_SECONDS` if needed

### Upstream format changes

- Tool errors use stable codes such as `upstream_error` without stack traces
- If normalization breaks after an API change, update fixtures under `tests/fixtures/` and adjust `normalize.py` deliberately

### No search results

- Confirm session and division values via `get_reference_data` (do not guess old term codes)
- Try a broader title query, or a full course code like `CSC108H1`
- Empty results (`courses: []`, `total: 0`) are valid — not an error

## License

See [LICENSE](LICENSE).
