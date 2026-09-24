"""Heimdall QA — review harness for the HTTP surface of a REST API.

The harness measures a product it was not written for: what the API is, where it
answers, and what its good and bad responses look like are all declared by the
project descriptor (see `heimdall_qa.project`), never compiled in. Everything
here is product-neutral; a product's own cases, contracts and campaigns live in
its provider.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version

#: The distribution's version, read from the metadata `pyproject.toml` installs.
#: Read rather than written down, so there is exactly one place a release number
#: lives and `--version` cannot disagree with the wheel it came from. A source tree
#: that was never installed has no metadata to read, and says so rather than
#: guessing a number that would be wrong in a bug report.
try:
    __version__ = version("heimdall-qa")
except PackageNotFoundError:  # pragma: no cover - only when run uninstalled
    __version__ = "0.0.0+source"

__all__ = ["__version__"]
