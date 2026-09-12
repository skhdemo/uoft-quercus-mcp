# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Document Cursor and Claude Desktop setup for Windows and macOS/Linux, including absolute `uvx` paths when the client cannot see `PATH`

## [0.1.0] - 2026-09-09

First public release of the unofficial University of Toronto Quercus MCP server.

### Added

- Quercus (Canvas) tools via a personal access token: identity, courses, todo/upcoming, assignments, announcements, modules, files, and PDF/DOCX/text extraction
- Public Timetable Builder tools: reference data, course search, course details, and deterministic section conflict checks
- Stdio MCP entry points `uoft-quercus-mcp` and deprecated alias `uoft-timetable-mcp`

[0.1.0]: https://github.com/skhdemo/uoft-quercus-mcp/releases/tag/v0.1.0
