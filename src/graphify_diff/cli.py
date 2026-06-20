"""CLI interface for graphify-diff."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from .diff_parser import detect_graph_baseline, find_graph_path, get_git_diff, parse_diff
from .engine import apply_diff, run_from_git

console = Console()


def _resolve_since(
    repo_path: Path,
    graph_path: Path | None,
    since: str | None,
    staged: bool,
) -> str | None:
    """Use an explicit --since if given; otherwise infer the baseline from the last graph build."""
    if since is not None or staged:
        return since
    baseline = detect_graph_baseline(repo_path, graph_path)
    if baseline:
        label = baseline if len(baseline) <= 12 else baseline[:12]
        console.print(f"[dim]Auto-baseline: diffing since {label} (last graph build)[/dim]")
    return baseline


def _announce_graph(repo_path: Path, gp: Path, explicit: bool) -> None:
    """Print which graph is being used when it was auto-discovered off the default path."""
    if explicit:
        return
    default = repo_path / "graphify-out" / "graph.json"
    if gp.resolve() != default.resolve():
        try:
            shown = gp.relative_to(repo_path)
        except ValueError:
            shown = gp
        console.print(f"[dim]Using graph at {shown}[/dim]")


class GraphifyGroup(click.Group):
    """Command group that accepts subcommands as flags (e.g. `graphify --analyze`)."""

    _FLAG_COMMANDS = {
        "--analyze": "analyze",
        "--patch": "patch",
        "--impact": "impact",
    }

    def parse_args(self, ctx, args):
        rewritten = [self._FLAG_COMMANDS.get(arg, arg) for arg in args]
        return super().parse_args(ctx, rewritten)


@click.group(cls=GraphifyGroup, invoke_without_command=True)
@click.version_option(version="0.1.0")
@click.pass_context
def main(ctx):
    """graphify-diff: Incremental graph updates from git diffs.

    Patch a Graphify knowledge graph without re-extracting the entire codebase.
    Operates on git diffs to identify changed symbols and update the graph
    incrementally, cascading changes to dependent nodes.

    Run a command from any directory by pointing it at a repo, or run it with no
    arguments from inside a repo — the diff baseline is auto-detected from the
    last graph build:

        gdiff analyze
        gdiff patch
        gdiff --patch /path/to/repo --since HEAD~1
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit()


@main.command()
@click.argument("repo", type=click.Path(exists=True), default=".")
@click.option("--graph", "-g", "graph_path", type=click.Path(), default=None,
              help="Path to graph.json (default: REPO/graphify-out/graph.json)")
@click.option("--since", "-s", default=None,
              help="Git ref to diff against (e.g., HEAD~1, main, abc123)")
@click.option("--staged", is_flag=True, default=False,
              help="Diff staged changes instead of unstaged")
@click.option("--output", "-o", "output_path", type=click.Path(), default=None,
              help="Output path (default: overwrite input graph.json)")
@click.option("--dry-run", "-n", is_flag=True, default=False,
              help="Show what would change without writing")
@click.option("--cascade-depth", "-d", default=3, type=int,
              help="How far to cascade dependency changes (default: 3)")
@click.option("--json", "json_output", is_flag=True, default=False,
              help="Output machine-readable JSON")
def patch(
    repo: str,
    graph_path: str | None,
    since: str | None,
    staged: bool,
    output_path: str | None,
    dry_run: bool,
    cascade_depth: int,
    json_output: bool,
):
    """Apply git diff to a Graphify graph.json.

    Run with no arguments from inside a repo to patch against the last graph
    build; pass --since to override the auto-detected baseline.

    Examples:

        # Patch graph with changes since the last graph build
        gdiff patch

        # Patch graph with changes since last commit
        gdiff patch --since HEAD~1

        # Patch graph with changes on a branch
        gdiff patch . --since main

        # Dry run to see what would change
        gdiff patch --since HEAD~1 --dry-run
    """
    repo_path = Path(repo).resolve()
    gp = find_graph_path(repo_path, graph_path)
    _announce_graph(repo_path, gp, explicit=graph_path is not None)
    op = Path(output_path) if output_path else None
    since = _resolve_since(repo_path, gp, since, staged)

    try:
        result = run_from_git(
            repo_path=repo_path,
            graph_path=gp,
            since=since,
            staged=staged,
            output_path=op,
            dry_run=dry_run,
            cascade_depth=cascade_depth,
        )
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

    if json_output:
        import json
        console.print(json.dumps({
            "files_processed": result.files_processed,
            "nodes_added": result.nodes_added,
            "nodes_removed": result.nodes_removed,
            "nodes_updated": result.nodes_updated,
            "nodes_marked_for_review": result.nodes_marked_for_review,
            "edges_added": result.edges_added,
            "edges_removed": result.edges_removed,
            "communities_reclustered": result.communities_reclustered,
            "warnings": result.warnings,
            "dry_run": result.dry_run,
        }, indent=2))
        return

    # Rich output
    if result.files_processed == 0:
        console.print("[yellow]No code changes detected. Graph is up to date.[/yellow]")
        return

    dry_run_banner = "[bold yellow]DRY RUN[/bold yellow] — no changes written\n" if dry_run else ""
    console.print(Panel.fit(
        f"{dry_run_banner}[bold]graphify-diff patch results[/bold]",
        border_style="blue",
    ))

    table = Table(show_header=True, header_style="bold")
    table.add_column("Metric", style="dim")
    table.add_column("Count", justify="right")

    table.add_row("Files processed", str(result.files_processed))
    table.add_row("Nodes added", f"[green]+{result.nodes_added}[/green]")
    table.add_row("Nodes removed", f"[red]-{result.nodes_removed}[/red]")
    table.add_row("Nodes updated", f"[yellow]~{result.nodes_updated}[/yellow]")
    table.add_row("Nodes needing review", f"[bold red]!{result.nodes_marked_for_review}[/bold red]")
    table.add_row("Edges added", f"[green]+{result.edges_added}[/green]")
    table.add_row("Edges removed", f"[red]-{result.edges_removed}[/red]")
    table.add_row("Communities re-clustered", str(result.communities_reclustered))

    console.print(table)

    if result.warnings:
        console.print("\n[bold yellow]Warnings:[/bold yellow]")
        for w in result.warnings:
            console.print(f"  ⚠ {w}")


@main.command()
@click.argument("repo", type=click.Path(exists=True), default=".")
@click.option("--since", "-s", default=None, help="Git ref to diff against")
@click.option("--staged", is_flag=True, default=False, help="Diff staged changes")
def analyze(repo: str, since: str | None, staged: bool):
    """Analyze a git diff and show what would change (no graph needed).

    Read-only: parses the git diff and shows which files, symbols, and
    potential dependencies would be affected. The diff baseline is
    auto-detected from the last graph build when --since is omitted.
    """
    repo_path = Path(repo).resolve()
    gp = find_graph_path(repo_path, None)
    _announce_graph(repo_path, gp, explicit=False)
    since = _resolve_since(repo_path, gp, since, staged)

    try:
        raw_diff = get_git_diff(repo_path, since=since, staged=staged)
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

    if not raw_diff.strip():
        console.print("[yellow]No changes detected.[/yellow]")
        return

    diff = parse_diff(raw_diff, repo_root=repo_path)

    console.print(Panel.fit(
        f"[bold]Diff analysis[/bold] — {len(diff.files)} file(s) changed",
        border_style="blue",
    ))

    # Summary table
    table = Table(show_header=True, header_style="bold")
    table.add_column("File")
    table.add_column("Change")
    table.add_column("Symbols +/-", justify="right")

    for fc in diff.files:
        change_color = {
            "added": "green",
            "modified": "yellow",
            "deleted": "red",
            "renamed": "blue",
        }.get(fc.change_type.value, "white")

        syms = f"[red]-{len(fc.removed_symbols)}[/red] / [green]+{len(fc.added_symbols)}[/green]"
        table.add_row(
            fc.path,
            f"[{change_color}]{fc.change_type.value}[/{change_color}]",
            syms,
        )

    console.print(table)

    # Symbol details
    all_added = diff.all_symbols_added
    all_removed = diff.all_symbols_removed

    if all_added:
        console.print(f"\n[bold green]Added symbols:[/bold green] {', '.join(all_added)}")
    if all_removed:
        console.print(f"[bold red]Removed symbols:[/bold red] {', '.join(all_removed)}")

    # Code vs non-code
    code_files = [f for f in diff.files if f.is_code_file]
    non_code_files = [f for f in diff.files if not f.is_code_file]
    if non_code_files:
        console.print(f"\n[dim]Non-code files (skipped): {len(non_code_files)}[/dim]")


@main.command()
@click.argument("repo", type=click.Path(exists=True), default=".")
@click.option("--graph", "-g", "graph_path", type=click.Path(), default=None,
              help="Path to graph.json")
@click.option("--since", "-s", default=None, help="Git ref to diff against")
@click.option("--depth", "-d", default=2, type=int, help="Cascade depth for impact analysis")
def impact(repo: str, graph_path: str | None, since: str | None, depth: int):
    """Show the cascading impact of changes on the existing graph.

    Loads the existing graph and shows which nodes would be affected
    by the changes, including transitive dependencies. The diff baseline
    is auto-detected from the last graph build when --since is omitted.
    """
    from .cascade import cascade_deleted_file, cascade_modified_file
    from .graph_patcher import load_graph

    repo_path = Path(repo).resolve()
    gp = find_graph_path(repo_path, graph_path)
    _announce_graph(repo_path, gp, explicit=graph_path is not None)

    if not gp.exists():
        console.print(f"[red]No graph found at {gp}[/red]")
        console.print("Run 'graphify extract' first to build the initial graph, or pass --graph PATH.")
        sys.exit(1)

    since = _resolve_since(repo_path, gp, since, staged=False)

    try:
        raw_diff = get_git_diff(repo_path, since=since)
    except RuntimeError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)

    if not raw_diff.strip():
        console.print("[yellow]No changes detected.[/yellow]")
        return

    diff = parse_diff(raw_diff, repo_root=repo_path)
    G, raw = load_graph(gp)

    console.print(Panel.fit(
        f"[bold]Impact analysis[/bold] — graph has {G.number_of_nodes()} nodes, {G.number_of_edges()} edges",
        border_style="blue",
    ))

    total_direct = 0
    total_transitive = 0

    for fc in diff.files:
        if not fc.is_code_file:
            continue

        if fc.change_type.value == "deleted":
            cascade = cascade_deleted_file(G, fc.path, depth)
        else:
            cascade = cascade_modified_file(G, fc.path, fc.added_symbols, fc.removed_symbols, depth)

        total_direct += len(cascade.directly_affected)
        total_transitive += len(cascade.transitively_affected)

        if cascade.directly_affected or cascade.transitively_affected:
            console.print(f"\n[bold]{fc.path}[/bold] ({fc.change_type.value})")
            if cascade.directly_affected:
                console.print(f"  [red]Directly affected:[/red] {len(cascade.directly_affected)}")
                for nid in cascade.directly_affected[:5]:
                    if nid in G:
                        label = G.nodes[nid].get("label", nid)
                        console.print(f"    - {label} [{nid}]")
                if len(cascade.directly_affected) > 5:
                    console.print(f"    ... and {len(cascade.directly_affected) - 5} more")
            if cascade.transitively_affected:
                console.print(f"  [yellow]Transitively affected:[/yellow] {len(cascade.transitively_affected)}")
                for nid in cascade.transitively_affected[:5]:
                    if nid in G:
                        label = G.nodes[nid].get("label", nid)
                        console.print(f"    - {label} [{nid}]")
                if len(cascade.transitively_affected) > 5:
                    console.print(f"    ... and {len(cascade.transitively_affected) - 5} more")

    console.print(f"\n[bold]Total: {total_direct} direct, {total_transitive} transitive[/bold]")


if __name__ == "__main__":
    main()
