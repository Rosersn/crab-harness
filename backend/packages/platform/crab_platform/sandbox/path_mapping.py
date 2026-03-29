"""Virtual-to-actual path mapping for the E2B sandbox backend."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from deerflow.config.paths import VIRTUAL_PATH_PREFIX

_DEFAULT_VIRTUAL_SKILLS_ROOT = "/mnt/skills"
_DEFAULT_VIRTUAL_ACP_ROOT = "/mnt/acp-workspace"
_DEFAULT_ACTUAL_USER_DATA_ROOT = "/home/user/.deerflow/user-data"
_DEFAULT_ACTUAL_SKILLS_ROOT = "/home/user/.deerflow/skills"
_DEFAULT_ACTUAL_ACP_ROOT = "/home/user/.deerflow/acp-workspace"


def _replace_prefixes(text: str, replacements: list[tuple[str, str]]) -> str:
    result = text
    for source, target in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        pattern = re.compile(
            rf"(?<![:\\w]){re.escape(source)}(?=(?:/|$|[\\s\\\"'`;&|<>()]))"
        )
        result = pattern.sub(target, result)
    return result


def _join(base: str, relative: str) -> str:
    return str(PurePosixPath(base.rstrip("/")) / relative.lstrip("/"))


@dataclass(frozen=True)
class E2BPathMapping:
    """Describes how DeerFlow virtual sandbox paths map into E2B paths."""

    virtual_user_data_root: str = VIRTUAL_PATH_PREFIX
    actual_user_data_root: str = _DEFAULT_ACTUAL_USER_DATA_ROOT
    virtual_skills_root: str = _DEFAULT_VIRTUAL_SKILLS_ROOT
    actual_skills_root: str = _DEFAULT_ACTUAL_SKILLS_ROOT
    virtual_acp_workspace_root: str = _DEFAULT_VIRTUAL_ACP_ROOT
    actual_acp_workspace_root: str = _DEFAULT_ACTUAL_ACP_ROOT
    working_directory: str | None = None

    def __post_init__(self) -> None:
        if self.working_directory is None:
            object.__setattr__(self, "working_directory", _join(self.actual_user_data_root, "workspace"))

    @property
    def actual_workspace_dir(self) -> str:
        return _join(self.actual_user_data_root, "workspace")

    @property
    def actual_uploads_dir(self) -> str:
        return _join(self.actual_user_data_root, "uploads")

    @property
    def actual_outputs_dir(self) -> str:
        return _join(self.actual_user_data_root, "outputs")

    @property
    def actual_custom_skills_dir(self) -> str:
        return _join(self.actual_skills_root, "custom")

    @property
    def actual_replacements(self) -> list[tuple[str, str]]:
        return [
            (self.actual_user_data_root, self.virtual_user_data_root),
            (self.actual_skills_root, self.virtual_skills_root),
            (self.actual_acp_workspace_root, self.virtual_acp_workspace_root),
        ]

    @property
    def virtual_replacements(self) -> list[tuple[str, str]]:
        return [
            (self.virtual_user_data_root, self.actual_user_data_root),
            (self.virtual_skills_root, self.actual_skills_root),
            (self.virtual_acp_workspace_root, self.actual_acp_workspace_root),
        ]

    def map_path(self, path: str) -> str:
        for virtual_root, actual_root in sorted(self.virtual_replacements, key=lambda item: len(item[0]), reverse=True):
            if path == virtual_root:
                return actual_root
            if path.startswith(f"{virtual_root}/"):
                return _join(actual_root, path[len(virtual_root) :])
        return path

    def unmap_path(self, path: str) -> str:
        for actual_root, virtual_root in sorted(self.actual_replacements, key=lambda item: len(item[0]), reverse=True):
            if path == actual_root:
                return virtual_root
            if path.startswith(f"{actual_root}/"):
                return _join(virtual_root, path[len(actual_root) :])
        return path

    def map_text(self, text: str) -> str:
        return _replace_prefixes(text, self.virtual_replacements)

    def unmap_text(self, text: str) -> str:
        return _replace_prefixes(text, self.actual_replacements)


def build_e2b_path_mapping() -> E2BPathMapping:
    """Build the E2B path mapping from the active app config."""
    from deerflow.config import get_app_config

    config = get_app_config()
    sandbox_config = config.sandbox

    actual_user_data_root = getattr(sandbox_config, "e2b_user_data_dir", None) or _DEFAULT_ACTUAL_USER_DATA_ROOT
    actual_skills_root = getattr(sandbox_config, "e2b_skills_dir", None) or _DEFAULT_ACTUAL_SKILLS_ROOT
    actual_acp_root = getattr(sandbox_config, "e2b_acp_workspace_dir", None) or _DEFAULT_ACTUAL_ACP_ROOT
    working_directory = getattr(sandbox_config, "e2b_working_directory", None) or _join(actual_user_data_root, "workspace")

    virtual_skills_root = getattr(config.skills, "container_path", None)
    if not isinstance(virtual_skills_root, str) or not virtual_skills_root:
        virtual_skills_root = _DEFAULT_VIRTUAL_SKILLS_ROOT

    return E2BPathMapping(
        virtual_user_data_root=VIRTUAL_PATH_PREFIX,
        actual_user_data_root=actual_user_data_root,
        virtual_skills_root=virtual_skills_root,
        actual_skills_root=actual_skills_root,
        virtual_acp_workspace_root=_DEFAULT_VIRTUAL_ACP_ROOT,
        actual_acp_workspace_root=actual_acp_root,
        working_directory=working_directory,
    )
