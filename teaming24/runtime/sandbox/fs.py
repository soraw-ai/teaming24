"""File System Operations.

Secure file operations within sandbox workspace boundaries.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from teaming24.runtime.types import FileAccessError, FileInfo, FileType, RuntimeConfig
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class FileMatch:
    """Search match result."""
    path: str
    line_number: int
    line: str
    match: str


class FileSystem:
    """File system operations within sandbox workspace."""

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._workspace = config.workspace
        self._ensure_workspace()

    def _ensure_workspace(self):
        if not self._workspace.exists():
            self._workspace.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, path: str) -> Path:
        """Resolve path within workspace, with security checks."""
        p = Path(path)

        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self._workspace / p).resolve()

        workspace_resolved = self._workspace.resolve()
        if not str(resolved).startswith(str(workspace_resolved)):
            allowed = any(
                str(resolved).startswith(ap)
                for ap in self.config.allowed_paths
            )
            if not allowed:
                raise FileAccessError(f"Access denied: {path} is outside workspace")

        return resolved

    def read(
        self,
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        encoding: str = "utf-8",
    ) -> str:
        """Read file content."""
        file_path = self._resolve_path(path)

        if not file_path.exists():
            raise FileAccessError(f"File not found: {path}")
        if not file_path.is_file():
            raise FileAccessError(f"Not a file: {path}")

        content = file_path.read_text(encoding=encoding)

        if start_line is not None or end_line is not None:
            lines = content.splitlines(keepends=True)
            start = start_line or 0
            end = end_line or len(lines)
            content = "".join(lines[start:end])

        return content

    def read_bytes(self, path: str) -> bytes:
        """Read file as binary."""
        file_path = self._resolve_path(path)

        if not file_path.exists():
            raise FileAccessError(f"File not found: {path}")

        return file_path.read_bytes()

    def write(
        self,
        path: str,
        content: str,
        append: bool = False,
        encoding: str = "utf-8",
        create_dirs: bool = True,
    ) -> int:
        """Write content to file."""
        file_path = self._resolve_path(path)

        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        mode = "a" if append else "w"
        with open(file_path, mode, encoding=encoding) as f:
            written = f.write(content)

        return written

    def write_bytes(self, path: str, content: bytes, create_dirs: bool = True) -> int:
        """Write binary content to file."""
        file_path = self._resolve_path(path)

        if create_dirs:
            file_path.parent.mkdir(parents=True, exist_ok=True)

        return file_path.write_bytes(content)

    def delete(self, path: str, recursive: bool = False) -> bool:
        """Delete file or directory."""
        file_path = self._resolve_path(path)

        if not file_path.exists():
            return False

        if file_path.is_dir():
            if recursive:
                shutil.rmtree(file_path)
            else:
                file_path.rmdir()
        else:
            file_path.unlink()

        return True

    def copy(self, src: str, dst: str) -> str:
        """Copy file or directory."""
        src_path = self._resolve_path(src)
        dst_path = self._resolve_path(dst)

        if not src_path.exists():
            raise FileAccessError(f"Source not found: {src}")

        dst_path.parent.mkdir(parents=True, exist_ok=True)

        if src_path.is_dir():
            shutil.copytree(src_path, dst_path)
        else:
            shutil.copy2(src_path, dst_path)

        return str(dst_path)

    def move(self, src: str, dst: str) -> str:
        """Move file or directory."""
        src_path = self._resolve_path(src)
        dst_path = self._resolve_path(dst)

        if not src_path.exists():
            raise FileAccessError(f"Source not found: {src}")

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))

        return str(dst_path)

    def mkdir(self, path: str, parents: bool = True) -> str:
        """Create directory."""
        dir_path = self._resolve_path(path)
        dir_path.mkdir(parents=parents, exist_ok=True)
        return str(dir_path)

    def exists(self, path: str) -> bool:
        """Check if path exists."""
        try:
            return self._resolve_path(path).exists()
        except FileAccessError as exc:
            logger.debug("FileSystem.exists denied for %s: %s", path, exc)
            return False

    def is_file(self, path: str) -> bool:
        try:
            return self._resolve_path(path).is_file()
        except FileAccessError as exc:
            logger.debug("FileSystem.is_file denied for %s: %s", path, exc)
            return False

    def is_dir(self, path: str) -> bool:
        try:
            return self._resolve_path(path).is_dir()
        except FileAccessError as exc:
            logger.debug("FileSystem.is_dir denied for %s: %s", path, exc)
            return False

    def list_dir(
        self,
        path: str = ".",
        recursive: bool = False,
        show_hidden: bool = False,
        file_types: list[str] | None = None,
        max_depth: int = 10,
    ) -> list[FileInfo]:
        """List directory contents."""
        dir_path = self._resolve_path(path)

        if not dir_path.exists():
            raise FileAccessError(f"Directory not found: {path}")
        if not dir_path.is_dir():
            raise FileAccessError(f"Not a directory: {path}")

        results = []

        def _scan(current: Path, depth: int):
            if depth > max_depth:
                return

            try:
                for item in current.iterdir():
                    if not show_hidden and item.name.startswith("."):
                        continue

                    if file_types and item.is_file():
                        if not any(item.suffix == ext for ext in file_types):
                            continue

                    try:
                        results.append(FileInfo.from_path(item))
                    except OSError as exc:
                        logger.debug("Failed to stat/list item %s: %s", item, exc)
                        continue

                    if recursive and item.is_dir():
                        _scan(item, depth + 1)
            except PermissionError as e:
                logger.debug(f"Permission denied listing directory {current}: {e}")

        _scan(dir_path, 0)
        return sorted(results, key=lambda x: (x.type != FileType.DIRECTORY, x.name.lower()))

    def find(self, pattern: str, path: str = ".", max_results: int = 100) -> list[str]:
        """Find files matching glob pattern."""
        dir_path = self._resolve_path(path)

        if not dir_path.exists():
            return []

        results = []
        for p in dir_path.rglob(pattern):
            if len(results) >= max_results:
                break
            results.append(str(p.relative_to(dir_path)))

        return results

    def search(
        self,
        pattern: str,
        path: str,
        regex: bool = True,
        max_results: int = 100,
    ) -> list[FileMatch]:
        """Search for pattern in file content."""
        file_path = self._resolve_path(path)

        if not file_path.exists():
            raise FileAccessError(f"File not found: {path}")

        results = []

        if regex:
            compiled = re.compile(pattern)

        try:
            content = file_path.read_text()
            for i, line in enumerate(content.splitlines(), 1):
                if regex:
                    match = compiled.search(line)
                    if match:
                        results.append(FileMatch(
                            path=str(file_path),
                            line_number=i,
                            line=line,
                            match=match.group(0),
                        ))
                else:
                    if pattern in line:
                        results.append(FileMatch(
                            path=str(file_path),
                            line_number=i,
                            line=line,
                            match=pattern,
                        ))

                if len(results) >= max_results:
                    break
        except UnicodeDecodeError as e:
            logger.debug(f"Skipping binary file during search {file_path}: {e}")

        return results

    def replace(
        self,
        path: str,
        old: str,
        new: str,
        regex: bool = False,
        count: int = 0,
    ) -> int:
        """Replace text in file."""
        file_path = self._resolve_path(path)

        if not file_path.exists():
            raise FileAccessError(f"File not found: {path}")

        content = file_path.read_text()

        if regex:
            if count > 0:
                new_content = re.sub(old, new, content, count=count)
            else:
                new_content = re.sub(old, new, content)
            replacements = len(re.findall(old, content))
        else:
            if count > 0:
                new_content = content.replace(old, new, count)
            else:
                new_content = content.replace(old, new)
            replacements = content.count(old)

        if new_content != content:
            file_path.write_text(new_content)

        return min(replacements, count) if count > 0 else replacements

    @property
    def workspace(self) -> str:
        return str(self._workspace)


__all__ = ["FileSystem", "FileMatch"]
