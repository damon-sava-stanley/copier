"""Validate linked template instances stored in answers files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._template import Template
from ._user_data import load_answersfile_data
from ._vcs import get_repo
from .errors import UserMessageError


@dataclass
class _LinkedInstance:
    """One saved template instance."""

    path: Path
    answers: dict[str, Any]
    parent: Path | None
    children: tuple[Path, ...]


def _answers_path(root: Path, value: str | Path) -> Path:
    """Normalize an answers path confined to a destination."""
    path = Path(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {".", ".."} for part in path.parts)
        or not (root / path).resolve().is_relative_to(root.resolve())
    ):
        raise UserMessageError(f"Invalid answers-file path `{value}`")
    return path


def _source_identity(source: str) -> str:
    """Compare declared and saved repositories without their revisions."""
    repo = get_repo(source)
    if repo:
        repo_path = Path(repo).expanduser()
        if repo_path.is_absolute() or repo_path.exists():
            return str(repo_path.resolve())
        return str(repo)
    path = Path(source).expanduser()
    if path.is_absolute() or path.exists():
        return str(path.resolve())
    return source


class _LinkedGraph:
    """A validated component of reciprocal instance links."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.instances: dict[Path, _LinkedInstance] = {}

    def load(self, value: str | Path) -> _LinkedInstance:
        """Read a linked component and validate its saved metadata."""
        path = _answers_path(self.root, value)
        self._visit(path)
        self._validate()
        return self.instances[path]

    def _visit(self, path: Path) -> None:
        """Read one instance and recursively discover its links."""
        if path in self.instances:
            return
        if not (self.root / path).is_file():
            raise UserMessageError(f"Missing linked answers file `{path}`")
        answers = load_answersfile_data(self.root, path)
        if not isinstance(answers, dict) or not isinstance(
            answers.get("_src_path"), str
        ):
            raise UserMessageError(f"Invalid linked answers file `{path}`")
        parent_value = answers.get("_parent")
        children_value = answers.get("_children", [])
        if parent_value is not None and not isinstance(parent_value, str):
            raise UserMessageError(f"Invalid `_parent` in `{path}`")
        if not isinstance(children_value, list) or any(
            not isinstance(child, str) for child in children_value
        ):
            raise UserMessageError(f"Invalid `_children` in `{path}`")
        parent = _answers_path(self.root, parent_value) if parent_value else None
        children = tuple(_answers_path(self.root, child) for child in children_value)
        if len(children) != len(set(children)):
            raise UserMessageError(f"Repeated child answers file in `{path}`")
        self.instances[path] = _LinkedInstance(path, answers, parent, children)
        for linked in (*(() if parent is None else (parent,)), *children):
            self._visit(linked)

    def _validate(self) -> None:
        """Check reciprocal links, source declarations, and acyclicity."""
        checked: set[Path] = set()
        active: set[Path] = set()

        def visit(path: Path) -> None:
            if path in active:
                raise UserMessageError(f"Cycle in linked answers files at `{path}`")
            if path in checked:
                return
            active.add(path)
            instance = self.instances[path]
            if instance.parent is not None:
                parent = self.instances[instance.parent]
                self._validate_edge(instance, parent)
                visit(parent.path)
            for child_path in instance.children:
                child = self.instances[child_path]
                if child.parent != path:
                    raise UserMessageError(
                        f"Inconsistent links between `{path}` and `{child_path}`"
                    )
            active.remove(path)
            checked.add(path)

        for path in self.instances:
            visit(path)

    def _validate_edge(self, child: _LinkedInstance, parent: _LinkedInstance) -> None:
        """Check one reciprocal link against both template declarations."""
        if child.path not in parent.children:
            raise UserMessageError(
                f"Inconsistent links between `{child.path}` and `{parent.path}`"
            )
        with (
            _TemplateFor(parent) as parent_template,
            _TemplateFor(child) as child_template,
        ):
            if not parent_template.accept_children:
                raise UserMessageError(
                    f"Parent `{parent.path}` does not accept child `{child.path}`"
                )
            if not child_template.parent_template or _source_identity(
                child_template.parent_template
            ) != _source_identity(parent.answers["_src_path"]):
                raise UserMessageError(
                    f"Parent repository mismatch between `{child.path}` and `{parent.path}`"
                )


class _TemplateFor:
    """Temporarily open a saved instance's template."""

    def __init__(self, instance: _LinkedInstance) -> None:
        self.template = Template(
            url=instance.answers["_src_path"], ref=instance.answers.get("_commit")
        )

    def __enter__(self) -> Template:
        return self.template

    def __exit__(self, *_args: object) -> None:
        self.template._cleanup()
