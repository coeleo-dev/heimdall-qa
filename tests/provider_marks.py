"""Marks for tests that need a piece of the toolchain the core does not ship.

The core's suite has to pass with no provider distribution installed — that is what
`examples/toy-provider/gate.sh` claim 5 checks, and a core that needs a product in
order to test itself has not been split. Nothing here is gated on a provider: a
descriptor *declares* a provider id and resolving it is the run's business, so a
project can be configured before its provider is installed. The tests that check a
price model live in the suite of whoever owns that price model.

Availability is asked of the same function a run asks (`load_contract_source`), not of
`find_spec`: the point is whether the seam can be served, not whether a module happens
to be importable.
"""

import pytest

from heimdall_qa.contract_source import load_contract_source
from heimdall_qa.errors import HarnessError


def contract_source_available(name: str) -> bool:
    """Whether a reader for `name` can be loaded in this environment.

    Asked the way a run asks it — through the registry, not through `find_spec` —
    because the claim is that the *seam* is served, not that some module happens to
    be importable. `heimdall-qa-spring` is its own distribution, so a core checkout
    that has not installed it still has to collect this suite.
    """
    try:
        load_contract_source(name)
    except HarnessError:
        return False
    return True


#: For the tests that read a Java/Spring source tree.
needs_spring_reader = pytest.mark.skipif(
    not contract_source_available("spring"),
    reason="the spring reader is not installed (pip install -e packages/spring)",
)
