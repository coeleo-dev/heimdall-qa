"""The readers the core ships, and the map that resolves them without an entry point.

A built-in is not a plugin with a nicer story: `inline` has no document to read and
`openapi` needs only PyYAML, which the core already depends on. Registering them
through an entry point would mean the core's own readers were unavailable in a
source checkout that had not been `pip install`ed, which is the opposite of what a
seam is for.

Everything else — `spring` today — registers under the same group in
`contract_source.ENTRY_POINT_GROUP` and arrives with its own distribution.
"""

from collections.abc import Callable

from heimdall_qa.contract_source import INLINE
from heimdall_qa.contract_source import OPENAPI
from heimdall_qa.contract_source import ContractSource
from heimdall_qa.sources.inline import reader as inline_reader
from heimdall_qa.sources.openapi import reader as openapi_reader

#: Reader name -> factory. The factory is what a caller builds, never a shared
#: instance: a reader is a stateless value, and sharing one would make state a
#: thing a reader could grow.
BUILT_IN: dict[str, Callable[[], ContractSource]] = {
    INLINE: inline_reader,
    OPENAPI: openapi_reader,
}
