"""E2BSandbox — wraps an E2B SDK Sandbox instance to implement the harness Sandbox ABC.

All methods are synchronous, matching the Sandbox ABC contract and the E2B
sync SDK (httpx.Client under the hood).
"""

from __future__ import annotations

import logging
import shlex
from typing import TYPE_CHECKING

from deerflow.sandbox.sandbox import Sandbox
from crab_platform.sandbox.path_mapping import E2BPathMapping

if TYPE_CHECKING:
    from e2b import Sandbox as E2BSdkSandbox

logger = logging.getLogger(__name__)

# Command execution timeout (seconds).
_COMMAND_TIMEOUT = 300


class E2BSandbox(Sandbox):
    """Sandbox implementation backed by an E2B cloud VM.

    Wraps an already-connected ``e2b.Sandbox`` instance and delegates file and
    command operations to the E2B SDK.
    """

    def __init__(
        self,
        id: str,
        e2b_sandbox: E2BSdkSandbox,
        path_mapping: E2BPathMapping | None = None,
    ) -> None:
        """
        Args:
            id: E2B sandbox ID (also persisted in PG ``threads.sandbox_id``).
            e2b_sandbox: A connected ``e2b.Sandbox`` instance.
        """
        super().__init__(id)
        self._e2b = e2b_sandbox
        self._path_mapping = path_mapping or E2BPathMapping()

    @property
    def e2b_sandbox(self) -> E2BSdkSandbox:
        """Access the underlying E2B SDK sandbox (for advanced operations)."""
        return self._e2b

    # -- Sandbox ABC --------------------------------------------------------

    def execute_command(self, command: str) -> str:
        try:
            mapped_command = self._path_mapping.map_text(command)
            if self._path_mapping.working_directory:
                mapped_command = (
                    f"cd {shlex.quote(self._path_mapping.working_directory)} && {mapped_command}"
                )
            result = self._e2b.commands.run(mapped_command, timeout=_COMMAND_TIMEOUT)
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            output = stdout + stderr
            output = self._path_mapping.unmap_text(output)
            return output if output else "(no output)"
        except Exception as e:
            logger.error("E2B execute_command failed: %s", e)
            return f"Error: {e}"

    def read_file(self, path: str) -> str:
        try:
            content = self._e2b.files.read(self._path_mapping.map_path(path))
            if isinstance(content, bytes):
                return self._path_mapping.unmap_text(content.decode("utf-8", errors="replace"))
            return self._path_mapping.unmap_text(content)
        except Exception as e:
            logger.error("E2B read_file failed for %s: %s", path, e)
            raise

    def read_bytes(self, path: str) -> bytes:
        """Read raw bytes from a mapped sandbox path."""
        content = self._e2b.files.read(self._path_mapping.map_path(path))
        if isinstance(content, bytes):
            return content
        return content.encode("utf-8")

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        try:
            result = self._e2b.commands.run(
                f"find {shlex.quote(self._path_mapping.map_path(path))} -maxdepth {int(max_depth)} \\( -type f -o -type d \\) 2>/dev/null | head -500",
                timeout=30,
            )
            output = (result.stdout or "").strip()
            if not output:
                return []
            return [
                self._path_mapping.unmap_path(line.strip())
                for line in output.split("\n")
                if line.strip()
            ]
        except Exception as e:
            logger.error("E2B list_dir failed for %s: %s", path, e)
            return []

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        try:
            mapped_path = self._path_mapping.map_path(path)
            if append:
                try:
                    existing = self._e2b.files.read(mapped_path)
                    if isinstance(existing, bytes):
                        existing = existing.decode("utf-8", errors="replace")
                except Exception:
                    existing = ""
                content = existing + content
            self._e2b.files.write(mapped_path, content)
        except Exception as e:
            logger.error("E2B write_file failed for %s: %s", path, e)
            raise

    def update_file(self, path: str, content: bytes) -> None:
        try:
            self._e2b.files.write(self._path_mapping.map_path(path), content)
        except Exception as e:
            logger.error("E2B update_file failed for %s: %s", path, e)
            raise
