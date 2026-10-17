"""Acceptance tests for linked instance propagation."""

from pathlib import Path

import pytest
import yaml

from copier import run_copy, run_recopy, run_update
from copier.errors import UserMessageError

from .helpers import build_file_tree, git_save


def _template(path: Path, config: str, output: str, content: str) -> None:
    """Build a small template with a generated answers file."""
    build_file_tree(
        {
            path / "copier.yml": config,
            path / "{{ _copier_conf.answers_file }}.jinja": (
                "{{ _copier_answers | to_nice_yaml }}"
            ),
            path / output: content,
        }
    )


@pytest.mark.parametrize("selected", ["one.yml", "two.yml"])
def test_child_copy_and_recopy_rerender_selected_parent(
    tmp_path: Path, selected: str
) -> None:
    """A child changes only its selected same-repository parent's output."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\nname: parent\n",
        "parents-{{ name }}.txt.jinja",
        "{{ _children | map(attribute='name') | join(',') }}",
    )
    _template(
        child,
        f"_parent_template: {parent}\nname: child\n",
        "child.txt.jinja",
        "{{ name }}",
    )
    for answers_file, name in (("one.yml", "one"), ("two.yml", "two")):
        run_copy(
            str(parent),
            dst,
            answers_file=answers_file,
            data={"name": name},
            defaults=True,
            quiet=True,
        )
    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file=selected,
        data={"name": "first"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    assert (dst / f"parents-{selected.removesuffix('.yml')}.txt").read_text() == "first"
    run_recopy(
        dst,
        answers_file="child.yml",
        data={"name": "second"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    assert (
        dst / f"parents-{selected.removesuffix('.yml')}.txt"
    ).read_text() == "second"
    other = "two" if selected == "one.yml" else "one"
    assert (dst / f"parents-{other}.txt").read_text() == ""


@pytest.mark.parametrize(
    ("changed", "expected_root", "expected_middle", "expected_leaf", "expected_side"),
    [
        ("root.yml", "new/middle", "new/leaf", "middle", "new"),
        ("middle.yml", "root/new", "root/leaf", "new", "root"),
        ("leaf.yml", "root/middle", "root/new", "middle", "root"),
    ],
)
def test_recopy_rerenders_chain_without_sibling(
    tmp_path: Path,
    changed: str,
    expected_root: str,
    expected_middle: str,
    expected_leaf: str,
    expected_side: str,
) -> None:
    """Recopy follows the affected path once and preserves the side branch."""
    root, middle, leaf, side, dst = (
        tmp_path / name for name in ("root", "middle", "leaf", "side", "dst")
    )
    _template(
        root,
        "_accept_children: true\nname: root\n",
        "root.txt.jinja",
        "{{ name }}/{{ _children[0].name if _children else '' }}",
    )
    _template(
        middle,
        f"_parent_template: {root}\n_accept_children: true\nname: middle\n",
        "middle.txt.jinja",
        "{{ _parent.name }}/{{ _children[0].name if _children else '' }}",
    )
    _template(
        leaf,
        f"_parent_template: {middle}\nname: leaf\n",
        "leaf.txt.jinja",
        "{{ _parent.name }}",
    )
    _template(
        side,
        f"_parent_template: {root}\nname: side\n",
        "side.txt.jinja",
        "{{ _parent.name }}",
    )
    run_copy(str(root), dst, answers_file="root.yml", defaults=True, quiet=True)
    run_copy(
        str(middle),
        dst,
        answers_file="middle.yml",
        parent_answers_file="root.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    run_copy(
        str(leaf),
        dst,
        answers_file="leaf.yml",
        parent_answers_file="middle.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    run_copy(
        str(side),
        dst,
        answers_file="side.yml",
        parent_answers_file="root.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    before = {
        name: yaml.safe_load((dst / name).read_text()).get("_commit")
        for name in ("root.yml", "middle.yml", "leaf.yml", "side.yml")
    }
    run_recopy(
        dst,
        answers_file=changed,
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    assert (dst / "root.txt").read_text() == expected_root
    assert (dst / "middle.txt").read_text() == expected_middle
    assert (dst / "leaf.txt").read_text() == expected_leaf
    assert (dst / "side.txt").read_text() == expected_side
    after = {
        name: yaml.safe_load((dst / name).read_text()).get("_commit") for name in before
    }
    assert after == before


def test_output_collision_fails_before_new_child_is_written(tmp_path: Path) -> None:
    """An attaching child cannot claim an output owned by its parent."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n", "shared.txt", "parent")
    _template(child, f"_parent_template: {parent}\n", "shared.txt", "child")
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    with pytest.raises(UserMessageError, match="shared.txt.*parent.yml.*child.yml"):
        run_copy(
            str(child),
            dst,
            answers_file="child.yml",
            parent_answers_file="parent.yml",
            defaults=True,
            quiet=True,
        )
    assert (dst / "shared.txt").read_text() == "parent"
    assert not (dst / "child.yml").exists()


def test_answers_file_collision_reports_existing_owner(tmp_path: Path) -> None:
    """A new instance cannot claim an existing parent's generated file."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n", "child.yml", "parent output")
    _template(child, f"_parent_template: {parent}\n", "child.txt", "child")
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)

    with pytest.raises(UserMessageError, match="child.yml.*parent.yml.*child.yml"):
        run_copy(
            str(child),
            dst,
            answers_file="child.yml",
            parent_answers_file="parent.yml",
            defaults=True,
            quiet=True,
        )
    assert (dst / "child.yml").read_text() == "parent output"


def test_dependent_rerenders_do_not_run_tasks(tmp_path: Path) -> None:
    """Only the requested instance executes its template tasks."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\n_tasks: ['echo parent >> tasks.log']\n",
        "parent.txt.jinja",
        "{{ _children | length }}",
    )
    _template(
        child,
        f"_parent_template: {parent}\n_tasks: ['echo child >> tasks.log']\n",
        "child.txt.jinja",
        "{{ _parent.keys() | list | length }}",
    )
    run_copy(
        str(parent),
        dst,
        answers_file="parent.yml",
        defaults=True,
        quiet=True,
        unsafe=True,
    )
    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file="parent.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
        unsafe=True,
    )
    run_recopy(
        dst,
        answers_file="child.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
        unsafe=True,
    )

    assert (dst / "tasks.log").read_text().splitlines() == ["parent", "child", "child"]


def test_update_child_rerenders_selected_parent_at_recorded_revision(
    tmp_path: Path,
) -> None:
    """A child update refreshes a parent without advancing its revision."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\n",
        "parent.txt.jinja",
        "{{ _children | map(attribute='name') | join(',') }}",
    )
    _template(
        child,
        f"_parent_template: {parent}\nname: old\n",
        "child.txt.jinja",
        "{{ name }}",
    )
    git_save(parent, tag="v1.0.0")
    git_save(child, tag="v1.0.0")
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file="parent.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    parent_commit = yaml.safe_load((dst / "parent.yml").read_text())["_commit"]
    git_save(dst)
    build_file_tree({child / "new.txt": "new version"})
    git_save(child, tag="v2.0.0")

    run_update(
        dst,
        answers_file="child.yml",
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )

    assert (dst / "child.txt").read_text() == "new"
    assert (dst / "parent.txt").read_text() == "new"
    assert yaml.safe_load((dst / "parent.yml").read_text())["_commit"] == parent_commit


def test_update_rejects_new_answer_output_collision_before_mutation(
    tmp_path: Path,
) -> None:
    """An updated filename cannot take a parent's output or change answers."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n", "parent.txt", "parent output")
    _template(
        child,
        f"_parent_template: {parent}\nname: old\n",
        "{{ name }}.txt.jinja",
        "child output",
    )
    git_save(parent, tag="v1.0.0")
    git_save(child, tag="v1.0.0")
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file="parent.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    git_save(dst)
    build_file_tree({child / "new.txt": "new version"})
    git_save(child, tag="v2.0.0")
    before = {path.name: path.read_bytes() for path in dst.iterdir() if path.is_file()}

    with pytest.raises(UserMessageError, match="parent.txt.*child.yml.*parent.yml"):
        run_update(
            dst,
            answers_file="child.yml",
            data={"name": "parent"},
            defaults=True,
            quiet=True,
            overwrite=True,
        )

    assert {path.name: path.read_bytes() for path in dst.iterdir() if path.is_file()} == before


def test_update_rejects_migrated_answer_output_collision(tmp_path: Path) -> None:
    """A migrated answer cannot cause the child to claim a parent's output."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n", "parent.txt", "parent output")
    _template(
        child,
        f"_parent_template: {parent}\nname: old\n",
        "{{ name }}.txt.jinja",
        "child output",
    )
    git_save(parent, tag="v1.0.0")
    git_save(child, tag="v1.0.0")
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file="parent.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    git_save(dst)
    build_file_tree(
        {
            child / "copier.yml": (
                f"_parent_template: {parent}\n"
                "name: old\n"
                "_migrations:\n"
                "  - command: [sed, -i, 's/name: old/name: parent/', child.yml]\n"
                "    when: \"{{ _stage == 'before' }}\"\n"
            ),
            child / "new.txt": "new version",
        }
    )
    git_save(child, tag="v2.0.0")

    with pytest.raises(UserMessageError, match="parent.txt.*child.yml.*parent.yml"):
        run_update(
            dst,
            answers_file="child.yml",
            defaults=True,
            quiet=True,
            overwrite=True,
            unsafe=True,
        )

    assert (dst / "parent.txt").read_text() == "parent output"
    assert (dst / "old.txt").read_text() == "child output"
    assert not (dst / "new.txt").exists()
    assert yaml.safe_load((dst / "child.yml").read_text())["name"] == "parent"
