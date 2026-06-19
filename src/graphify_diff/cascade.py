"""Cascade engine — determine which nodes/edges are affected by changes and need updating."""

from __future__ import annotations

import networkx as nx
from dataclasses import dataclass, field

from .graph_patcher import (
    find_nodes_by_label,
    find_nodes_by_source_file,
    get_reverse_dependencies,
)


@dataclass
class CascadeResult:
    """Result of a cascade analysis."""
    directly_affected: list[str] = field(default_factory=list)  # node IDs directly changed
    transitively_affected: list[str] = field(default_factory=list)  # node IDs affected by propagation
    edges_to_review: list[tuple[str, str]] = field(default_factory=list)  # edges that may need updating
    communities_to_recluster: set[int] = field(default_factory=set)

    @property
    def all_affected(self) -> list[str]:
        return self.directly_affected + self.transitively_affected


def cascade_removed_symbols(
    graph: nx.Graph,
    symbol_names: list[str],
    file_path: str | None = None,
    max_depth: int = 3,
) -> CascadeResult:
    """Determine the impact of removing symbols from the graph.

    When a symbol is removed, we need to:
    1. Remove the symbol node itself
    2. Find all nodes that called/imported/referenced it (reverse deps)
    3. Flag edges that pointed to the removed node
    4. Identify communities that need re-clustering
    """
    result = CascadeResult()

    for symbol_name in symbol_names:
        candidates = find_nodes_by_label(graph, symbol_name)
        for node_id in candidates:
            data = graph.nodes[node_id]
            if data.get("file_type") != "code":
                continue
            if file_path:
                sf = str(data.get("source_file", ""))
                if sf != file_path and not sf.endswith(file_path):
                    continue

            result.directly_affected.append(node_id)

            # Find reverse dependencies — nodes that depend on this symbol
            rev_deps = get_reverse_dependencies(graph, node_id, max_depth)
            for dep_id, depth in rev_deps:
                if dep_id not in result.directly_affected:
                    result.transitively_affected.append(dep_id)

            # Collect edges that touch this node
            for _, target, _ in graph.edges(node_id, data=True):
                result.edges_to_review.append((node_id, str(target)))
            if not graph.is_directed():
                for source, _, _ in graph.in_edges(node_id, data=True) if hasattr(graph, "in_edges") else []:
                    result.edges_to_review.append((str(source), node_id))

    # Identify affected communities
    for node_id in result.all_affected:
        if node_id in graph:
            comm = graph.nodes[node_id].get("community")
            if comm is not None:
                try:
                    result.communities_to_recluster.add(int(comm))
                except (TypeError, ValueError):
                    pass

    return result


def cascade_added_symbols(
    graph: nx.Graph,
    symbol_names: list[str],
    file_path: str | None = None,
    max_depth: int = 2,
) -> CascadeResult:
    """Determine the impact of adding new symbols.

    When a symbol is added:
    1. The symbol node itself is new
    2. We need to check if existing nodes might call/reference it
    3. We need to check if it calls/references existing nodes
    4. Identify communities that need re-clustering
    """
    result = CascadeResult()

    for symbol_name in symbol_names:
        # Check if this symbol already exists (it shouldn't, but be safe)
        existing = find_nodes_by_label(graph, symbol_name)
        if existing:
            # Symbol already in graph — it's a modification, not an addition
            for node_id in existing:
                result.directly_affected.append(node_id)
        else:
            # New symbol — mark it as directly affected (will be added)
            # We use a synthetic ID based on the symbol name
            result.directly_affected.append(f"__new__:{symbol_name}")

    # For new symbols in existing files, check the file's community
    if file_path:
        file_nodes = find_nodes_by_source_file(graph, file_path)
        for node_id in file_nodes:
            comm = graph.nodes[node_id].get("community")
            if comm is not None:
                try:
                    result.communities_to_recluster.add(int(comm))
                except (TypeError, ValueError):
                    pass

    return result


def cascade_modified_file(
    graph: nx.Graph,
    file_path: str,
    added_symbols: list[str],
    removed_symbols: list[str],
    max_depth: int = 3,
) -> CascadeResult:
    """Determine the impact of modifications to a file.

    Combines the logic of added and removed symbols, plus handles
    the case where symbols were modified (same name, different signature).
    """
    result = CascadeResult()

    # Handle removed symbols
    if removed_symbols:
        removal = cascade_removed_symbols(graph, removed_symbols, file_path, max_depth)
        result.directly_affected.extend(removal.directly_affected)
        result.transitively_affected.extend(removal.transitively_affected)
        result.edges_to_review.extend(removal.edges_to_review)
        result.communities_to_recluster.update(removal.communities_to_recluster)

    # Handle added symbols
    if added_symbols:
        addition = cascade_added_symbols(graph, added_symbols, file_path, max_depth)
        result.directly_affected.extend(addition.directly_affected)
        result.transitively_affected.extend(addition.transitively_affected)
        result.edges_to_review.extend(addition.edges_to_review)
        result.communities_to_recluster.update(addition.communities_to_recluster)

    # Deduplicate
    result.directly_affected = list(dict.fromkeys(result.directly_affected))
    result.transitively_affected = [
        x for x in dict.fromkeys(result.transitively_affected)
        if x not in result.directly_affected
    ]

    return result


def cascade_deleted_file(
    graph: nx.Graph,
    file_path: str,
    max_depth: int = 3,
) -> CascadeResult:
    """Determine the impact of deleting an entire file.

    All nodes from the file are removed, plus we need to cascade
    to any nodes that depended on symbols from this file.
    """
    result = CascadeResult()

    # All nodes from this file are directly affected
    file_nodes = find_nodes_by_source_file(graph, file_path)
    result.directly_affected.extend(file_nodes)

    # Find transitive dependencies
    for node_id in file_nodes:
        rev_deps = get_reverse_dependencies(graph, node_id, max_depth)
        for dep_id, depth in rev_deps:
            if dep_id not in result.directly_affected and dep_id not in result.transitively_affected:
                result.transitively_affected.append(dep_id)

    # Collect edges
    for node_id in file_nodes:
        if node_id in graph:
            for _, target, _ in graph.edges(node_id, data=True):
                result.edges_to_review.append((node_id, str(target)))

    # Communities
    for node_id in result.all_affected:
        if node_id in graph:
            comm = graph.nodes[node_id].get("community")
            if comm is not None:
                try:
                    result.communities_to_recluster.add(int(comm))
                except (TypeError, ValueError):
                    pass

    return result
