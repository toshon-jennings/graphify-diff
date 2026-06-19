"""Tests for graphify-diff."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import networkx as nx
import pytest
from networkx.readwrite import json_graph

from graphify_diff.diff_parser import (
    ChangeType,
    parse_diff,
)
from graphify_diff.graph_patcher import (
    find_nodes_by_label,
    find_nodes_by_source_file,
    load_graph,
    remove_file_nodes,
    remove_symbol_nodes,
    save_graph,
)
from graphify_diff.cascade import (
    cascade_deleted_file,
    cascade_modified_file,
    cascade_removed_symbols,
)
from graphify_diff.engine import apply_diff, PatchResult


@pytest.fixture
def sample_graph_data() -> dict:
    """Create a sample graph.json structure."""
    return {
        "directed": False,
        "multigraph": False,
        "graph": {},
        "nodes": [
            {"id": "utils", "label": "utils", "file_type": "code", "source_file": "src/utils.py", "community": 0},
            {"id": "helper", "label": "helper", "file_type": "code", "source_file": "src/utils.py", "community": 0},
            {"id": "transform", "label": "transform", "file_type": "code", "source_file": "src/utils.py", "community": 0},
            {"id": "main", "label": "main", "file_type": "code", "source_file": "src/main.py", "community": 1},
            {"id": "process", "label": "process", "file_type": "code", "source_file": "src/main.py", "community": 1},
            {"id": "config", "label": "config", "file_type": "code", "source_file": "src/config.py", "community": 0},
            {"id": "app", "label": "App", "file_type": "code", "source_file": "src/app.py", "community": 1},
        ],
        "links": [
            {"source": "utils", "target": "helper", "relation": "contains", "confidence": "EXTRACTED"},
            {"source": "utils", "target": "transform", "relation": "contains", "confidence": "EXTRACTED"},
            {"source": "main", "target": "process", "relation": "contains", "confidence": "EXTRACTED"},
            {"source": "main", "target": "utils", "relation": "imports_from", "confidence": "EXTRACTED"},
            {"source": "main", "target": "helper", "relation": "calls", "confidence": "INFERRED"},
            {"source": "process", "target": "transform", "relation": "calls", "confidence": "INFERRED"},
            {"source": "app", "target": "main", "relation": "calls", "confidence": "EXTRACTED"},
            {"source": "app", "target": "config", "relation": "imports_from", "confidence": "EXTRACTED"},
        ],
    }


@pytest.fixture
def sample_graph(sample_graph_data) -> nx.Graph:
    """Create a NetworkX graph from sample data."""
    return json_graph.node_link_graph(sample_graph_data, edges="links")


@pytest.fixture
def sample_graph_file(sample_graph_data, tmp_path) -> Path:
    """Write sample graph to a temp file."""
    path = tmp_path / "graphify-out" / "graph.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(sample_graph_data, indent=2) + "\n")
    return path


@pytest.fixture
def sample_repo(tmp_path) -> Path:
    """Create a minimal git repo with some source files."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Initialize git
    import subprocess
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], capture_output=True)

    # Create source files
    src = repo / "src"
    src.mkdir()

    (src / "utils.py").write_text("""
def helper():
    pass

def transform():
    pass
""")

    (src / "main.py").write_text("""
from src.utils import helper

def process():
    helper()

if __name__ == "__main__":
    process()
""")

    (src / "config.py").write_text("""
DEBUG = True
""")

    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], capture_output=True)

    return repo


class TestDiffParser:
    """Tests for the diff parser."""

    def test_parse_added_file(self):
        raw = """diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,5 @@
+def hello():
+    return "world"
+
+class Greeter:
+    pass
"""
        result = parse_diff(raw)
        assert len(result.files) == 1
        assert result.files[0].path == "src/new.py"
        assert result.files[0].change_type == ChangeType.ADDED
        assert result.files[0].is_code_file
        assert "hello" in result.files[0].added_symbols
        assert "Greeter" in result.files[0].added_symbols

    def test_parse_deleted_file(self):
        raw = """diff --git a/src/old.py b/src/old.py
deleted file mode 100644
index 1234567..0000000
--- a/src/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def old_function():
-    pass
-
"""
        result = parse_diff(raw)
        assert len(result.files) == 1
        assert result.files[0].change_type == ChangeType.DELETED

    def test_parse_modified_file(self):
        raw = """diff --git a/src/utils.py b/src/utils.py
index 1234567..abcdefg 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,5 +1,6 @@
 def helper():
     pass

-def transform():
+def transform_v2():
     pass
+
+def new_helper():
+    pass
"""
        result = parse_diff(raw)
        assert len(result.files) == 1
        assert result.files[0].change_type == ChangeType.MODIFIED
        # Should detect the new function
        assert "new_helper" in result.files[0].added_symbols

    def test_parse_multiple_files(self):
        raw = """diff --git a/src/a.py b/src/a.py
index 111..222 100644
--- a/src/a.py
+++ b/src/a.py
@@ -1,2 +1,2 @@
-def old():
+def new():
     pass
diff --git a/src/b.js b/src/b.js
new file mode 100644
index 0000000..333
--- /dev/null
+++ b/src/b.js
@@ -0,0 +1,3 @@
+function greet() {
+  return 'hi';
+}
"""
        result = parse_diff(raw)
        assert len(result.files) == 2
        assert result.files[0].path == "src/a.py"
        assert result.files[1].path == "src/b.js"
        assert result.files[1].change_type == ChangeType.ADDED
        assert "greet" in result.files[1].added_symbols

    def test_non_code_file_ignored(self):
        raw = """diff --git a/README.md b/README.md
index 111..222 100644
--- a/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
-# Old Title
+# New Title
"""
        result = parse_diff(raw)
        assert len(result.files) == 1
        assert not result.files[0].is_code_file

    def test_parse_rename(self):
        raw = """diff --git a/src/old.py b/src/new.py
similarity index 100%
rename from src/old.py
rename to src/new.py
"""
        result = parse_diff(raw)
        assert len(result.files) == 1
        assert result.files[0].change_type == ChangeType.RENAMED
        assert result.files[0].path == "src/new.py"
        assert result.files[0].old_path == "src/old.py"


class TestGraphPatcher:
    """Tests for graph loading, patching, and saving."""

    def test_load_and_save_graph(self, sample_graph_data, tmp_path):
        path = tmp_path / "graph.json"
        path.write_text(json.dumps(sample_graph_data, indent=2))

        G, raw = load_graph(path)
        assert G.number_of_nodes() == 7
        assert G.number_of_edges() == 8

        out = tmp_path / "graph-out.json"
        save_graph(G, raw, out)
        assert out.exists()

        # Round-trip
        G2, raw2 = load_graph(out)
        assert G2.number_of_nodes() == G.number_of_nodes()

    def test_find_nodes_by_label(self, sample_graph):
        results = find_nodes_by_label(sample_graph, "helper")
        assert len(results) == 1
        assert results[0] == "helper"

        results = find_nodes_by_label(sample_graph, "HELPER")
        assert len(results) == 1  # case-insensitive

    def test_find_nodes_by_source_file(self, sample_graph):
        results = find_nodes_by_source_file(sample_graph, "src/utils.py")
        assert len(results) == 3  # utils, helper, transform

    def test_remove_symbol_nodes(self, sample_graph_data):
        G = json_graph.node_link_graph(sample_graph_data, edges="links")

        removed = remove_symbol_nodes(G, sample_graph_data, "transform")
        assert removed == 1
        assert "transform" not in G

        # Edges to/from transform should also be gone
        assert G.number_of_edges() < 8

    def test_remove_file_nodes(self, sample_graph_data):
        G = json_graph.node_link_graph(sample_graph_data, edges="links")

        removed = remove_file_nodes(G, sample_graph_data, "src/utils.py")
        assert removed == 3  # utils, helper, transform

        # All nodes from utils.py should be gone
        for node_id in G.nodes():
            sf = G.nodes[node_id].get("source_file", "")
            assert not sf.endswith("src/utils.py")


class TestCascade:
    """Tests for the cascade engine."""

    def test_cascade_removed_symbol(self, sample_graph_data):
        G = json_graph.node_link_graph(sample_graph_data, edges="links")

        cascade = cascade_removed_symbols(G, ["helper"], file_path="src/utils.py")
        assert "helper" in cascade.directly_affected
        # main.py calls helper, so main or process should be transitively affected
        assert len(cascade.transitively_affected) > 0

    def test_cascade_deleted_file(self, sample_graph_data):
        G = json_graph.node_link_graph(sample_graph_data, edges="links")

        cascade = cascade_deleted_file(G, "src/utils.py")
        # All 3 nodes from utils.py should be directly affected
        assert len(cascade.directly_affected) == 3
        # main.py imports from utils.py, so some transitive impact expected
        assert len(cascade.transitively_affected) > 0
        # Community 0 (where utils.py lives) should be marked for re-clustering
        assert len(cascade.communities_to_recluster) > 0

    def test_cascade_modified_file(self, sample_graph_data):
        G = json_graph.node_link_graph(sample_graph_data, edges="links")

        cascade = cascade_modified_file(
            G, "src/utils.py",
            added_symbols=["new_func"],
            removed_symbols=["transform"],
        )
        assert "transform" in cascade.directly_affected
        assert any("__new__:new_func" in a for a in cascade.directly_affected)


class TestEngine:
    """Tests for the main orchestrator."""

    def test_apply_diff_deleted_file(self, sample_graph_data, sample_graph_file, tmp_path):
        raw = """diff --git a/src/utils.py b/src/utils.py
deleted file mode 100644
index 1234567..0000000
--- a/src/utils.py
+++ /dev/null
@@ -1,5 +0,0 @@
-def helper():
-    pass
-
-def transform():
-    pass
"""
        from graphify_diff.diff_parser import parse_diff

        diff = parse_diff(raw)
        result = apply_diff(
            repo_path=tmp_path,
            graph_path=sample_graph_file,
            diff=diff,
            output_path=tmp_path / "graph-out.json",
            dry_run=False,
        )

        assert result.files_processed == 1
        assert result.nodes_removed == 3  # utils, helper, transform
        assert result.nodes_marked_for_review > 0  # main.py depends on utils.py

        # Verify graph was written
        out_path = tmp_path / "graph-out.json"
        assert out_path.exists()

        data = json.loads(out_path.read_text())
        # Should have 7 - 3 = 4 nodes remaining
        assert len(data["nodes"]) == 4

    def test_apply_diff_modified_file(self, sample_graph_data, sample_graph_file, tmp_path):
        raw = """diff --git a/src/utils.py b/src/utils.py
index 1234567..abcdefg 100644
--- a/src/utils.py
+++ b/src/utils.py
@@ -1,5 +1,6 @@
 def helper():
     pass

+def new_helper():
+    pass
"""
        from graphify_diff.diff_parser import parse_diff

        diff = parse_diff(raw)
        result = apply_diff(
            repo_path=tmp_path,
            graph_path=sample_graph_file,
            diff=diff,
            output_path=tmp_path / "graph-out.json",
            dry_run=False,
        )

        assert result.files_processed == 1
        assert result.nodes_added > 0  # new_helper stub

    def test_apply_diff_dry_run(self, sample_graph_data, sample_graph_file, tmp_path):
        raw = """diff --git a/src/utils.py b/src/utils.py
deleted file mode 100644
--- a/src/utils.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def helper():
-    pass
"""
        from graphify_diff.diff_parser import parse_diff

        diff = parse_diff(raw)
        result = apply_diff(
            repo_path=tmp_path,
            graph_path=sample_graph_file,
            diff=diff,
            dry_run=True,
        )

        assert result.dry_run
        assert result.nodes_removed > 0
        # Original graph should be unchanged
        data = json.loads(sample_graph_file.read_text())
        assert len(data["nodes"]) == 7  # unchanged

    def test_no_code_changes(self, sample_graph_data, sample_graph_file, tmp_path):
        raw = """diff --git a/README.md b/README.md
index 111..222 100644
--- a/README/README.md
+++ b/README.md
@@ -1,2 +1,2 @@
-# Old
+# New
"""
        from graphify_diff.diff_parser import parse_diff

        diff = parse_diff(raw)
        result = apply_diff(
            repo_path=tmp_path,
            graph_path=sample_graph_file,
            diff=diff,
            output_path=tmp_path / "graph-out.json",
        )

        assert result.files_processed == 0  # README.md is not a code file

    def test_run_from_git(self, sample_repo):
        """Test the full flow with a real git repo."""
        import subprocess

        # Create graph.json
        graph_dir = sample_repo / "graphify-out"
        graph_dir.mkdir()
        graph_data = {
            "directed": False,
            "multigraph": False,
            "graph": {},
            "nodes": [
                {"id": "src_utils", "label": "utils", "file_type": "code", "source_file": "src/utils.py", "community": 0},
                {"id": "helper", "label": "helper", "file_type": "code", "source_file": "src/utils.py", "community": 0},
                {"id": "transform", "label": "transform", "file_type": "code", "source_file": "src/utils.py", "community": 0},
            ],
            "links": [
                {"source": "src_utils", "target": "helper", "relation": "contains", "confidence": "EXTRACTED"},
                {"source": "src_utils", "target": "transform", "relation": "contains", "confidence": "EXTRACTED"},
            ],
        }
        (graph_dir / "graph.json").write_text(json.dumps(graph_data, indent=2))

        # Make a change
        (sample_repo / "src" / "utils.py").write_text("""
def helper():
    pass

def new_function():
    pass
""")
        subprocess.run(["git", "-C", str(sample_repo), "add", "."], capture_output=True)
        subprocess.run(["git", "-C", str(sample_repo), "commit", "-m", "update utils"], capture_output=True)

        from graphify_diff.engine import run_from_git

        result = run_from_git(
            repo_path=sample_repo,
            since="HEAD~1",
            dry_run=True,
        )

        assert result.files_processed >= 1  # at least utils.py
