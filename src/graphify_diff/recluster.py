"""Local re-clustering — re-run community detection only on affected parts of the graph."""

from __future__ import annotations

import networkx as nx


def recluster_affected_communities(
    graph: nx.Graph,
    affected_communities: set[int],
    all_nodes: list[dict],
) -> None:
    """Re-cluster only the affected communities in the graph.

    For small changes, this is much faster than re-clustering the entire graph.
    We extract the subgraph of affected communities, re-cluster it, and write
    the new community assignments back.

    If community detection libraries are not available, we fall back to
    marking affected nodes with a placeholder community.
    """
    if not affected_communities:
        return

    # Collect all nodes in affected communities
    affected_nodes = []
    for node_id, data in graph.nodes(data=True):
        comm = data.get("community")
        if comm is not None:
            try:
                if int(comm) in affected_communities:
                    affected_nodes.append(node_id)
            except (TypeError, ValueError):
                pass

    if not affected_nodes:
        return

    # Include neighbors of affected nodes for context
    context_nodes = set(affected_nodes)
    for node_id in affected_nodes:
        if node_id in graph:
            context_nodes.update(graph.neighbors(node_id))

    # Extract subgraph
    subgraph = graph.subgraph(context_nodes).copy()

    # Try to re-cluster the subgraph
    new_communities = _cluster_subgraph(subgraph)

    # Write new community assignments back to the main graph
    for node_id, comm_id in new_communities.items():
        if node_id in graph:
            graph.nodes[node_id]["community"] = comm_id

    # Update raw node data too
    for node_data in all_nodes:
        node_id = node_data.get("id")
        if node_id in new_communities:
            node_data["community"] = new_communities[node_id]


def _cluster_subgraph(subgraph: nx.Graph) -> dict[str, int]:
    """Run community detection on a subgraph. Returns {node_id: community_id}."""
    if len(subgraph) < 3:
        # Too small to cluster meaningfully — assign all to community 0
        return {str(n): 0 for n in subgraph.nodes()}

    try:
        import community as community_louvain  # python-louvain

        # Convert to undirected for Louvain
        undirected = subgraph.to_undirected() if subgraph.is_directed() else subgraph
        partition = community_louvain.best_partition(undirected, random_state=42)
        return {str(node): int(comm) for node, comm in partition.items()}

    except ImportError:
        pass

    try:
        # Fallback: use NetworkX's greedy modularity communities
        from networkx.algorithms.community import greedy_modularity_communities

        undirected = subgraph.to_undirected() if subgraph.is_directed() else subgraph
        communities = greedy_modularity_communities(undirected)
        result = {}
        for comm_id, comm_nodes in enumerate(communities):
            for node in comm_nodes:
                result[str(node)] = comm_id
        return result

    except Exception:
        pass

    # Last resort: connected components as communities
    undirected = subgraph.to_undirected() if subgraph.is_directed() else subgraph
    result = {}
    for comm_id, component in enumerate(nx.connected_components(undirected)):
        for node in component:
            result[str(node)] = comm_id
    return result


def mark_affected_for_review(
    graph: nx.Graph,
    affected_nodes: list[str],
) -> None:
    """Mark transitively affected nodes for human review.

    These are nodes that may need their edges updated but we can't
    determine the exact changes without re-extracting.
    """
    for node_id in affected_nodes:
        if node_id in graph:
            graph.nodes[node_id]["needs_review"] = True
            graph.nodes[node_id]["review_reason"] = "transitively_affected_by_change"
