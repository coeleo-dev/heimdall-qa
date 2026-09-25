"""Where the built client lives, and the error that says how to build it.

One constant and one check, in a module of their own, because there is now exactly one
renderer and every entry point has to find it: `heimdall-qa serve` for a browser,
`heimdall-qa desktop` for the pywebview window, and `serve/app.py` to mount it. Two of
those defining their own path would drift the day the directory moves, and the failure
would be a client that opens blank — which looks like a bug in the client and is not.

The directory is inside the package on purpose: `pip install` must not require Node, so
the built bundle is committed and shipped in the wheel. The check below is the one a
developer's checkout and the CI's drift job both rely on.
"""

from __future__ import annotations

from pathlib import Path

from heimdall_qa.errors import HarnessError

#: The built SPA. Inside the package so a wheel carries it, and beside `desktop/` so
#: that the shell and the served surface are obviously the same client.
WEBAPP = Path(__file__).resolve().parent.parent / "desktop" / "webapp"


def webapp_dir(webapp: Path | None = None) -> Path:
    """The built bundle, or a refusal that names the exact command that builds it.

    `webapp` overrides the package's own directory so a test can point the mount at a
    throwaway one — including an empty one, which is how `WEBAPP_NOT_BUILT` is exercised
    without disturbing the real bundle. There is one message and one hint for both
    callers: what a reader acts on is the words, and a second copy is a second thing to
    keep in step.
    """
    directory = webapp or WEBAPP
    if not (directory / "index.html").is_file():
        raise HarnessError(
            code="WEBAPP_NOT_BUILT",
            message=f"the desktop webapp is not built ({directory})",
            hint=(
                "build it once: npm --prefix desktop/webapp ci"
                " && npm --prefix desktop/webapp run build"
            ),
        )
    return directory
