"""ci-doctor as an MCP server: the second surface, beside `cli/`, for a coding agent.

Same rule as the command line (GUIDELINES §2.1): a tool parses, calls `service/`
and returns. The agent on the other end is the model, so nothing here calls one
(invariant #12).
"""
