"""FastMCP server instance for the UofT Timetable MCP."""

from fastmcp import FastMCP

from uoft_timetable_mcp import __version__

mcp = FastMCP(
    name="uoft-timetable-mcp",
    version=__version__,
    instructions=(
        "Unofficial University of Toronto Timetable Builder data tools. "
        "Discover sessions and filters, search courses, fetch section details, "
        "and deterministically check schedule conflicts. Data may change; "
        "this project is not affiliated with the University of Toronto."
    ),
)
