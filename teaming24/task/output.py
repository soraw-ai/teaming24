"""
Task Output Manager - Saves task outputs to organized folders.

All task results — local and remote — are persisted under a unified
output directory configured in ``teaming24.yaml → output.base_dir``.

Directory structure per task:
  {base_dir}/{task_id}/
    manifest.json    — metadata, file list, timing, remote AN info
    result.txt       — raw aggregated result (local + all remote)
    local/           — code files extracted from local Coordinator execution
    remote/          — results received back from remote ANs
      {an_name}/     — one subfolder per remote AN that participated
        result.txt   — raw result text from that AN
        *.py / ...   — extracted code files

Features:
- Extracts code blocks from task results
- Saves files to task-specific directories (local/ and remote/ subfolders)
- Generates run instructions based on file types
- Provides summary with paths and execution commands
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class OutputFile:
    """Represents a saved output file."""
    filename: str
    filepath: str
    language: str
    size: int
    run_command: str | None = None
    description: str = ""


@dataclass
class TaskOutput:
    """Represents the complete output of a task."""
    task_id: str
    task_name: str
    output_dir: str
    files: list[OutputFile] = field(default_factory=list)
    summary: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "output_dir": self.output_dir,
            "files": [
                {
                    "filename": f.filename,
                    "filepath": f.filepath,
                    "language": f.language,
                    "size": f.size,
                    "run_command": f.run_command,
                    "description": f.description,
                }
                for f in self.files
            ],
            "summary": self.summary,
            "created_at": self.created_at,
        }


# Language to file extension mapping
LANGUAGE_EXTENSIONS = {
    "python": ".py",
    "py": ".py",
    "javascript": ".js",
    "js": ".js",
    "typescript": ".ts",
    "ts": ".ts",
    "html": ".html",
    "css": ".css",
    "json": ".json",
    "yaml": ".yaml",
    "yml": ".yml",
    "markdown": ".md",
    "md": ".md",
    "bash": ".sh",
    "shell": ".sh",
    "sh": ".sh",
    "sql": ".sql",
    "rust": ".rs",
    "go": ".go",
    "java": ".java",
    "cpp": ".cpp",
    "c++": ".cpp",
    "c": ".c",
    "ruby": ".rb",
    "php": ".php",
    "swift": ".swift",
    "kotlin": ".kt",
    "scala": ".scala",
    "r": ".r",
    "xml": ".xml",
    "toml": ".toml",
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "text": ".txt",
    "txt": ".txt",
}

# Extensions to include from workspace (agent-produced outputs)
WORKSPACE_OUTPUT_EXTENSIONS = {".json", ".png", ".jpg", ".jpeg", ".svg", ".csv", ".html", ".txt"}

# Run commands by language/extension
RUN_COMMANDS = {
    ".py": "python {file}",
    ".js": "node {file}",
    ".ts": "npx ts-node {file}",
    ".html": "open {file}  # or: python -m http.server",
    ".sh": "bash {file}",
    ".go": "go run {file}",
    ".rs": "cargo run  # or: rustc {file} && ./{name}",
    ".java": "javac {file} && java {name}",
    ".rb": "ruby {file}",
    ".php": "php {file}",
}


class TaskOutputManager:
    """
    Manages task output storage and organization.

    Directory structure:
    {base_dir}/
    ├── {task_id}/
    │   ├── manifest.json     # Task metadata and file list
    │   ├── result.txt        # Raw aggregated result
    │   ├── local/            # Code files extracted from local execution
    │   │   ├── main.py
    │   │   └── ...
    │   └── remote/           # Results from remote ANs
    │       ├── {an_name}/
    │       │   ├── result.txt
    │       │   └── ...
    │       └── ...
    """

    def __init__(self, base_dir: str = None):
        """Initialize with base output directory.

        If ``base_dir`` is None, the value is read from
        ``config → output.base_dir`` (default ``~/.teaming24/outputs``).
        """
        if base_dir is None:
            try:
                from teaming24.config import get_config
                cfg = get_config()
                base_dir = cfg.output.base_dir
            except Exception as e:
                logger.debug("Failed to load output base_dir from config: %s", e)
                base_dir = "~/.teaming24/outputs"

        self.base_dir = Path(os.path.expanduser(base_dir))
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Task outputs will be saved to: {self.base_dir}")

    def save_task_output(
        self,
        task_id: str,
        task_name: str,
        result: str,
        duration: float = 0,
        tokens: int = 0,
    ) -> TaskOutput:
        """
        Save local task output to ``{base_dir}/{task_id}/local/``.

        Also saves the aggregated raw result to ``{base_dir}/{task_id}/result.txt``.

        Args:
            task_id: Unique task identifier
            task_name: Human-readable task name/description
            result: Raw result string from task execution
            duration: Task duration in seconds
            tokens: Total tokens used

        Returns:
            TaskOutput with paths and run instructions
        """
        # Create task directory
        task_dir = self.base_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        # Local subfolder for extracted code files
        local_dir = task_dir / "local"
        local_dir.mkdir(parents=True, exist_ok=True)

        # Initialize output
        output = TaskOutput(
            task_id=task_id,
            task_name=task_name[:100],
            output_dir=str(task_dir),
        )

        # Save raw result to task root
        result_path = task_dir / "result.txt"
        result_path.write_text(result, encoding="utf-8")

        # Extract and save code blocks into local/
        code_blocks = self._extract_code_blocks(result)

        if code_blocks:
            for i, (language, code, filename_hint) in enumerate(code_blocks):
                saved_file = self._save_code_file(
                    local_dir, language, code, filename_hint, i
                )
                if saved_file:
                    output.files.append(saved_file)

        # Include workspace files (agent-produced outputs via file_write)
        workspace_dir = task_dir / "workspace"
        if workspace_dir.exists():
            for wf in self._scan_workspace_files(workspace_dir):
                output.files.append(wf)

        # Generate summary
        output.summary = self._generate_summary(output, duration, tokens)

        # Save manifest
        self._write_manifest(task_dir, output)

        logger.info(f"Task output saved: {task_dir} ({len(output.files)} files)")

        return output

    def save_remote_result(
        self,
        task_id: str,
        an_name: str,
        result_text: str,
        ip: str = None,
        port: int = None,
        an_id: str = None,
    ) -> str | None:
        """
        Save a result received from a remote AN into
        ``{base_dir}/{task_id}/remote/{folder_name}/``.

        The folder name includes the AN name, IP:port, and an_id for
        easy identification:  ``{an_name}_{ip}_{port}_{an_id}``

        Args:
            task_id: Parent task identifier.
            an_name: Display name of the remote AN.
            result_text: Raw result text from the remote AN.
            ip: IP address of the remote AN.
            port: Port number of the remote AN.
            an_id: Canonical AN identifier.

        Returns:
            Path to the remote result directory, or None on error.
        """
        try:
            from teaming24.config import get_config
            cfg = get_config()
            if not cfg.output.save_remote_results:
                return None
        except Exception as e:
            logger.debug(f"Failed to check save_remote_results config: {e}")

        try:
            # Build descriptive folder name: {an_name}_{ip}_{port}_{an_id}
            parts = [an_name]
            if ip:
                parts.append(ip)
            if port:
                parts.append(str(port))
            if an_id:
                # Use last 6 chars of an_id for brevity
                parts.append(an_id[-6:] if len(an_id) > 6 else an_id)
            folder_name = "_".join(parts)

            safe_name = self._sanitize_filename(
                folder_name.replace(":", "_").replace("/", "_").replace(" ", "_")
            )
            task_dir = self.base_dir / task_id
            remote_dir = task_dir / "remote" / safe_name
            remote_dir.mkdir(parents=True, exist_ok=True)

            # Save raw remote result text
            (remote_dir / "result.txt").write_text(
                result_text, encoding="utf-8"
            )

            # Save metadata about the remote AN
            meta = {
                "an_name": an_name,
                "ip": ip,
                "port": port,
                "an_id": an_id,
            }
            (remote_dir / "an_info.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            # Extract and save any code blocks from the remote result
            code_blocks = self._extract_code_blocks(result_text)
            for i, (language, code, filename_hint) in enumerate(code_blocks):
                self._save_code_file(
                    remote_dir, language, code, filename_hint, i
                )

            logger.info(
                f"Remote result saved: {remote_dir} "
                f"({len(code_blocks)} code blocks)"
            )
            return str(remote_dir)
        except Exception as e:
            logger.warning(f"Failed to save remote result: {e}")
            return None

    def save_aggregated_result(
        self,
        task_id: str,
        task_name: str,
        aggregated_text: str,
        remote_results: list[dict] = None,
        duration: float = 0,
        tokens: int = 0,
    ) -> TaskOutput:
        """
        Save the final aggregated result (local + remote) for a task.

        This is the top-level entry point called after Organizer collects
        all results.  It:
          1. Saves aggregated text to ``{task_id}/result.txt``
          2. Extracts code files into ``{task_id}/local/``
          3. Saves each remote AN result into ``{task_id}/remote/{an}/``
          4. Writes ``manifest.json``

        Args:
            task_id: Unique task identifier.
            task_name: Human-readable task name.
            aggregated_text: Combined result string.
            remote_results: List of dicts from ``_aggregate_results``,
                            each with keys: assigned_to, result, status.
            duration: Total task duration in seconds.
            tokens: Total tokens consumed.

        Returns:
            TaskOutput with all paths.
        """
        # Save local portion (also writes result.txt)
        output = self.save_task_output(
            task_id, task_name, aggregated_text, duration, tokens
        )

        # Save individual remote results
        if remote_results:
            for r in remote_results:
                an_name = r.get("assigned_to", "unknown_an")
                r_text = r.get("result", "")
                if r_text:
                    self.save_remote_result(
                        task_id, an_name, r_text,
                        ip=r.get("ip"),
                        port=r.get("port"),
                        an_id=r.get("node_id"),
                    )

        # Re-write manifest with remote info included
        self._write_manifest(
            Path(output.output_dir), output, remote_results
        )

        return output

    def _write_manifest(
        self,
        task_dir: Path,
        output: TaskOutput,
        remote_results: list[dict] = None,
    ):
        """Write or update manifest.json in the task directory."""
        data = output.to_dict()
        if remote_results:
            from teaming24.config import get_config
            preview_max = get_config().output.result_preview_max_chars
            data["remote_results"] = [
                {
                    "assigned_to": r.get("assigned_to", ""),
                    "status": r.get("status", ""),
                    "result_preview": str(r.get("result", ""))[:preview_max],
                }
                for r in remote_results
            ]
        manifest_path = task_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _extract_code_blocks(self, text: str) -> list[tuple[str, str, str | None]]:
        """
        Extract code blocks from markdown-formatted text.

        Returns:
            List of (language, code, filename_hint) tuples
        """
        blocks = []

        # Pattern for fenced code blocks: ```language or ```language:filename
        pattern = r'```(\w+)?(?::([^\n]+))?\n(.*?)```'

        matches = re.findall(pattern, text, re.DOTALL)

        for lang, filename, code in matches:
            language = (lang or "text").lower().strip()
            filename_hint = filename.strip() if filename else None
            code_content = code.strip()

            if code_content:  # Skip empty blocks
                blocks.append((language, code_content, filename_hint))

        # Also try to detect inline code that looks like full files
        # e.g., patterns that suggest a complete file
        if not blocks:
            # Check for common file patterns without code fences
            file_patterns = [
                (r'<!DOCTYPE html>.*?</html>', 'html'),
                (r'<html.*?>.*?</html>', 'html'),
                (r'#!/usr/bin/env python.*?(?=\n\n\n|\Z)', 'python'),
                (r'#!/bin/bash.*?(?=\n\n\n|\Z)', 'bash'),
            ]
            for pattern, lang in file_patterns:
                match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
                if match:
                    blocks.append((lang, match.group(0).strip(), None))

        return blocks

    def _save_code_file(
        self,
        task_dir: Path,
        language: str,
        code: str,
        filename_hint: str | None,
        index: int,
    ) -> OutputFile | None:
        """Save a code block to a file."""
        try:
            # Determine filename
            if filename_hint:
                filename = filename_hint
            else:
                ext = LANGUAGE_EXTENSIONS.get(language, ".txt")
                if ext.startswith("."):
                    # Use descriptive name based on index
                    base_name = "main" if index == 0 else f"file_{index}"
                    filename = f"{base_name}{ext}"
                else:
                    filename = ext  # For Dockerfile, Makefile, etc.

            # Ensure safe filename
            filename = self._sanitize_filename(filename)

            # Save file
            filepath = task_dir / filename
            filepath.write_text(code, encoding="utf-8")

            # Determine run command
            ext = filepath.suffix.lower()
            run_cmd = RUN_COMMANDS.get(ext)
            if run_cmd:
                name_no_ext = filepath.stem
                run_cmd = run_cmd.format(file=filename, name=name_no_ext)

            return OutputFile(
                filename=filename,
                filepath=str(filepath),
                language=language,
                size=len(code),
                run_command=run_cmd,
                description=f"{language.title()} source file",
            )

        except Exception as e:
            logger.warning(f"Failed to save code file: {e}")
            return None

    def list_workspace_filenames(self, task_id: str) -> list[str]:
        """List workspace output filenames for a task (for synthesis prompt)."""
        if not task_id:
            return []
        workspace_dir = self.base_dir / task_id / "workspace"
        if not workspace_dir.exists():
            return []
        files = self._scan_workspace_files(workspace_dir)
        return [f.filename for f in files]

    def _scan_workspace_files(self, workspace_dir: Path) -> list[OutputFile]:
        """Scan workspace for agent-produced output files and add to output.files.

        Files in subdirs use flattened names (e.g. outputs/chart.png -> outputs_chart.png)
        so the file serve endpoint can resolve them.
        """
        out: list[OutputFile] = []
        try:
            for fp in workspace_dir.rglob("*"):
                if not fp.is_file():
                    continue
                if fp.suffix.lower() not in WORKSPACE_OUTPUT_EXTENSIONS:
                    continue
                try:
                    rel = fp.relative_to(workspace_dir)
                    # Flatten path for URL: outputs/chart.png -> outputs_chart.png
                    flat_name = str(rel).replace("/", "_").replace("\\", "_")
                    flat_name = self._sanitize_filename(flat_name)
                    size = fp.stat().st_size
                    out.append(
                        OutputFile(
                            filename=flat_name,
                            filepath=str(fp.resolve()),
                            language="",
                            size=size,
                            description="Workspace output",
                        )
                    )
                except (ValueError, OSError) as e:
                    logger.debug(f"Skip workspace file {fp}: {e}")
        except Exception as e:
            logger.warning(f"Failed to scan workspace {workspace_dir}: {e}")
        return out

    def _sanitize_filename(self, filename: str) -> str:
        """Sanitize filename to remove invalid characters."""
        # Remove path separators and null bytes
        filename = filename.replace("/", "_").replace("\\", "_").replace("\x00", "")
        # Remove other problematic characters
        filename = re.sub(r'[<>:"|?*]', "_", filename)
        # Limit length
        from teaming24.config import get_config
        max_chars = get_config().output.filename_max_chars
        if len(filename) > max_chars:
            name, ext = os.path.splitext(filename)
            filename = name[:max_chars - len(ext)] + ext
        return filename or "output.txt"

    def _generate_summary(
        self,
        output: TaskOutput,
        duration: float,
        tokens: int,
    ) -> str:
        """Generate a formatted summary with paths and run instructions."""
        lines = [
            "",
            "=" * 70,
            "📁 TASK OUTPUT SAVED",
            "=" * 70,
            "",
            "📂 Output Directory:",
            f"   {output.output_dir}",
            "",
        ]

        if output.files:
            lines.append("📄 Generated Files:")
            for f in output.files:
                lines.append(f"   • {f.filename} ({f.language}, {f.size} bytes)")
                if f.run_command:
                    lines.append(f"     Run: cd {output.output_dir} && {f.run_command}")
            lines.append("")

        # Add quick start instructions
        if output.files:
            main_file = output.files[0]
            lines.extend([
                "🚀 Quick Start:",
                f"   cd {output.output_dir}",
            ])
            if main_file.run_command:
                lines.append(f"   {main_file.run_command}")
            lines.append("")

        lines.extend([
            f"⏱️  Duration: {duration:.1f}s | Tokens: {tokens}",
            "=" * 70,
            "",
        ])

        return "\n".join(lines)

    def get_task_output(self, task_id: str) -> TaskOutput | None:
        """Load task output from disk."""
        task_dir = self.base_dir / task_id
        manifest_path = task_dir / "manifest.json"

        if not manifest_path.exists():
            return None

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            output = TaskOutput(
                task_id=data["task_id"],
                task_name=data["task_name"],
                output_dir=data["output_dir"],
                summary=data.get("summary", ""),
                created_at=data.get("created_at", 0),
            )
            for f in data.get("files", []):
                output.files.append(OutputFile(**f))
            return output
        except Exception as e:
            logger.warning(f"Failed to load task output: {e}")
            return None

    def list_outputs(self, limit: int = 20) -> list[TaskOutput]:
        """List recent task outputs."""
        outputs = []

        if not self.base_dir.exists():
            return outputs

        # Get all task directories sorted by modification time
        task_dirs = sorted(
            self.base_dir.iterdir(),
            key=lambda p: p.stat().st_mtime if p.is_dir() else 0,
            reverse=True
        )

        for task_dir in task_dirs[:limit]:
            if task_dir.is_dir():
                output = self.get_task_output(task_dir.name)
                if output:
                    outputs.append(output)

        return outputs

    def cleanup_old_outputs(self, max_age_days: int = None) -> int:
        """Remove outputs older than specified days.

        If ``max_age_days`` is None, reads from
        ``config → output.cleanup_max_age_days``.  Set to 0 to skip.
        """
        import shutil

        if max_age_days is None:
            try:
                from teaming24.config import get_config
                max_age_days = get_config().output.cleanup_max_age_days
            except Exception as e:
                logger.debug("Failed to load cleanup_max_age_days from config: %s", e)
                max_age_days = 30

        if max_age_days <= 0:
            return 0

        cutoff_time = time.time() - (max_age_days * 24 * 60 * 60)
        removed = 0

        for task_dir in self.base_dir.iterdir():
            if not task_dir.is_dir():
                continue
            # Only delete dirs that look like valid task outputs (have manifest.json)
            # Avoids data errors from deleting wrong paths
            manifest_path = task_dir / "manifest.json"
            if not manifest_path.exists():
                logger.debug(f"Skipping non-task dir (no manifest): {task_dir.name}")
                continue
            try:
                # Resolve to absolute path to avoid symlink issues
                abs_path = task_dir.resolve()
                if abs_path.stat().st_mtime < cutoff_time:
                    shutil.rmtree(abs_path)
                    removed += 1
                else:
                    logger.debug(f"Skipping recent output: {task_dir.name}")
            except Exception as e:
                logger.warning(f"Failed to remove old output {task_dir}: {e}")

        if removed:
            logger.info(f"Cleaned up {removed} old task outputs")

        return removed


# Global instance
_output_manager: TaskOutputManager | None = None


def get_output_manager() -> TaskOutputManager:
    """Get or create the global output manager instance.

    Reads ``output.base_dir`` from config on first call.
    """
    global _output_manager
    if _output_manager is None:
        _output_manager = TaskOutputManager()
    return _output_manager


def save_task_output(
    task_id: str,
    task_name: str,
    result: str,
    duration: float = 0,
    tokens: int = 0,
) -> TaskOutput:
    """Convenience function — saves local task output."""
    return get_output_manager().save_task_output(
        task_id, task_name, result, duration, tokens
    )


def save_aggregated_output(
    task_id: str,
    task_name: str,
    aggregated_text: str,
    remote_results: list[dict] = None,
    duration: float = 0,
    tokens: int = 0,
) -> TaskOutput:
    """Convenience function — saves aggregated output (local + remote)."""
    return get_output_manager().save_aggregated_result(
        task_id, task_name, aggregated_text,
        remote_results, duration, tokens,
    )
