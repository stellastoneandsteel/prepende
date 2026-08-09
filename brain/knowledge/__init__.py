"""Knowledge implementation — vault read/write + wikilink/frontmatter parsing.

The vault (vault/) is the source of truth. This reads/writes its markdown
directly (no Obsidian app dependency), parses [[wikilinks]] + YAML frontmatter
into a graph, and runs the scheduled self-organization passes (ingest, link,
summarize, lint) through the ModelGateway.

SKELETON — Phase 2; self-organization jobs in Phase 5.
"""
