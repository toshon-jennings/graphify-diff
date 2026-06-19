"""Git diff parsing — extract structured change information from git diffs."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


@dataclass
class Hunk:
    """A single diff hunk within a file."""
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)
    context_lines: list[str] = field(default_factory=list)


@dataclass
class FileChange:
    """All changes for a single file."""
    path: str
    change_type: ChangeType
    old_path: str | None = None  # for renames
    hunks: list[Hunk] = field(default_factory=list)
    added_symbols: list[str] = field(default_factory=list)
    removed_symbols: list[str] = field(default_factory=list)
    is_code_file: bool = False


@dataclass
class DiffResult:
    """Complete parsed diff."""
    files: list[FileChange] = field(default_factory=list)

    @property
    def changed_files(self) -> list[str]:
        return [f.path for f in self.files]

    @property
    def added_files(self) -> list[str]:
        return [f.path for f in self.files if f.change_type == ChangeType.ADDED]

    @property
    def deleted_files(self) -> list[str]:
        return [f.path for f in self.files if f.change_type == ChangeType.DELETED]

    @property
    def modified_files(self) -> list[str]:
        return [f.path for f in self.files if f.change_type == ChangeType.MODIFIED]

    @property
    def all_symbols_added(self) -> list[str]:
        syms = []
        for f in self.files:
            syms.extend(f.added_symbols)
        return syms

    @property
    def all_symbols_removed(self) -> list[str]:
        syms = []
        for f in self.files:
            syms.extend(f.removed_symbols)
        return syms


# File extensions we care about (code files that Graphify tracks)
CODE_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs",
    ".java", ".cpp", ".cc", ".c", ".h", ".hpp", ".rb", ".swift",
    ".kt", ".cs", ".scala", ".php", ".lua", ".zig", ".sh", ".bash",
}


def _is_code_file(path: str) -> bool:
    return Path(path).suffix.lower() in CODE_EXTENSIONS


def _extract_symbols_from_lines(lines: list[str], extension: str) -> list[str]:
    """Extract function/class/def names from added or removed lines."""
    symbols = []
    ext = extension.lower()

    if ext == ".py":
        # Match: def function_name, class ClassName
        for line in lines:
            m = re.match(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))

    elif ext in (".ts", ".tsx", ".js", ".jsx", ".mjs"):
        # Match: function name, const name = ..., class name, export function, etc.
        for line in lines:
            # function declarations
            m = re.match(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))
                continue
            # const/let/var with function expression or arrow
            m = re.match(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?:async\s+)?(?:\(|function)", line)
            if m:
                symbols.append(m.group(1))
                continue
            # class declarations
            m = re.match(r"^\s*(?:export\s+)?class\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))
                continue
            # method definitions inside classes
            m = re.match(r"^\s*(?:async\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^)]*\)\s*\{", line)
            if m and m.group(1) not in ("if", "while", "for", "switch", "catch"):
                symbols.append(m.group(1))

    elif ext in (".go",):
        # Match: func name, type name struct, type name interface
        for line in lines:
            m = re.match(r"^\s*func\s+(?:\([^)]+\)\s+)?([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))
                continue
            m = re.match(r"^\s*type\s+([A-Za-z_][A-Za-z0-9_]*)\s+(?:struct|interface)", line)
            if m:
                symbols.append(m.group(1))

    elif ext in (".rs",):
        # Match: fn name, struct name, enum name, trait name, impl name
        for line in lines:
            m = re.match(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))
                continue
            m = re.match(r"^\s*(?:pub\s+)?(?:struct|enum|trait|type)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))

    elif ext in (".java", ".kt", ".scala", ".groovy"):
        # Match: def/method, class, interface
        for line in lines:
            m = re.match(r"^\s*(?:public|private|protected|static|\s)*(?:fun|def|void|int|String|boolean|long|double|float)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
            if m:
                symbols.append(m.group(1))
                continue
            m = re.match(r"^\s*(?:public|private|protected|abstract|\s)*(?:class|interface|object)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))

    elif ext in (".c", ".h", ".cpp", ".hpp", ".cc"):
        # Match: function definitions, struct/enum/typedef
        for line in lines:
            m = re.match(r"^\s*(?:static\s+)?(?:inline\s+)?(?:const\s+)?(?:void|int|char|long|float|double|bool|struct\s+\w+|size_t|unsigned)\s+\*?([A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
            if m:
                symbols.append(m.group(1))
                continue
            m = re.match(r"^\s*(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))

    elif ext in (".rb",):
        # Match: def name, class name, module name
        for line in lines:
            m = re.match(r"^\s*def\s+(?:self\.)?([A-Za-z_][A-Za-z0-9_]*[!?]?)", line)
            if m:
                symbols.append(m.group(1))
                continue
            m = re.match(r"^\s*(?:class|module)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))

    elif ext in (".sh", ".bash"):
        # Match: function name() {, function name {
        for line in lines:
            m = re.match(r"^\s*(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{", line)
            if m:
                symbols.append(m.group(1))

    else:
        # Generic: try common patterns
        for line in lines:
            m = re.match(r"^\s*(?:def|function|func|fn|fun)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))
                continue
            m = re.match(r"^\s*(?:class|struct|trait|enum|type)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if m:
                symbols.append(m.group(1))

    return symbols


def parse_diff(raw_diff: str, repo_root: Path | None = None) -> DiffResult:
    """Parse a unified diff string into structured FileChange objects."""
    result = DiffResult()
    current_file: FileChange | None = None
    current_hunk: Hunk | None = None

    for line in raw_diff.splitlines():
        # File header: diff --git a/path b/path
        m = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if m:
            if current_file is not None:
                if current_hunk is not None:
                    current_file.hunks.append(current_hunk)
                result.files.append(current_file)
            old_path = m.group(1)
            new_path = m.group(2)
            current_file = FileChange(
                path=new_path,
                change_type=ChangeType.MODIFIED,
                old_path=old_path if old_path != new_path else None,
                is_code_file=_is_code_file(new_path),
            )
            current_hunk = None
            continue

        # File status lines
        if line.startswith("new file mode "):
            if current_file:
                current_file.change_type = ChangeType.ADDED
            continue
        if line.startswith("deleted file mode "):
            if current_file:
                current_file.change_type = ChangeType.DELETED
            continue
        if line.startswith("rename from "):
            if current_file:
                current_file.change_type = ChangeType.RENAMED
                current_file.old_path = line[len("rename from "):]
            continue
        if line.startswith("rename to "):
            if current_file:
                current_file.path = line[len("rename to "):]
            continue
        if line.startswith("similarity index "):
            continue

        # Hunk header: @@ -old_start,old_lines +new_start,new_lines @@
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if m and current_file is not None:
            if current_hunk is not None:
                current_file.hunks.append(current_hunk)
            current_hunk = Hunk(
                old_start=int(m.group(1)),
                old_lines=int(m.group(2)) if m.group(2) else 1,
                new_start=int(m.group(3)),
                new_lines=int(m.group(4)) if m.group(4) else 1,
            )
            continue

        # Hunk content
        if current_hunk is not None:
            if line.startswith("+") and not line.startswith("+++"):
                current_hunk.added_lines.append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                current_hunk.removed_lines.append(line[1:])
            elif line.startswith(" "):
                current_hunk.context_lines.append(line[1:])

    # Finalize last file
    if current_file is not None:
        if current_hunk is not None:
            current_file.hunks.append(current_hunk)
        result.files.append(current_file)

    # Extract symbols from added/removed lines
    for fc in result.files:
        if not fc.is_code_file:
            continue
        ext = Path(fc.path).suffix
        all_added = []
        all_removed = []
        for hunk in fc.hunks:
            all_added.extend(hunk.added_lines)
            all_removed.extend(hunk.removed_lines)
        fc.added_symbols = _extract_symbols_from_lines(all_added, ext)
        fc.removed_symbols = _extract_symbols_from_lines(all_removed, ext)

    return result


def get_git_diff(
    repo_path: Path,
    since: str | None = None,
    staged: bool = False,
    from_diff: str | None = None,
) -> str:
    """Get git diff output from the repository.

    Args:
        repo_path: Path to the git repository root.
        since: Git ref to diff against (e.g., "HEAD~1", "main", "abc123").
               If None, diffs unstaged changes.
        staged: If True, diff staged changes (--cached).
        from_diff: Raw diff string to use instead of running git.
    """
    if from_diff is not None:
        return from_diff

    cmd = ["git", "-C", str(repo_path), "diff", "--unified=5"]

    if staged:
        cmd.append("--cached")

    if since:
        cmd.append(since)

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git diff failed: {result.stderr.strip()}")

    return result.stdout


def get_changed_files_from_git(
    repo_path: Path,
    since: str | None = None,
    staged: bool = False,
) -> list[str]:
    """Get just the list of changed file paths."""
    if since:
        cmd = ["git", "-C", str(repo_path), "diff", "--name-only", since]
    elif staged:
        cmd = ["git", "-C", str(repo_path), "diff", "--name-only", "--cached"]
    else:
        cmd = ["git", "-C", str(repo_path), "diff", "--name-only"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git diff --name-only failed: {result.stderr.strip()}")

    return [f for f in result.stdout.strip().splitlines() if f]
