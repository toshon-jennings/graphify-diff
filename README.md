# graphify-diff (`gdiff`)

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

---

## Quick start

```bash
pip install graphify-diff
```

Then run from inside a repo with **no arguments** — baseline is auto-detected from the last graph build:

```bash
gdiff patch        # apply the diff to graph.json
gdiff analyze      # read-only preview of what would change
gdiff impact       # trace transitive effects on the graph
```

Override the baseline with `--since`:

```bash
gdiff patch --since HEAD~1
gdiff patch . --since main     # run from outside the repo
```

---

## Subcommands

| Command | What it does |
|---------|-------------|
| `gdiff patch` | Apply a git diff to graph.json — cascades through dependent nodes (default depth: 3) |
| `gdiff analyze` | Read-only parse of what would change — no graph required |
| `gdiff impact` | Trace transitive effects on the existing graph — does not modify it |

### Patch flags

| Flag | Description |
|------|-------------|
| `--since / -s <ref>` | Git ref to diff against (auto-detected if omitted) |
| `--staged` | Diff staged changes instead of unstaged |
| `--dry-run / -n` | Show what would change without writing |
| `--cascade-depth / -d <n>` | How far to cascade dependency changes (default: 3) |
| `--graph / -g <path>` | Path to graph.json (default: `graphify-out/graph.json`) |
| `--output / -o <path>` | Output path (default: overwrite input) |
| `--json` | Emit machine-readable JSON output |

### Analyze & impact flags

| Flag | Description |
|------|-------------|
| `--since / -s <ref>` | Git ref to diff against |
| `--staged` | Diff staged changes |
| `--depth / -d <n>` | Cascade depth for impact analysis |
| `--graph / -g <path>` | Path to graph.json |

---

## Project links

| | |
|---|---|
| **Source** | [github.com/toshon-jennings/graphify-diff](https://github.com/toshon-jennings/graphify-diff) |
| **PyPI** | [pypi.org/project/graphify-diff](https://pypi.org/project/graphify-diff) |
| **Issues** | [GitHub Issues](https://github.com/toshon-jennings/graphify-diff/issues) |
| **Changelog** | [CHANGELOG.md](CHANGELOG.md) |

---

## License

MIT © 2026 Toshon Jennings
