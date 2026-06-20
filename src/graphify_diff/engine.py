"""Main orchestrator — ties together diff parsing, graph patching, cascade, and re-clustering."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx

from .cascade import (
    CascadeResult,
    cascade_added_symbols,
    cascade_deleted_file,
    cascade_modified_file,
    cascade_removed_symbols,
)
from .diff_parser import (
    ChangeType,
    DiffResult,
    FileChange,
    current_head,
    find_graph_path,
    get_git_diff,
    parse_diff,
)
from .graph_patcher import (
    add_nodes_and_edges,
    find_nodes_by_label,
    find_nodes_by_source_file,
    load_graph,
    remove_file_nodes,
    remove_symbol_nodes,
    save_graph,
    update_node_attributes,
)
from .recluster import mark_affected_for_review, recluster_affected_communities


@dataclass
class PatchResult:
    """Result of applying a diff patch to the graph."""
    files_processed: int = 0
    nodes_added: int = 0
    nodes_removed: int = 0
    nodes_updated: int = 0
    nodes_marked_for_review: int = 0
    edges_added: int = 0
    edges_removed: int = 0
    communities_reclustered: int = 0
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        lines = []
        if self.dry_run:
            lines.append("DRY RUN — no changes written")
        lines.append(f"Files processed: {self.files_processed}")
        lines.append(f"Nodes added: {self.nodes_added}")
        lines.append(f"Nodes removed: {self.nodes_removed}")
        lines.append(f"Nodes updated: {self.nodes_updated}")
        lines.append(f"Nodes marked for review: {self.nodes_marked_for_review}")
        lines.append(f"Edges added: {self.edges_added}")
        lines.append(f"Edges removed: {self.edges_removed}")
        lines.append(f"Communities re-clustered: {self.communities_reclustered}")
        if self.warnings:
            lines.append(f"\nWarnings ({len(self.warnings)}):")
            for w in self.warnings:
                lines.append(f"  - {w}")
        return "\n".join(lines)


def apply_diff(
    repo_path: Path,
    graph_path: Path,
    diff: DiffResult,
    output_path: Path | None = None,
    dry_run: bool = False,
    cascade_depth: int = 3,
    built_at_commit: str | None = None,
) -> PatchResult:
    """Apply a parsed diff to a Graphify graph.

    Args:
        repo_path: Path to the git repository root.
        graph_path: Path to the existing graph.json.
        diff: Parsed diff from parse_diff().
        output_path: Where to write the updated graph. Defaults to graph_path.
        dry_run: If True, compute changes but don't write.
        cascade_depth: How far to cascade dependency changes.
        built_at_commit: Commit SHA to stamp as the graph's new baseline.
            When set (and not a dry run), the graph's ``built_at_commit`` is
            updated so subsequent runs diff from this point.
    """
    result = PatchResult(dry_run=dry_run)
    out = output_path or graph_path

    # Load existing graph
    if not graph_path.exists():
        raise RuntimeError(
            f"No graph found at {graph_path}.\n"
            "Run 'graphify extract' first to build the initial graph, "
            "or pass --graph PATH to point at an existing graph.json."
        )
    G, raw = load_graph(graph_path)

    # Track edge counts for reporting
    initial_nodes = G.number_of_nodes()
    initial_edges = G.number_of_edges()

    all_affected_communities: set[int] = set()

    for file_change in diff.files:
        if not file_change.is_code_file:
            continue

        result.files_processed += 1

        if file_change.change_type == ChangeType.DELETED:
            _handle_deleted_file(G, raw, file_change, result, cascade_depth, all_affected_communities)
        elif file_change.change_type == ChangeType.ADDED:
            _handle_added_file(G, raw, file_change, result, cascade_depth, all_affected_communities)
        elif file_change.change_type in (ChangeType.MODIFIED, ChangeType.RENAMED):
            _handle_modified_file(G, raw, file_change, result, cascade_depth, all_affected_communities)

    # Re-cluster affected communities
    if all_affected_communities:
        recluster_affected_communities(G, all_affected_communities, raw.get("nodes", []))
        result.communities_reclustered = len(all_affected_communities)

    # Compute final edge delta
    result.edges_added = max(0, G.number_of_edges() - initial_edges)
    result.edges_removed = max(0, initial_edges - G.number_of_edges())

    # Write output
    if not dry_run:
        if built_at_commit:
            raw["built_at_commit"] = built_at_commit
        save_graph(G, raw, out)

    return result


def _handle_deleted_file(
    G: nx.Graph,
    raw: dict,
    fc: FileChange,
    result: PatchResult,
    cascade_depth: int,
    affected_communities: set[int],
) -> None:
    """Handle a deleted file — remove all its nodes and cascade."""
    # Cascade analysis
    cascade = cascade_deleted_file(G, fc.path, cascade_depth)

    # Remove all nodes from this file
    removed = remove_file_nodes(G, raw, fc.path)
    result.nodes_removed += removed

    # Mark transitive dependencies for review
    for node_id in cascade.transitively_affected:
        if node_id in G:
            mark_affected_for_review(G, [node_id])
            result.nodes_marked_for_review += 1

    affected_communities.update(cascade.communities_to_recluster)

    if cascade.transitively_affected:
        result.warnings.append(
            f"Deleted {fc.path}: {removed} nodes removed, "
            f"{len(cascade.transitively_affected)} dependent nodes need review"
        )


def _handle_added_file(
    G: nx.Graph,
    raw: dict,
    fc: FileChange,
    result: PatchResult,
    cascade_depth: int,
    affected_communities: set[int],
) -> None:
    """Handle a new file — we can't fully extract without tree-sitter, so we create stub nodes."""
    # For added files, we create a file-level node and stub symbol nodes
    # The user should run `graphify update` later for full extraction
    file_node_id = f"__stub__:{fc.path}"

    new_nodes = [{
        "id": file_node_id,
        "label": Path(fc.path).stem,
        "file_type": "code",
        "source_file": fc.path,
        "stub": True,
        "needs_extraction": True,
    }]

    # Create stub nodes for detected symbols
    for sym in fc.added_symbols:
        sym_id = f"__stub__:{fc.path}:{sym}"
        new_nodes.append({
            "id": sym_id,
            "label": sym,
            "file_type": "code",
            "source_file": fc.path,
            "stub": True,
            "needs_extraction": True,
        })
        # Add contains edge from file to symbol
        # (we'll add edges after nodes)

    add_nodes_and_edges(G, raw, new_nodes, [])
    result.nodes_added += len(new_nodes)

    # Add contains edges
    for sym in fc.added_symbols:
        sym_id = f"__stub__:{fc.path}:{sym}"
        G.add_edge(file_node_id, sym_id, relation="contains", confidence="EXTRACTED")

    result.warnings.append(
        f"Added {fc.path}: {len(new_nodes)} stub nodes created. "
        f"Run 'graphify update' for full extraction."
    )


def _handle_modified_file(
    G: nx.Graph,
    raw: dict,
    fc: FileChange,
    result: PatchResult,
    cascade_depth: int,
    affected_communities: set[int],
) -> None:
    """Handle a modified file — remove old symbols, add new ones, cascade."""
    # Cascade analysis
    cascade = cascade_modified_file(
        G, fc.path, fc.added_symbols, fc.removed_symbols, cascade_depth
    )

    # Remove deleted symbols
    for sym in fc.removed_symbols:
        removed = remove_symbol_nodes(G, raw, sym, fc.path)
        result.nodes_removed += removed

    # Handle renamed symbols (removed + added with different name)
    # For modified symbols (same name, different signature), update attributes
    for sym in fc.added_symbols:
        existing = find_nodes_by_label(G, sym)
        # Filter to this file
        in_file = [
            nid for nid in existing
            if str(G.nodes[nid].get("source_file", "")).endswith(fc.path)
        ]
        if in_file:
            # Symbol already exists — mark as modified
            for nid in in_file:
                update_node_attributes(G, raw, nid, {"modified": True})
                result.nodes_updated += 1
        else:
            # New symbol — create stub
            sym_id = f"__stub__:{fc.path}:{sym}"
            add_nodes_and_edges(G, raw, [{
                "id": sym_id,
                "label": sym,
                "file_type": "code",
                "source_file": fc.path,
                "stub": True,
                "needs_extraction": True,
            }], [])
            result.nodes_added += 1

    # Mark transitive dependencies for review
    for node_id in cascade.transitively_affected:
        if node_id in G:
            mark_affected_for_review(G, [node_id])
            result.nodes_marked_for_review += 1

    affected_communities.update(cascade.communities_to_recluster)

    if fc.removed_symbols or fc.added_symbols:
        result.warnings.append(
            f"Modified {fc.path}: symbols -{len(fc.removed_symbols)}/+{len(fc.added_symbols)}, "
            f"{len(cascade.transitively_affected)} dependent nodes need review"
        )


def run_from_git(
    repo_path: Path,
    graph_path: Path | None = None,
    since: str | None = None,
    staged: bool = False,
    output_path: Path | None = None,
    dry_run: bool = False,
    cascade_depth: int = 3,
) -> PatchResult:
    """Convenience function: get git diff, parse it, and apply to graph.

    Args:
        repo_path: Path to the git repository.
        graph_path: Path to graph.json. Defaults to repo/graphify-out/graph.json.
        since: Git ref to diff against.
        staged: Diff staged changes.
        output_path: Output path for updated graph.
        dry_run: Compute but don't write.
        cascade_depth: Dependency cascade depth.
    """
    repo = repo_path.resolve()
    graph_path = find_graph_path(repo, graph_path)

    if not graph_path.exists():
        raise RuntimeError(
            f"No graph found at {graph_path}.\n"
            "Run 'graphify extract' first to build the initial graph, "
            "or pass --graph PATH to point at an existing graph.json."
        )

    # Get and parse diff
    raw_diff = get_git_diff(repo, since=since, staged=staged)
    if not raw_diff.strip():
        return PatchResult(dry_run=dry_run)

    diff = parse_diff(raw_diff, repo_root=repo)

    return apply_diff(
        repo_path=repo,
        graph_path=graph_path,
        diff=diff,
        output_path=output_path,
        dry_run=dry_run,
        cascade_depth=cascade_depth,
        built_at_commit=current_head(repo) if not dry_run else None,
    )
