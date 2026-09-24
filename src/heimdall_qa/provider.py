"""How the core finds a provider without importing one.

A descriptor that declares `provider: acme` is naming domain logic the core does
not contain: the price model, and later whole packs and step kinds. The core has
to get from that name to the code without a single `import heimdall_qa_acme`
anywhere, or the wheel stops being generic.

The answer is the `heimdall_qa.providers` entry point group. A provider
distribution declares::

    [project.entry-points."heimdall_qa.providers"]
    acme = "heimdall_qa_acme"

and `importlib.metadata` hands the module back here. A source checkout that has
not installed the provider yet falls back to importing `heimdall_qa_<id>`, the
package name the provider convention fixes; a name that resolves to neither is a
named error, never a silent neutral default.
"""

from collections.abc import Callable
from importlib import import_module
from importlib.metadata import entry_points
from types import ModuleType

from heimdall_qa.errors import HarnessError
from heimdall_qa.oracle import Oracle

#: The entry point group a provider distribution publishes itself under.
ENTRY_POINT_GROUP = "heimdall_qa.providers"

#: What a provider module exposes to answer for its price model.
ORACLE_ATTRIBUTE = "oracle"


def load_provider(provider_id: str) -> ModuleType:
    """The provider package named `provider_id`, installed or importable."""
    loaded = _from_entry_points(provider_id)
    if loaded is not None:
        return loaded
    return _from_module_name(provider_id)


def oracle_factory(provider_id: str) -> Callable[[], Oracle]:
    """`provider_id`'s book factory.

    The entry point may point at the module or straight at the callable, so both
    spellings work: `acme = "heimdall_qa_acme"` and
    `acme = "heimdall_qa_acme:oracle"`.
    """
    loaded = _from_entry_points(provider_id)
    if callable(loaded) and not isinstance(loaded, ModuleType):
        return loaded
    module = loaded if loaded is not None else _from_module_name(provider_id)
    factory = getattr(module, ORACLE_ATTRIBUTE, None)
    if not callable(factory):
        raise HarnessError(
            code="PROVIDER_INVALID",
            message=f"provider '{provider_id}' exposes no {ORACLE_ATTRIBUTE}()",
            hint=(
                f"the package must define {ORACLE_ATTRIBUTE}() returning the"
                " product's oracle, or point the entry point at the callable"
            ),
        )
    return factory


def _from_entry_points(provider_id: str) -> ModuleType | Callable[[], Oracle] | None:
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        if entry.name != provider_id:
            continue
        return entry.load()
    return None


def _from_module_name(provider_id: str) -> ModuleType:
    module_name = _module_name(provider_id)
    try:
        return import_module(module_name)
    except ModuleNotFoundError as missing:
        if missing.name not in {module_name, module_name.split(".")[0]}:
            raise
        raise HarnessError(
            code="PROVIDER_NOT_FOUND",
            message=f"provider '{provider_id}' is not installed",
            hint=(
                f"install the distribution that publishes the"
                f" {ENTRY_POINT_GROUP} entry point '{provider_id}', or drop the"
                " provider from the descriptor"
            ),
        ) from missing


def _module_name(provider_id: str) -> str:
    """The package name the provider convention derives from an id."""
    return f"heimdall_qa_{provider_id.replace('-', '_')}"
