"""The desktop client: a window over the process the CLI already runs.

The harness is not re-implemented here. `heimdall-qa desktop` builds the same
`WorkspaceSession` `heimdall-qa serve` builds, serves it with the same FastAPI app and
the same `/api/*` surface, and puts a native window on the port instead of a browser
tab. Everything the client can do, the CLI and an agent can still do to the same run
directory, because the run directory was never the window's to own.
"""
