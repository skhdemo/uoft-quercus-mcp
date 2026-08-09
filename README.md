# uoft-quercus-mcp

Unofficial MCP server for University of Toronto students: **Quercus (Canvas)** tools via a personal access token, plus public **Timetable Builder** helpers for schedule search and conflict checks.

## Disclaimer

This project is **not affiliated with, endorsed by, or supported by the University of Toronto**.

- Quercus tools use the Canvas API with a **personal access token you create**. That is not an official Quercus product.
- Timetable tools use the public Timetable Builder HTTP API (separate from Quercus). U of T may change, rate-limit, or block access at any time.

Use at your own risk and respect upstream terms of use.

## What it does

**Quercus (requires `QUERCUS_ACCESS_TOKEN`):**

- Verify your Quercus identity
- List enrolled courses
- Todo items and upcoming events
- Assignments and announcements (announcements use term start, else last 365 days, through today)
- Modules and course files
- Download files and extract text from PDF/DOCX/plain text (no OCR)

**Timetable Builder (no Quercus token):**

- Discover current sessions, divisions, campuses, delivery modes, and course levels
- Search courses by code or title
- Fetch section, meeting, instructor, and enrolment details
- Deterministically check selected sections for time overlaps and transition gaps

## What it does not do

- UTORid / password login, cookie scraping, or session hijacking
- Hosted OAuth / multi-user Quercus product (personal token only for now)
- Course enrolment or waitlist changes
- Degree / prerequisite / eligibility decisions
- Exam schedules
- Saved timetables, accounts, or persistence
- OCR for scanned PDFs/images
- Automatic schedule generation inside the server

## Privacy

- Quercus: put your personal access token only in the MCP server `env` (for example Cursor `mcp.json`). The token is never logged or returned in tool output. Do not commit or share it.
- Downloads stay on your machine under `QUERCUS_DOWNLOAD_DIR`.
- Timetable tools call the public Timetable Builder API only — no U of T credentials required.

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)

## Installation

```bash
git clone git@github.com:skhdemo/uoft-quercus-mcp.git
cd uoft-quercus-mcp
uv sync
```

If your local folder is still named `uoft-timetable-mcp`, that is fine until you rename it; clone/docs examples use the new name.

## Create a Quercus personal access token

1. Sign in to [Quercus](https://q.utoronto.ca).
2. Open **Account → Settings → New Access Token** (wording may vary by campus/theme).
3. Copy the token once when it is shown.
4. Put it only in MCP `env` as `QUERCUS_ACCESS_TOKEN` — never in chat, commits, or screenshots.

Background on Canvas tokens: [How do I manage API access tokens?](https://community.canvaslms.com/t5/Canvas-Basics-Guide/How-do-I-manage-API-access-tokens-as-an-account-admin/ta-p/615312) (campus Quercus help may differ slightly).

Without a token, Timetable tools still work; Quercus tools return `quercus_auth_missing`.

## Run over stdio

```bash
uv run uoft-quercus-mcp
# or
uv run python -m uoft_quercus_mcp
```

A deprecated console-script alias `uoft-timetable-mcp` still points at the same entry point for old Cursor configs. Prefer `uoft-quercus-mcp`.

## Cursor MCP configuration

Add something like this to your Cursor MCP settings (adjust the absolute path):

```json
{
  "mcpServers": {
    "uoft-quercus": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/uoft-quercus-mcp",
        "run",
        "uoft-quercus-mcp"
      ],
      "env": {
        "QUERCUS_ACCESS_TOKEN": "your-quercus-personal-access-token"
      }
    }
  }
}
```

After restarting MCP, Cursor should discover Quercus tools (token required) and Timetable tools:

- Quercus: `quercus_whoami`, `quercus_list_courses`, `quercus_list_todo`, `quercus_list_assignments`, `quercus_list_announcements`, `quercus_list_modules`, `quercus_list_files`, `quercus_get_file`
- Timetable: `get_reference_data`, `search_courses`, `get_course_details`, `check_conflicts`

## Quick start prompts

- “What’s due on Quercus?”
- “List modules for MATA22 and open the past quiz PDF, then help with question 4.”
- “Search Fall timetable for CSC108H1 and check conflicts for these lecture/tutorial sections.”

## Tools

### Quercus tools

| Tool | Purpose |
|------|---------|
| `quercus_whoami` | Verify the token / identity |
| `quercus_list_courses` | Canvas `id` + `course_code` + `name` |
| `quercus_list_todo` | Todo items and upcoming events |
| `quercus_list_assignments` | Course-scoped assignments (full list) |
| `quercus_list_announcements` | Course-scoped; term start (else last 365 days) through today |
| `quercus_list_modules` | Modules / materials map (`file_id` on File items) |
| `quercus_list_files` | Course Files area |
| `quercus_get_file` | `mode=metadata\|text\|download` (PDF/DOCX/text extraction; no OCR) |

Course-scoped tools accept a Canvas id, code fragment (`MATA22`), or name fragment. Prefer ids from `quercus_list_courses` when ambiguous.

Past quiz PDF flow: `quercus_list_modules` or `quercus_list_files` → `quercus_get_file` with `mode=text`.

### Timetable tools

#### `get_reference_data`

Return currently valid filter values. Sessions are **not hard-coded**.

```json
{}
```

#### `search_courses`

Search by course code or title. Requires at least one session and one division. Results are concise summaries with pagination (`page` is one-based; `page_size` max 50).

Prefer **full** UofT course codes when known (e.g. `CSC108H1`, `CSCA08H3`). Students often omit the campus suffix (`CSCA08` vs `CSCA08H3`). Commonly, the final `H`/`Y` is course weight and the final digit is campus — usually `1` St. George, `3` UTSC, `5` UTM. Pick the matching division from `get_reference_data` (`ARTSC` / `APSC` St. George, `SCAR` UTSC, `ERIN` UTM). Short code-like queries are expanded using that division and are never treated as title searches.

```json
{
  "query": "CSC108H1",
  "sessions": ["20269"],
  "divisions": ["ARTSC"],
  "page": 1,
  "page_size": 10
}
```

#### `get_course_details`

Fetch normalized course/section/meeting details for a course code in a required session. Optional `section_code` is the term half (`F`, `S`, `Y`), not a LEC/TUT name.

```json
{
  "course_code": "CSC148H1",
  "session": "20269",
  "section_code": null
}
```

#### `check_conflicts`

Resolve selected section meeting times from current timetable data and report overlaps / transition gaps.

**Verification rule for the LLM:** before telling a student a schedule is verified, call this tool with every selected section. Only claim verification when **all** of these are true:

- `has_conflicts` is `false`
- `transition_violations` is empty
- `is_complete` is `true`

If `is_complete` is `false`, say the schedule could not be fully verified.

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

## Data sources and freshness

- Quercus: `https://q.utoronto.ca` (Canvas API; needs personal access token)
- Timetable Builder: `https://api.easi.utoronto.ca/ttb`
- Reference data is cached in memory for 15 minutes (`TIMETABLE_REFERENCE_CACHE_TTL_SECONDS`)
- Search / details / conflicts fetch current upstream data
- Enrolment numbers, rooms, and Quercus content can change; treat responses as point-in-time

### Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `QUERCUS_ACCESS_TOKEN` | _(unset)_ | Quercus personal access token |
| `QUERCUS_BASE_URL` | `https://q.utoronto.ca` | Quercus site root |
| `QUERCUS_CONNECT_TIMEOUT_SECONDS` | `5` | Quercus connect timeout |
| `QUERCUS_READ_TIMEOUT_SECONDS` | `20` | Quercus read timeout |
| `QUERCUS_MAX_ATTEMPTS` | `3` | Quercus retry attempts |
| `QUERCUS_MAX_PAGE_SIZE` | `100` | Canvas `per_page` cap |
| `QUERCUS_MAX_CONCURRENCY` | `5` | Max concurrent module-item fetches |
| `QUERCUS_DOWNLOAD_DIR` | `~/.cache/uoft-quercus-mcp/files` | Local download directory |
| `QUERCUS_COURSE_CACHE_TTL_SECONDS` | `120` | Course-list resolver cache TTL |
| `QUERCUS_MAX_DOWNLOAD_BYTES` | `26214400` | Max file download size (25 MiB) |
| `QUERCUS_MAX_TEXT_CHARS` | `200000` | Max extracted text characters |
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

Optional live API smoke tests (excluded by default):

```bash
uv run pytest -m live
uv run pytest -m live_quercus
```

## Troubleshooting

### Quercus auth missing / rejected

- Confirm `QUERCUS_ACCESS_TOKEN` is set in the MCP server environment (Cursor `mcp.json` `env` does not apply to a plain shell `pytest`)
- Regenerate the token in Quercus if it was revoked
- Some Canvas features may return `quercus_forbidden` depending on course permissions

### Timeouts / upstream unavailable

- Retry later; transient `429` / `5xx` / network timeouts are retried a few times
- Check network access to `q.utoronto.ca` and/or `api.easi.utoronto.ca`
- Increase `QUERCUS_READ_TIMEOUT_SECONDS` or `TIMETABLE_READ_TIMEOUT_SECONDS` if needed

### No timetable search results

- Confirm session and division values via `get_reference_data` (do not guess old term codes)
- Try a broader title query, or a full course code like `CSC108H1`
- Empty results (`courses: []`, `total: 0`) are valid — not an error

### Local folder still named `uoft-timetable-mcp`

The GitHub repo is `uoft-quercus-mcp`. Renaming your local checkout folder is optional; update Cursor `--directory` paths if you do.

## License

See [LICENSE](LICENSE).
