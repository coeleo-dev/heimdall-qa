"""How a content document is written back to disk.

A case file is read and written by a person as much as by the harness: a reviewer
opens `cases/ingest.yaml`, finds the case they were told about, and edits it. The
text therefore has one style, decided here, and every writer uses it — a corpus whose
files are formatted by whichever tool touched them last is a corpus where every edit
shows a diff of lines nobody changed.

Two choices make the diff small:

`indentless=False` — PyYAML writes a nested list item under the key that owns it by
default (`tags:` then `- function` at the same column). Lining the item up under the
key reads as the ownership it is, and it is what the corpus already looks like.

`width=1000` — the default wraps at 80 columns, which turns a `reason:` a human wrote
on one line into three, and a re-dump of a file after a one-word edit into a rewrite of
every long line in it.
"""

from collections.abc import Mapping
from typing import Any

import yaml


class _IndentedDumper(yaml.SafeDumper):
    """A dumper that lines a sequence item up under the key that owns it."""

    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow, False)


def dump(data: Mapping[str, Any]) -> str:
    """`data` as the text of a content document, ending in a newline."""
    return yaml.dump(
        dict(data),
        Dumper=_IndentedDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=1000,
    )
