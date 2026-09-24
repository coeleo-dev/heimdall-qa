"""The harness over MCP, for a model rather than a shell.

`server.py` holds the whole of it: the tools, the run resource and the two recipes it
carries as prompts. The SDK is an optional extra, so importing this package must not
need it — and it does not, because `server.py` imports `mcp` inside the function that
builds the server rather than at the top.

The CLI stays the primary interface: it is what CI calls and what a person reads.
This exists because a model that can ask "does this round validate" without composing
a shell command asks a better question, not because a shell is going away.
"""
