"""Connecting ci-doctor's MCP server to coding agents: `ci-doctor mcp install` and `uninstall`.

Each agent (Claude Code, Cursor, Codex, ...) keeps its MCP config in its own file and
format. One module per agent in `targets/` knows where and how; `registry.py` lists
them. Every edit changes only ci-doctor's entry and leaves the rest of the file alone.
"""
