# graphify-diff

Incremental graph updates from git diffs — patch a [Graphify](https://github.com/safishamsi/graphify) knowledge graph without re-extracting the entire codebase.

## The Problem

You run `graphify extract` to build a dependency/relationship graph of your codebase. Then you make changes and have to re-run the full extraction. For large codebases with docs and images, this means LLM API calls and minutes of waiting — even for a one-line change.

## The Solution

`graphify-diff` reads a git diff, identifies which symbols (functions, classes, imports) were added, removed, or modified, and patches the existing `graph.json` in-place. No LLM calls. No tree-sitter re-parsing. Seconds instead of minutes.

## How It Differs from `graphify update`

Graphify already has `graphify update`, which re-extracts only changed files. But it still:
- Re-parses every changed file with tree-sitter
- Re-runs the full graph merge pipeline
- Re-clusters all communities

`graphify-diff` skips all of that. It works at the **symbol level**, not the file level. If you change one function in a 500-line file, only that function's node and edges are touched.

## Installation

```bash
pip install graphify-diff
```

## Usage

```bash
# Analyze what would change (no graph needed)
graphify-diff analyze /path/to/repo --since HEAD~1

# Patch the graph with changes since last commit
graphify-diff patch /path/to/repo --since HEAD~1

# Dry run to see what would change
graphify-diff patch /path/to/repo --since HEAD~1 --dry-run

# Patch with changes against main branch
graphify-diff patch /path/to/repo --since main

# Show cascading impact on existing graph
graphify-diff impact /path/to/repo --since HEAD~1

# Use a custom graph.json location
graphify-diff patch /path/to/repo --graph /custom/path/graph.json

# Output machine-readable JSON
graphify-diff patch /path/to/repo --since HEAD~1 --json
```

## What It Does

1. **Parses git diff** — identifies added, removed, and modified files
2. **Extracts symbol changes** — detects which functions/classes were added or removed using language-aware regex patterns (Python, TypeScript, JavaScript, Go, Rust, Java, Kotlin, C/C++, Ruby, Bash, and generic fallbacks)
3. **Patches the graph** — removes nodes for deleted symbols, creates stub nodes for new symbols, updates modified nodes
4. **Cascades changes** — finds nodes that depend on changed symbols and marks them for review
5. **Re-clusters locally** — re-runs community detection only on affected communities (requires `python-louvain` for best results)

## Limitations

- **New files** get stub nodes. Run `graphify update` later for full extraction.
- **Semantic edges** (LLM-inferred relationships) are not re-computed. The tool only handles structural changes.
- **Cross-file symbol resolution** is conservative — it won't guess which file a symbol belongs to if there are ambiguities.
- **Supported languages**: Python, TypeScript/JavaScript, Go, Rust, Java/Kotlin, C/C++, Ruby, Bash. Other languages get generic pattern matching.

## Architecture

```
git diff → parse_diff() → DiffResult
    ↓
DiffResult → apply_diff() → PatchResult
    ↓
  ┌─────────────────────────────────────┐
  │ For each changed file:              │
  │  1. cascade_*() — find impacted     │
  │  2. remove_symbol_nodes() — delete  │
  │  3. add_nodes_and_edges() — create  │
  │  4. mark_affected_for_review()      │
  └─────────────────────────────────────┘
    ↓
recluster_affected_communities()
    ↓
save_graph()
```

## License

MIT
