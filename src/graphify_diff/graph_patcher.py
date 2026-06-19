"""Graph loading, patching, and saving — mutate a Graphify graph.json in-place."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

import networkx as nx
from networkx.readwrite import json_graph


def _normalize_id(s: str) -> str:
    """Normalize an ID the same way Graphify's extract._make_id does."""
    s = unicodedata.normalize("NFKC", s)
    cleaned = re.sub(r"[^\w]+", "_", s, flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_").casefold()


def _file_stem(rel_path: str) -> str:
    """Return stem qualified with parent dir: {parent_dir}_{stem}."""
    p = Path(rel_path)
    parent = p.parent.name
    if parent and parent not in (".", ""):
        return f"{parent}.{p.stem}"
    return p.stem


def _file_node_id(rel_path: str) -> str:
    """File-level node ID matching Graphify's convention."""
    return _normalize_id(_file_stem(rel_path))


def load_graph(graph_path: Path) -> tuple[nx.Graph, dict]:
    """Load a Graphify graph.json and return (NetworkX graph, raw data dict)."""
    raw = json.loads(graph_path.read_text(encoding="utf-8"))

    # Normalize edges key
    if "edges" not in raw and "links" in raw:
        raw["links"] = raw["links"]  # keep as-is for node_link_graph

    try:
        G = json_graph.node_link_graph(raw, edges="links")
    except (TypeError, KeyError):
        try:
            G = json_graph.node_link_graph(raw, edges="edges")
        except (TypeError, KeyError):
            G = json_graph.node_link_graph(raw)

    return G, raw


def save_graph(G: nx.Graph, raw_data: dict, output_path: Path) -> None:
    """Save a NetworkX graph back to Graphify graph.json format."""
    try:
        data = json_graph.node_link_data(G, edges="links")
    except TypeError:
        data = json_graph.node_link_data(G)

    # Preserve hyperedges
    hyperedges = getattr(G, "graph", {}).get("hyperedges", [])
    if hyperedges:
        data["hyperedges"] = hyperedges

    # Preserve metadata from raw_data
    for key in ("built_at_commit", "input_tokens", "output_tokens", "version"):
        if key in raw_data:
            data[key] = raw_data[key]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def find_nodes_by_label(graph: nx.Graph, label: str) -> list[str]:
    """Find node IDs whose label matches (case-insensitive)."""
    label_lower = label.lower()
    matches = []
    for node_id, data in graph.nodes(data=True):
        node_label = str(data.get("label", ""))
        if node_label.lower() == label_lower:
            matches.append(str(node_id))
    return matches


def find_nodes_by_source_file(graph: nx.Graph, source_file: str) -> list[str]:
    """Find all nodes belonging to a given source file."""
    matches = []
    for node_id, data in graph.nodes(data=True):
        sf = str(data.get("source_file", ""))
        if sf == source_file or sf.endswith(source_file):
            matches.append(str(node_id))
    return matches


def find_node_by_id_prefix(graph: nx.Graph, prefix: str) -> str | None:
    """Find a node whose ID starts with the given prefix."""
    prefix_lower = prefix.lower()
    for node_id in graph.nodes():
        if str(node_id).lower().startswith(prefix_lower):
            return str(node_id)
    return None


def remove_file_nodes(graph: nx.Graph, raw_data: dict, file_path: str) -> int:
    """Remove all nodes and edges associated with a deleted file. Returns count removed."""
    nodes_to_remove = find_nodes_by_source_file(graph, file_path)
    graph.remove_nodes_from(nodes_to_remove)

    # Also remove from raw_data
    if nodes_to_remove:
        node_set = set(nodes_to_remove)
        raw_data["nodes"] = [n for n in raw_data.get("nodes", []) if n.get("id") not in node_set]
        # Remove edges that reference removed nodes
        for edge_key in ("edges", "links"):
            if edge_key in raw_data:
                raw_data[edge_key] = [
                    e for e in raw_data[edge_key]
                    if e.get("source") not in node_set and e.get("target") not in node_set
                ]

    return len(nodes_to_remove)


def remove_symbol_nodes(
    graph: nx.Graph,
    raw_data: dict,
    symbol_name: str,
    file_path: str | None = None,
) -> int:
    """Remove nodes matching a symbol name, optionally scoped to a file. Returns count removed."""
    candidates = find_nodes_by_label(graph, symbol_name)
    to_remove = []

    for node_id in candidates:
        data = graph.nodes[node_id]
        if file_path:
            sf = str(data.get("source_file", ""))
            if sf != file_path and not sf.endswith(file_path):
                continue
        # Only remove code nodes
        if data.get("file_type") == "code":
            to_remove.append(node_id)

    graph.remove_nodes_from(to_remove)

    if to_remove:
        node_set = set(to_remove)
        raw_data["nodes"] = [n for n in raw_data.get("nodes", []) if n.get("id") not in node_set]
        for edge_key in ("edges", "links"):
            if edge_key in raw_data:
                raw_data[edge_key] = [
                    e for e in raw_data[edge_key]
                    if e.get("source") not in node_set and e.get("target") not in node_set
                ]

    return len(to_remove)


def add_nodes_and_edges(
    graph: nx.Graph,
    raw_data: dict,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> None:
    """Add new nodes and edges to the graph."""
    existing_ids = set(str(n) for n in graph.nodes())

    for node in nodes:
        node_id = str(node.get("id", ""))
        if node_id and node_id not in existing_ids:
            graph.add_node(node_id, **{k: v for k, v in node.items() if k != "id"})
            existing_ids.add(node_id)
            raw_data.setdefault("nodes", []).append(node)

    for edge in edges:
        src = str(edge.get("source", ""))
        tgt = str(edge.get("target", ""))
        if src in existing_ids and tgt in existing_ids:
            graph.add_edge(src, tgt, **{k: v for k, v in edge.items() if k not in ("source", "target")})
            edge_key = "edges" if "edges" in raw_data else "links"
            raw_data.setdefault(edge_key, []).append(edge)


def update_node_attributes(
    graph: nx.Graph,
    raw_data: dict,
    node_id: str,
    attributes: dict[str, Any],
) -> bool:
    """Update attributes on an existing node. Returns True if node was found."""
    if node_id not in graph:
        return False

    for key, value in attributes.items():
        graph.nodes[node_id][key] = value

    # Update in raw_data too
    for node in raw_data.get("nodes", []):
        if str(node.get("id")) == node_id:
            node.update(attributes)
            break

    return True


def get_reverse_dependencies(graph: nx.Graph, node_id: str, max_depth: int = 3) -> list[tuple[str, int]]:
    """Get nodes that depend on (point to) the given node, up to max_depth."""
    visited = {node_id}
    queue = [(node_id, 0)]
    results = []

    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue

        # Find all nodes that have an edge pointing TO current
        for pred in graph.predecessors(current) if hasattr(graph, "predecessors") else []:
            if pred not in visited:
                visited.add(pred)
                results.append((str(pred), depth + 1))
                queue.append((pred, depth + 1))

        # For undirected graphs, also check neighbors
        if not graph.is_directed():
            for neighbor in graph.neighbors(current):
                if neighbor not in visited:
                    visited.add(neighbor)
                    results.append((str(neighbor), depth + 1))
                    queue.append((neighbor, depth + 1))

    return results


def get_forward_dependencies(graph: nx.Graph, node_id: str, max_depth: int = 3) -> list[tuple[str, int]]:
    """Get nodes that the given node depends on (points to), up to max_depth."""
    visited = {node_id}
    queue = [(node_id, 0)]
    results = []

    while queue:
        current, depth = queue.pop(0)
        if depth >= max_depth:
            continue

        for succ in graph.successors(current) if hasattr(graph, "successors") else []:
            if succ not in visited:
                visited.add(succ)
                results.append((str(succ), depth + 1))
                queue.append((succ, depth + 1))

        if not graph.is_directed():
            for neighbor in graph.neighbors(current):
                if neighbor not in visited:
                    visited.add(neighbor)
                    results.append((str(neighbor), depth + 1))
                    queue.append((neighbor, depth + 1))

    return results
