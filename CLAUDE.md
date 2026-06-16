# CLAUDE.md

@AGENTS.md

Claude Code entrypoint:

- Use `AGENTS.md` for shared instructions.
- Run `make ci-local` before final handoff.
- Source-of-truth files: `panelapp_link/constants.py` (confidence maps, ranks,
  region labels, citations), `panelapp_link/data/schema.sql` (SQLite schema),
  and `uv.lock` (dependency lock).
- Keep public MCP tools read-only and research-use scoped; respect the per-file
  600-line budget (`make lint-loc`).
