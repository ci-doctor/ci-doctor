"""What each command does, with no terminal in it.

`cli/` parses options, calls one of these and prints the result; anything else
driving ci-doctor (an editor, an agent, a future `mcp` command) calls them too.
`cli/` imports `service/`, never the reverse.
"""
