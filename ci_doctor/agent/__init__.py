"""ci-doctor for coding agents: the agent is the model, and calls ci-doctor.

`server.py` is the MCP server, the second surface beside `cli/`: a tool parses, calls
`service/` and returns, and nothing here calls a model (invariant #12). `harness/`
connects agents to it (`ci-doctor agent install`). The other direction, ci-doctor
calling a model, is `llm/`.
"""
