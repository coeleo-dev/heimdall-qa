"""Reading the correlation line of one trace out of every declared log source.

`collector.py` is the only module here, and it knows nothing about a product: it is
handed the sources the descriptor declared, and it answers with what each one said —
including the ones that said nothing, and why. `contrib/architecture.md`,
`## 7. Context propagation` is the measurement behind the shape.
"""
