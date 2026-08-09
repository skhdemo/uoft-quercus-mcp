"""Stdio entry point for the UofT Quercus MCP server."""

from uoft_quercus_mcp.server import mcp


def main() -> None:
    """Run the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    main()
