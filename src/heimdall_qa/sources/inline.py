"""The reader for a contract a human wrote.

It reads nothing on purpose. `inline` is the baseline of the staircase: it means
the contract YAML in the repository *is* the source of truth, so there is no
document to derive it from and nothing to derive. Declaring it is how a project
says "this is authored, stop looking for a machine-readable spec".

It exists as a reader, and not as a special case in the resolver, so that every
degrau of the staircase is the same kind of thing: a name that resolves to
something with `read()`.
"""

from heimdall_qa.contract_source import INLINE
from heimdall_qa.contract_source import ApiSchema


class InlineSource:
    """The empty schema, and the honest reason for it."""

    name = INLINE

    def read(self, location: str | None) -> ApiSchema:
        """No endpoints and no gaps: an authored contract declares its own.

        A gap here would be wrong twice over — the reader is not missing a fact,
        and a project that declares `inline` has already made the decision the gap
        would be asking for.
        """
        return ApiSchema(source=INLINE)


def reader() -> InlineSource:
    """The factory the registry builds."""
    return InlineSource()
