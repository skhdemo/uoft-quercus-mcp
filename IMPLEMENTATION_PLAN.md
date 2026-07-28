# UofT Timetable MCP — Implementation Plan

## 1. Purpose

Build a Python MCP server that gives an LLM reliable, current University of Toronto course and timetable data from the public Timetable Builder API.

The server is a data and validation layer. The LLM remains responsible for understanding a student's preferences, proposing schedules, and explaining trade-offs. Exact overlap detection is handled by a deterministic `check_conflicts` tool so the LLM does not need to perform time arithmetic itself.

This is an unofficial integration. It must not require or collect U of T credentials, access ACORN, enrol students, or claim affiliation with the University of Toronto.

This project uses the public Timetable Builder HTTP API with `Origin` and `Referer` headers that mimic the official website. That is common for unofficial integrations but is not an officially supported API. U of T may change, rate-limit, or block access at any time.

## 2. V1 scope

Implement these MCP tools:

1. `get_reference_data`
   - Return valid current sessions, divisions, campuses, delivery modes, course levels, and related search values.
2. `search_courses`
   - Search courses by code/title and optional filters.
   - Return concise course summaries and pagination metadata.
3. `get_course_details`
   - Return normalized course, section, meeting, instructor, availability, waitlist, and location information.
4. `check_conflicts`
   - Resolve selected section identifiers against current API data.
   - Deterministically report all time overlaps and unresolved selections.

Explicitly out of scope for V1:

- ACORN or Quercus authentication.
- Course enrolment or waitlist changes.
- Degree requirement validation.
- Prerequisite interpretation or eligibility decisions.
- Exam schedules.
- Persistence, user accounts, or saved timetables.
- A schedule-generation algorithm inside the MCP server.
- Scraping HTML when the timetable API is unavailable.

## 3. Upstream API

Base URL:

```text
https://api.easi.utoronto.ca/ttb
```

The API is public but undocumented and may change. Keep all upstream-specific code inside one client module.

### 3.1 Required request headers

Send:

```text
Accept: application/json
Content-Type: application/json        # POST only
Origin: https://ttb.utoronto.ca
Referer: https://ttb.utoronto.ca/
User-Agent: uoft-timetable-mcp/<version>
```

Do not copy browser-only headers such as `Sec-CH-UA`.

### 3.2 Endpoints verified during discovery

#### Reference data

```http
GET /reference-data
```

The response contains current session codes and valid filters. Session values are not to be hard-coded. For example, at discovery time:

- `20265F`: Summer 2026 first sub-session
- `20265S`: Summer 2026 second sub-session
- `20265`: Summer 2026 full session
- `20269`: Fall 2026
- `20271`: Winter 2027
- `20269-20271`: Fall–Winter 2026–2027

These are examples only. Always obtain live values from `reference-data`.

#### Pageable course search

```http
POST /getPageableCourses
```

Known-good body:

```json
{
  "courseCodeAndTitleProps": {
    "courseCode": "CSC108H1",
    "courseTitle": "",
    "courseSectionCode": "",
    "searchCourseDescription": false
  },
  "departmentProps": [],
  "campuses": [],
  "sessions": ["20269"],
  "requirementProps": [],
  "instructor": "",
  "courseLevels": [],
  "deliveryModes": [],
  "dayPreferences": [],
  "timePreferences": [],
  "divisions": ["ARTSC"],
  "creditWeights": [],
  "availableSpace": false,
  "waitListable": false,
  "page": 1,
  "pageSize": 10,
  "direction": "asc"
}
```

Important:

- `sessions` and `divisions` are required by the Timetable Builder search flow.
- `page` is one-based.
- Keep `pageSize` bounded to avoid very large API and MCP responses.
- Successful course data is under `payload.pageableCourse`.
- HTTP success does not guarantee application success. Inspect `payload` and `status`.

#### Lookup by course code

```http
GET /getCoursesByCodeAndSectionCode/{course_code}
GET /getCoursesByCodeAndSectionCode/{course_code}?sectionCode={section_code}
```

Use the code-only form for details, then filter records by requested session and/or section term-half locally. URL-encode all path and query values.

**Important:** the upstream query parameter `sectionCode` means the course **term half** (`F`, `S`, `Y`), not a lecture/tutorial component such as `LEC0201`. Prefer the code-only lookup and filter locally.

#### Terminology glossary

| Term | Example | Meaning | Used in |
|------|---------|---------|---------|
| `course_code` | `CSC108H1` | Full course identifier | all tools |
| `session` | `20269`, `20269-20271` | Academic term/session code from reference data | search, details, conflicts |
| `section_code` / term half | `F`, `S`, `Y` | Which half or full-year offering of the course | `get_course_details` input; course model field `section_code` |
| `section_name` / component | `LEC0201`, `TUT0101` | Lecture, tutorial, or practical section | `check_conflicts` input; Section model field `name` |

Do not confuse `section_code` (term half) with `section_name` (LEC/TUT component).

### 3.3 Upstream response handling

Treat a response as an upstream application error when:

- JSON decoding fails.
- `payload` is null.
- `status` contains one or more errors.
- Expected keys are absent or have incompatible types.

Never silently return an empty result for a malformed upstream response. Return an MCP-friendly error with a stable category and a useful message, without exposing stack traces.

Suggested exception hierarchy:

- `TimetableError`
- `TimetableNetworkError`
- `TimetableTimeoutError`
- `TimetableRateLimitError`
- `TimetableUpstreamError`
- `TimetableValidationError`
- `CourseNotFoundError`

Use explicit connect/read timeouts. Retry only transient failures (`429`, `502`, `503`, `504`, connection reset, timeout), with a small capped exponential backoff and no more than three attempts. Respect `Retry-After` when provided. Do not retry other `4xx` responses.

Map domain exceptions to MCP tool errors with stable codes and user-safe messages. Do not return stack traces. Example shapes:

```json
{
  "error": {
    "code": "course_not_found",
    "message": "No course records matched CSC999H1 in session 20269.",
    "retryable": false
  }
}
```

```json
{
  "error": {
    "code": "upstream_unavailable",
    "message": "Timetable Builder did not return course data. Try again shortly.",
    "retryable": true
  }
}
```

Suggested code mapping:

| Exception | MCP error code | Retryable |
|-----------|----------------|-----------|
| `TimetableValidationError` | `invalid_input` | false |
| `CourseNotFoundError` | `course_not_found` | false |
| `TimetableTimeoutError` | `upstream_timeout` | true |
| `TimetableRateLimitError` | `rate_limited` | true |
| `TimetableNetworkError` | `network_error` | true |
| `TimetableUpstreamError` | `upstream_error` | varies |

Validation errors from Pydantic tool inputs should surface as MCP invalid-params errors before any upstream call.

Endpoint-specific not-found rules:

- `search_courses`: valid empty search returns `courses: []` with `total: 0` (not an error).
- `get_course_details`: no matching records after filtering raises `CourseNotFoundError`.
- `check_conflicts`: unresolved selections go in the tool success payload under `unresolved`; do not raise for individual missing sections.

### 3.4 Upstream response shapes

These are minimal schemas for implementation and fixtures. Field names must match upstream JSON exactly in the client layer.

#### `GET /reference-data`

Top-level envelope:

```json
{
  "payload": {
    "currentSessions": [],
    "divisions": [],
    "campuses": [],
    "requirements": [],
    "courseLevels": [],
    "deliveryModes": [],
    "temporaryDeliveryModes": [],
    "creditWeights": [],
    "dayPreferences": [],
    "timePreferences": [],
    "directions": { "asc": "...", "desc": "..." }
  },
  "status": []
}
```

Each selectable option item:

```json
{
  "label": "Fall 2026 (F)",
  "value": "20269",
  "selected": false,
  "header": false,
  "group": "FallWinter-20269-20271",
  "metadata": { "name": null, "type": null }
}
```

Normalization rules:

- Drop items where `header` is `true` from selectable outputs.
- Preserve `label`, `value`, and optional `group`.
- V1 exposes: sessions, divisions, campuses, delivery modes, course levels.

#### `POST /getPageableCourses`

Success envelope:

```json
{
  "payload": {
    "pageableCourse": {
      "total": 1,
      "courses": []
    },
    "divisionalLegends": [],
    "divisionalEnrolmentIndicators": []
  },
  "status": []
}
```

Each course in search results includes at least: `id`, `code`, `name`, `sectionCode`, `campus`, `sessions`, `sections`.

Pagination:

- `total` is the total match count across all pages.
- Compute `has_next_page` as `page * page_size < total`.

#### `GET /getCoursesByCodeAndSectionCode/{course_code}`

Success envelope uses the same `payload.pageableCourse.courses` structure as search.

Course record keys observed: `id`, `name`, `code`, `sectionCode`, `campus`, `sessions`, `sections`, `department`, `faculty`, `title`, `notes`, `cancelInd`, and related metadata.

Section record keys observed: `name`, `type`, `teachMethod`, `sectionNumber`, `meetingTimes`, `instructors`, `currentEnrolment`, `maxEnrolment`, `cancelInd`, `waitlistInd`, `deliveryModes`, `currentWaitlist`, `enrolmentInd`, `tbaInd`, `notes`, `enrolmentControls`.

Meeting record keys observed: `start`, `end`, `building`, `sessionCode`, `repetition`, `repetitionTime`.

Each meeting `start`/`end` contains `{ "day": 1, "millisofday": 46800000 }`.

Each instructor contains `{ "firstName": "...", "lastName": "..." }`.

### 3.5 Session matching rules

When filtering course records by a requested `session` value `S`:

1. Include the record if `S` is an element of `course.sessions`.
2. Include the record if any element of `course.sessions` is a combined range whose endpoints contain `S` (for example, `20269-20271` matches both `20269` and `20271`).
3. When matching combined ranges, split on `-` only for known multi-session patterns; treat single tokens such as `20265F` as exact values.
4. If `section_code` (term half) is also supplied, require `course.sectionCode == section_code`.

Apply the same session-matching helper in `get_course_details` and `check_conflicts`.

## 4. Technology choices

- Python 3.11 or newer.
- `fastmcp` for the MCP server and in-memory MCP client tests.
- `httpx` with one reusable `AsyncClient`.
- Pydantic models for tool inputs and normalized output.
- `pytest`, `pytest-asyncio`, and `respx` for tests.
- `ruff` for formatting/linting.
- `pyright` for static type checking.
- `uv` for dependency and command management.

Do not pin versions by guessing. When implementation begins, add current compatible releases with `uv add`, commit the resulting lockfile, and record the supported Python version in `pyproject.toml`.

Use asynchronous I/O throughout the API client and MCP tools. Do not create a new HTTP client for every call.

## 5. Proposed project structure

```text
uoft-timetable-mcp/
├── pyproject.toml
├── uv.lock
├── README.md
├── LICENSE
├── src/
│   └── uoft_timetable_mcp/
│       ├── __init__.py
│       ├── __main__.py
│       ├── server.py
│       ├── client.py
│       ├── models.py
│       ├── normalize.py
│       ├── conflicts.py
│       ├── errors.py
│       └── settings.py
└── tests/
    ├── fixtures/
    │   ├── reference_data.json
    │   ├── search_csc108.json
    │   ├── course_csc108.json
    │   └── upstream_error.json
    ├── test_client.py
    ├── test_normalize.py
    ├── test_conflicts.py
    ├── test_tools.py
    └── test_smoke_stdio.py
```

Responsibilities:

- `server.py`: FastMCP instance and thin tool functions.
- `client.py`: all HTTP requests, retries, response validation, and upstream payload construction.
- `models.py`: Pydantic input/output models and enums.
- `normalize.py`: upstream-to-public-model conversion.
- `conflicts.py`: pure overlap logic with no HTTP or MCP dependency.
- `settings.py`: base URL, timeout, retry, page-size cap, cache TTL, and version.
- `errors.py`: stable domain exceptions and user-safe error mapping.

Configurable settings (environment variables with defaults in code):

| Setting | Env var | Purpose |
|---------|---------|---------|
| Base URL | `TIMETABLE_BASE_URL` | Upstream API root |
| Connect timeout | `TIMETABLE_CONNECT_TIMEOUT_SECONDS` | HTTP connect timeout |
| Read timeout | `TIMETABLE_READ_TIMEOUT_SECONDS` | HTTP read timeout |
| Reference cache TTL | `TIMETABLE_REFERENCE_CACHE_TTL_SECONDS` | Default 900 (15 min) |
| Max page size | `TIMETABLE_MAX_PAGE_SIZE` | Default 50 |
| Max upstream concurrency | `TIMETABLE_MAX_CONCURRENCY` | Default 5 |

Tool functions should orchestrate modules, not contain API parsing or conflict algorithms.

## 6. Public normalized data model

Do not return the full raw upstream response. It is large, unstable, and token-expensive. Normalize it into a stable public shape.

### 6.1 Meeting

```json
{
  "day": "Monday",
  "day_number": 1,
  "start": "13:00",
  "end": "14:00",
  "start_minutes": 780,
  "end_minutes": 840,
  "location": "PB B250",
  "building_code": "PB",
  "room": "B250",
  "building_url": "https://map.utoronto.ca/...",
  "session_code": "20269",
  "repetition": "WEEKLY"
}
```

`start_minutes` and `end_minutes` are minutes after midnight and are the canonical conflict-comparison values. Human-readable strings are included for the LLM and users.

Upstream time conversion:

```text
minutes_after_midnight = millisofday // 60000
HH:MM = divmod(minutes_after_midnight, 60)
```

Reject or mark invalid values outside `0..86_400_000` milliseconds. Do not silently wrap them.

Determine the upstream day-number mapping from verified fixture data and document it in code. Current observations indicate `1 = Monday`; add tests for every supported day. Unknown values must become `"Unknown"` and must not be used for conflict claims.

V1 repetition policy:

- Treat meetings with `repetition: "WEEKLY"` as recurring weekly on their stated day.
- Meetings with any other repetition value (for example biweekly or one-off) must be copied into normalized output but excluded from overlap calculations and listed under `unchecked_meetings`.
- Do not infer weekly recurrence from unknown repetition values.

### 6.2 Section

```json
{
  "name": "LEC0201",
  "type": "Lecture",
  "teaching_method": "LEC",
  "section_number": "0201",
  "meetings": [],
  "instructors": ["Samarendra Chandan Bind Dash"],
  "delivery_modes": [{"session": "20269", "mode": "INPER"}],
  "current_enrolment": 177,
  "max_enrolment": 196,
  "available_space": 19,
  "current_waitlist": 0,
  "waitlist_allowed": true,
  "cancelled": false,
  "tba": false,
  "notes": []
}
```

Rules:

- `available_space = max_enrolment - current_enrolment` only when both values are valid integers; otherwise null.
- Do not infer that enrolment is allowed solely because space is positive. Preserve the upstream enrolment indicator separately if available.
- Convert `"Y"`/`"N"` flags explicitly. Unknown values become null, not false.
- Join non-empty instructor first/last names safely.
- An asynchronous or TBA section may have no meetings; this is not an error.

### 6.3 Course

Include:

- upstream ID
- code
- name
- section code (`F`, `S`, `Y`, etc.)
- campus
- sessions
- division and department when available
- description when available
- prerequisites, corequisites, exclusions, and breadth/requirement information when available
- normalized sections
- last-fetched UTC timestamp

Keep verbose enrolment controls and divisional legends out of default responses. They can be added later behind an explicit option if users need them.

## 7. MCP tool contracts

All tool descriptions must explain required values, defaults, limits, and that data is unofficial and may change.

### 7.0 LLM workflow and verification rule

The MCP provides data and deterministic overlap checking. The LLM proposes schedules. The overlap logic in `check_conflicts` is the source of truth for time conflicts — not the LLM's own time reasoning.

Required workflow for any final or recommended schedule:

1. Use `search_courses` and/or `get_course_details` to fetch current section data.
2. Propose one or more schedule options to the student.
3. Before claiming a schedule has no time conflicts, call `check_conflicts` with every selected section for that schedule.
4. Only say a schedule is verified when **all** are true:
   - `has_conflicts` is `false`
   - `transition_violations` is empty
   - `is_complete` is `true`
5. If `is_complete` is `false`, tell the student the check could not fully verify the schedule (for example, unresolved sections, cancelled sections with no reliable times, or TBA meetings) and do not present it as confirmed conflict-free.
6. If `has_conflicts` is `true` or `transition_violations` is non-empty, explain the reported issues and suggest alternatives.

This rule must appear in the `check_conflicts` tool description and in the README example prompts. The MCP cannot force an LLM to follow it, but clear tool descriptions make the expected behavior explicit and reliable.

### 7.1 `get_reference_data`

Purpose: allow the LLM to discover current valid filter values before searching.

Input:

```json
{}
```

Output:

```json
{
  "sessions": [{"label": "Fall 2026 (F)", "value": "20269"}],
  "divisions": [{"label": "Faculty of Arts and Science", "value": "ARTSC"}],
  "campuses": [],
  "delivery_modes": [],
  "course_levels": [],
  "fetched_at": "2026-07-27T01:00:00Z"
}
```

Implementation:

- Cache reference data in memory for 15 minutes (TTL from fetch completion time).
- Do not cache failures.
- Use single-flight loading on cache miss so concurrent requests share one upstream fetch.
- Protect cache reads/writes with asyncio-safe locking.
- Preserve both human labels and API values.
- Remove UI-only group headers from selectable options, but session grouping may be retained as metadata.

### 7.2 `search_courses`

Input model:

```json
{
  "query": "CSC108H1",
  "sessions": ["20269"],
  "divisions": ["ARTSC"],
  "campuses": [],
  "instructor": null,
  "course_levels": [],
  "delivery_modes": [],
  "available_space_only": false,
  "waitlistable_only": false,
  "search_description": false,
  "page": 1,
  "page_size": 10
}
```

Validation:

- `query`: trimmed; maximum 200 characters.
- At least one session and one division are required.
- `page >= 1`.
- `1 <= page_size <= 50`.
- Lists are deduplicated while retaining order.
- Reject newline/control characters in code-like filters.
- Do not permit caller-supplied URL, headers, or arbitrary upstream payload fields.

Query mapping:

- Normalize `query` to uppercase and trim whitespace before classification.
- If the query matches `^[A-Z]{2,4}\d{3}[A-Z0-9]*$`, treat it as a course code and put it in `courseCode`.
- Otherwise put it in `courseTitle` and set `searchCourseDescription` according to the input.
- This heuristic is intentionally permissive across campuses and divisions; do not over-restrict suffix patterns.
- Add unit tests for code-like queries (`CSC108H1`), title queries (`computer programming`), and ambiguous short strings.

Output:

```json
{
  "courses": [
    {
      "id": "...",
      "code": "CSC108H1",
      "name": "Introduction to Computer Programming",
      "section_code": "F",
      "campus": "St. George",
      "sessions": ["20269"],
      "section_count": 12
    }
  ],
  "page": 1,
  "page_size": 10,
  "total": 1,
  "has_next_page": false,
  "fetched_at": "2026-07-27T01:00:00Z"
}
```

Keep search results concise. Full section details belong in `get_course_details`.

### 7.3 `get_course_details`

Input:

```json
{
  "course_code": "CSC108H1",
  "session": "20269",
  "section_code": null
}
```

Validation:

- Normalize `course_code` to uppercase and trim surrounding whitespace.
- Maximum course-code length: 32.
- `session` is required to prevent ambiguity across terms.
- `section_code` is optional and normalized to uppercase.

Behavior:

1. Call the code lookup endpoint.
2. Filter returned course records using the session-matching rules in §3.5.
3. If `section_code` (term half) was supplied, filter to matching `course.sectionCode`.
4. Return all remaining matches because the upstream API may legitimately represent a course in multiple records.
5. If no records remain after filtering, raise `CourseNotFoundError` (MCP error code `course_not_found`). Do not return a silent empty success.

Output on success:

```json
{
  "courses": [],
  "found": true,
  "fetched_at": "2026-07-27T01:00:00Z"
}
```

Each course uses the normalized course model from Section 6. V1 returns all matching sections for the requested course/session; do not truncate section lists.

### 7.4 `check_conflicts`

This tool must resolve section names from fresh/current course data rather than trusting meeting times copied by the LLM.

Tool description (include verbatim or equivalent in the registered MCP tool):

> Deterministically check whether selected course sections overlap in time. Resolves section meeting times from current timetable data — do not rely on your own time arithmetic. Before telling a student that a proposed schedule is verified, call this tool with every selected section. Only claim the schedule is verified when `has_conflicts` is false, `transition_violations` is empty, and `is_complete` is true. If `is_complete` is false, say the schedule could not be fully verified.

Input:

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

Validation:

- One session is required.
- Require 1–20 courses.
- Require 1–10 section names per course.
- Deduplicate course and section identifiers.
- `minimum_transition_minutes` must be between 0 and 180.

Resolution:

- Fetch each unique course code once, concurrently with a conservative concurrency limit (for example, five).
- Filter course records using the session-matching rules in §3.5.
- Resolve `section_names` (LEC/TUT components) case-insensitively under their parent `course_code`, and return canonical names.
- If an upstream fetch fails for a course (timeout, upstream error, not found), add that course to `unresolved` and continue checking the remaining courses. Set `is_complete=false`.
- If a requested section name cannot be resolved, add it to `unresolved`. Set `is_complete=false`.
- If more than one upstream record contains the same requested section for the session, report ambiguity in `unresolved`.
- If a resolved section is cancelled (`cancelled=true`), include it in `cancelled_sections`, still evaluate its meeting times if present, and warn the LLM via tool output metadata.

Conflict algorithm:

1. Flatten selected sections into individual meetings.
2. Ignore meetings with unknown day/time or non-weekly repetition for overlap calculations and list them under `unchecked_meetings`.
3. Compare each unique pair of meetings from different selected sections.
4. A direct conflict exists when days match and:

   ```text
   a.start_minutes < b.end_minutes
   AND
   b.start_minutes < a.end_minutes
   ```

5. Back-to-back meetings (`a.end == b.start`) are not direct conflicts.
6. When `minimum_transition_minutes > 0`, evaluate transition violations only between **chronologically adjacent** same-day meetings:
   - Group checkable meetings by day.
   - Sort each day's meetings by `start_minutes`.
   - Compare each consecutive pair in that sorted list.
   - If the gap between the earlier meeting's end and the later meeting's start is greater than or equal to zero and less than `minimum_transition_minutes`, record a transition violation.
   - Do not compare every unordered same-day pair; only adjacent meetings in the day's timeline.
7. Do not compare a meeting with itself. Do compare different selected sections from the same course because a user can accidentally select overlapping components.
8. Deduplicate equivalent conflict records and sort by day, overlap start, and section identifiers for deterministic output.

Output:

```json
{
  "has_conflicts": true,
  "is_complete": true,
  "conflicts": [
    {
      "kind": "overlap",
      "day": "Tuesday",
      "section_a": "CSC148H1 LEC0101",
      "section_b": "STA256H1 LEC0201",
      "meeting_a": "10:00-12:00",
      "meeting_b": "11:00-13:00",
      "overlap_start": "11:00",
      "overlap_end": "12:00",
      "overlap_minutes": 60
    }
  ],
  "transition_violations": [],
  "unresolved": [],
  "unchecked_meetings": [],
  "cancelled_sections": [],
  "checked_section_count": 4,
  "fetched_at": "2026-07-27T01:00:00Z"
}
```

Semantics:

- `has_conflicts` concerns confirmed direct overlaps only.
- `transition_violations` concerns same-day gaps that are too small when `minimum_transition_minutes > 0`.
- `is_complete` is false if anything is unresolved, unchecked, or if any requested course fetch failed.
- Never summarize an incomplete check as “no conflicts.” Return “no confirmed conflicts, but the check is incomplete.”
- Never summarize a schedule as verified if `transition_violations` is non-empty.
- TBA, asynchronous, and non-weekly sections are placed in `unchecked_meetings`; they are not conflicts by themselves.
- `section_names` are always scoped under their parent `course_code`; the same component name on different courses is not ambiguous.

## 8. Safety, reliability, and token efficiency

- Never accept arbitrary URLs. The base URL is controlled by settings; test overrides are dependency-injected, not exposed as MCP parameters.
- Do not log full responses or potentially sensitive user prompts. Log request endpoint, duration, status, retry count, and result counts.
- Use structured logging to stderr because stdout is reserved for stdio MCP transport.
- Cap all list sizes and response sizes.
- Use ISO 8601 UTC timestamps.
- Preserve unknown upstream values rather than inventing meanings.
- Include a short data-freshness disclaimer in relevant tool descriptions, not repeated inside every section object.
- Send no U of T credentials or cookies.
- Avoid broad course dumps. Pagination and concise search summaries are mandatory.
- Make deterministic functions pure where possible.

## 9. Testing strategy

Tests must not depend on the live U of T API except for an explicitly marked optional live smoke test. CI must be deterministic and use saved, sanitized fixtures.

### 9.1 Unit tests: normalization

Test:

- Milliseconds convert correctly at midnight, morning, noon, and late evening.
- Every valid day number maps to the expected weekday.
- Unknown day values remain unknown and are not conflict-checkable.
- Building code, room, and suffix form a clean location with no duplicated spaces.
- Missing room/building fields are handled.
- Instructor names handle missing first or last names.
- `"Y"`, `"N"`, null, and unknown flags normalize correctly.
- Available-space calculation handles positive, zero, over-capacity, missing, and malformed values.
- TBA and asynchronous sections with no meetings normalize without failure.
- Raw fixture input is not mutated.

### 9.2 Unit tests: direct conflicts

At minimum:

1. Same day, partial overlap → conflict.
2. Same day, one meeting contained in another → conflict.
3. Same day, identical intervals → conflict.
4. Same day, back-to-back intervals → no conflict.
5. Same day, separated intervals → no conflict.
6. Different days, same time → no conflict.
7. Multiple meetings produce all conflicts without duplicates.
8. Two components of the same course can conflict.
9. Unknown/TBA meeting → unchecked, not conflict.
10. Inputs are returned in deterministic order.

### 9.3 Unit tests: transition buffer

Test:

- Zero buffer accepts back-to-back meetings.
- A 15-minute buffer flags a 10-minute gap between adjacent same-day meetings.
- A 15-minute buffer accepts a 15-minute gap between adjacent same-day meetings.
- Overlaps appear only in `conflicts`, not duplicated as transition violations.
- Different-day meetings never create transition violations.
- Three meetings on one day (`09:00-10:00`, `10:10-11:00`, `11:30-12:30`) with a 15-minute buffer flag only the adjacent gaps (`10:00-10:10` and `11:00-11:30`), not non-adjacent pairs.

### 9.4 API client tests with `respx`

Test:

- Correct method, URL, headers, and JSON payload.
- Search page and page-size mapping.
- Query values are URL-encoded.
- Successful response parsing.
- `payload: null` with an application error raises `TimetableUpstreamError`.
- Invalid JSON raises a safe upstream error.
- Missing expected keys raise a safe upstream error.
- Timeout is retried and then raises `TimetableTimeoutError`.
- `503` is retried.
- `400` is not retried.
- `429` respects `Retry-After` without making tests actually sleep (inject or mock the sleeper).
- HTTP client is closed cleanly.

### 9.5 MCP tool tests with in-memory client

Use FastMCP's in-memory client to call the registered tools through the MCP protocol rather than calling only their Python functions.

Test:

- Exactly the four V1 tools are discoverable.
- Tool names, descriptions, and input schemas are useful and accurate.
- The `check_conflicts` description instructs the LLM to call it before claiming a schedule is verified, and to require `has_conflicts=false`, empty `transition_violations`, and `is_complete=true`.
- `get_reference_data` returns selectable normalized values and strips header-only session entries.
- Reference-data cache prevents a duplicate upstream call before expiry.
- A cache miss occurs after controlled expiry.
- Reference-data cache uses single-flight loading on concurrent misses.
- Failed reference-data fetches are not cached.
- `search_courses` rejects missing sessions/divisions and out-of-range page sizes.
- `search_courses` routes code-like and title-like queries using the §7.2 heuristic.
- `search_courses` returns concise data and correct pagination.
- Valid empty search returns `courses: []` without error.
- `get_course_details` normalizes lowercase input and filters session correctly, including combined sessions such as `20269-20271`.
- `get_course_details` raises `course_not_found` when no records remain.
- `get_course_details` handles multiple upstream records for Y courses.
- `check_conflicts` resolves identifiers and returns a known overlap.
- Duplicate course codes cause only one upstream fetch.
- Partial upstream failure marks unresolved courses and sets `is_complete=false`.
- Ambiguous duplicate section resolution is reported in `unresolved`.
- Cancelled sections appear in `cancelled_sections`.
- Missing sections make `is_complete` false.
- Non-weekly meetings are listed in `unchecked_meetings`.
- Each mapped exception type produces the documented MCP-safe error shape.
- Tool errors do not expose internal stack traces.
- End-to-end fixture flow: reference → search → details → conflicts.

### 9.6 Transport smoke test

Start the module through stdio in a subprocess, perform MCP initialization, list tools, and terminate cleanly. This verifies packaging and entry-point behavior.

The test must have a strict timeout so CI cannot hang.

### 9.7 Optional live test

Mark with `@pytest.mark.live` and exclude by default. It may:

1. Fetch reference data.
2. Pick a currently advertised session/division.
3. Perform one small search with `page_size=1`.
4. Validate only broad invariants, not a specific instructor, room, enrolment count, or course availability.

Never run the live test automatically on every local test command or in untrusted pull requests.

## 10. Quality gates

The implementation is complete only when all of these pass:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
```

Target at least 90% branch coverage for `normalize.py` and `conflicts.py`, and cover retry/error branches in `client.py`.

## 11. Implementation sequence

### Phase 1 — Project setup

1. Initialize `pyproject.toml` and the `src` package.
2. Add runtime and development dependencies using `uv`.
3. Configure Ruff, Pyright, Pytest, and asyncio mode.
4. Add a minimal FastMCP server and stdio entry point.
5. Add a smoke test that lists tools.

### Phase 2 — Models and normalization

1. Save sanitized representative API fixtures.
2. Define upstream parsing helpers and public Pydantic models.
3. Implement day/time, instructor, location, flags, and availability normalization.
4. Complete normalization unit tests before exposing course data through tools.

### Phase 3 — API client

1. Implement reusable async `httpx.AsyncClient`.
2. Add headers, timeout, retry policy, and safe errors.
3. Implement reference, search, and code-lookup methods.
4. Complete mocked client tests.

### Phase 4 — Data tools

1. Implement `get_reference_data` and TTL cache.
2. Implement `search_courses`.
3. Implement `get_course_details`.
4. Test all three through an in-memory MCP client.

### Phase 5 — Conflict tool

1. Implement pure overlap and transition functions.
2. Add complete edge-case tests.
3. Implement concurrent identifier resolution with bounded concurrency.
4. Register and test `check_conflicts` through MCP.

### Phase 6 — Documentation and final verification

1. Document installation with `uv`.
2. Document Cursor MCP configuration using stdio.
3. Include example prompts and tool calls.
4. State unofficial status and API-change risk.
5. Run all quality gates.
6. Manually verify with MCP Inspector and one small live API call.

## 12. README requirements

The finished README must include:

- What the server does and does not do.
- The four tools and example inputs.
- Python and `uv` prerequisites.
- Installation and stdio launch commands.
- Cursor MCP configuration.
- Development/test commands.
- Data source and freshness notes.
- Unofficial-project disclaimer and upstream access/ToS risk note.
- Troubleshooting for timeout, upstream format changes, and no search results.
- A privacy statement confirming no credentials are required or collected.

Example prompts:

- “Find Fall 2026 Arts & Science sections of CSC108H1.”
- “Show enrolment and meeting details for CSC148H1 in session 20269.”
- “Check whether these selected lecture and tutorial sections overlap.”
- “Create a schedule from these courses, avoid Fridays, and use the conflict tool to verify the final choice.”

## 13. Acceptance criteria

V1 is accepted when:

1. A fresh checkout installs reproducibly with `uv sync`.
2. Cursor can start the server over stdio and discover exactly four tools.
3. The server can discover current sessions rather than relying on hard-coded terms.
4. Search results are paginated and concise.
5. Course details expose human-readable and machine-comparable meeting times.
6. Availability and waitlist values are represented without unsupported inference.
7. Conflict checking uses current resolved section data and correctly handles overlaps, back-to-back meetings, buffers, TBA meetings, missing sections, and ambiguity.
8. An incomplete conflict check cannot be mistaken for a verified conflict-free schedule.
9. The `check_conflicts` tool description instructs the LLM to verify every final schedule before claiming it is verified, requiring empty `transition_violations` as well as `has_conflicts=false` and `is_complete=true`.
10. Session matching handles combined sessions such as `20269-20271`.
11. `get_course_details` returns a structured not-found error rather than a silent empty success.
12. MCP errors use stable codes and do not expose internals.
13. No U of T login, cookie, or student data is required.
14. Tests pass without live network access.
15. Ruff and Pyright pass.

## 14. Guidance for the implementing agent

- Read this entire document before editing.
- Verify the current FastMCP APIs from official documentation before writing framework-specific code; package APIs can change.
- Preserve the architecture boundaries in Section 5.
- Implement one phase at a time and run its focused tests before proceeding.
- Use dependency injection for the timetable client, clock, sleeper, and cache timing so tests remain deterministic.
- Do not weaken tests merely to make them pass.
- Do not add features outside V1 without explicit approval.
- Do not commit or push unless explicitly requested.
- If the live upstream schema differs from the fixtures, capture the smallest sanitized example needed, update normalization deliberately, and explain the compatibility change.
- If an upstream field's meaning is uncertain, preserve it as unknown or raw metadata; do not guess.
- Finish by reporting files changed, tests run, results, and any remaining upstream assumptions.
