# Design records (historical)

These dated specs and plans record what was **designed** at the time. They are a
historical record, not the live contract, and parts of them were superseded by the
implementation — for example they describe an `AmbiguousQueryError` / `ambiguous_query`
error code, a `data_unavailable` code, an `hgnc_id` argument on `resolve_gene`, and
substring panel matching. **None of those exist in the server.**

They are deliberately left as written: rewriting them would falsify the record.

For the contract the server actually honours, use:

- the live MCP schemas (`get_server_capabilities`, `panelapp://usage`, `panelapp://reference`)
- [`README.md`](../../README.md), [`usage.md`](../usage.md), [`architecture.md`](../architecture.md),
  [`data-lifecycle.md`](../data-lifecycle.md), [`configuration.md`](../configuration.md),
  [`deployment.md`](../deployment.md)

Those surfaces are machine-checked against the running server by
`tests/unit/test_docs_contract.py`, which is why this directory is excluded from it.
