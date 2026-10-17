"""Acceptance tests for merging linked template update output."""

from pathlib import Path
from typing import Literal

import pytest
import yaml

from copier import run_copy, run_update

from .helpers import build_file_tree, git_save


def _template(path: Path, config: str, output: str, content: str) -> None:
    """Create a versioned linked template."""
    build_file_tree(
        {
            path / "copier.yml": config,
            path / "{{ _copier_conf.answers_file }}.jinja": (
                "{{ _copier_answers | to_nice_yaml }}"
            ),
            path / output: content,
        }
    )
    git_save(path, tag="v1.0.0")


def _copy_fragment_pair(
    tmp_path: Path,
    parent_config: str = "_accept_children: true\n",
    child_output: str = "child output",
) -> tuple[Path, Path, Path]:
    """Copy a parent README composed from one versioned child fragment."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        parent_config,
        "README.md.jinja",
        "Intro\nOne\nTwo\nThree\nFour\nFive\n"
        "{% for child in _children %}"
        "{{ render_child(child, 'part.jinja') }}"
        "{% endfor %}End\n",
    )
    build_file_tree(
        {
            child / "copier.yml": (
                f"_parent_template: {parent}\n_subdirectory: output\nname: old\n"
            ),
            child / "output/{{ _copier_conf.answers_file }}.jinja": (
                "{{ _copier_answers | to_nice_yaml }}"
            ),
            child / "part.jinja": "Child: {{ name }}\n",
            child / "output/child.txt.jinja": child_output,
        }
    )
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
    return parent, child, dst


def test_child_update_merges_parent_owned_fragment_with_project_edit(
    tmp_path: Path,
) -> None:
    """A changed fragment keeps an independent edit in its parent's file."""
    _, child, dst = _copy_fragment_pair(tmp_path)
    parent_commit = yaml.safe_load((dst / "parent.yml").read_text())["_commit"]
    (dst / "README.md").write_text(
        (dst / "README.md").read_text().replace("Intro", "User intro")
    )
    git_save(dst)
    build_file_tree({child / "output/extra.txt": "version two"})
    git_save(child, tag="v2.0.0")

    run_update(
        dst,
        answers_file="child.yml",
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )

    assert (dst / "README.md").read_text() == (
        "User intro\nOne\nTwo\nThree\nFour\nFive\nChild: new\nEnd\n"
    )
    assert yaml.safe_load((dst / "parent.yml").read_text())["_commit"] == parent_commit


@pytest.mark.parametrize("conflict", ["inline", "rej"])
def test_child_update_uses_configured_conflict_for_parent_file(
    tmp_path: Path, conflict: Literal["inline", "rej"]
) -> None:
    """Overlapping fragment edits use Copier's selected conflict form."""
    _, child, dst = _copy_fragment_pair(tmp_path)
    (dst / "README.md").write_text(
        (dst / "README.md").read_text().replace("Child: old", "Child: user")
    )
    git_save(dst)
    build_file_tree({child / "output/extra.txt": "version two"})
    git_save(child, tag="v2.0.0")

    run_update(
        dst,
        answers_file="child.yml",
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
        conflict=conflict,
    )

    readme = (dst / "README.md").read_text()
    if conflict == "inline":
        assert "<<<<<<< before updating" in readme
        assert "Child: user" in readme
        assert "Child: new" in readme
        assert not (dst / "README.md.rej").exists()
    else:
        assert "Child: new" in readme
        assert "Child: user" in (dst / "README.md.rej").read_text()


@pytest.mark.parametrize("policy", ["deleted", "skip"])
def test_child_update_respects_parent_file_policy(tmp_path: Path, policy: str) -> None:
    """An update honors an intentionally removed or skipped parent file."""
    _, child, dst = _copy_fragment_pair(tmp_path)
    original = (dst / "README.md").read_text()
    if policy == "deleted":
        (dst / "README.md").unlink()
    git_save(dst)
    build_file_tree({child / "output/extra.txt": "version two"})
    git_save(child, tag="v2.0.0")

    run_update(
        dst,
        answers_file="child.yml",
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
        skip_if_exists=["README.md"] if policy == "skip" else [],
    )

    if policy == "deleted":
        assert not (dst / "README.md").exists()
    else:
        assert (dst / "README.md").read_text() == original


@pytest.mark.parametrize("deleted", [False, True])
def test_child_update_uses_parent_template_skip_if_exists(
    tmp_path: Path, deleted: bool
) -> None:
    """An owner's skip rule protects existing files and restores deleted ones."""
    _, child, dst = _copy_fragment_pair(
        tmp_path,
        parent_config="_accept_children: true\n_skip_if_exists: [README.md]\n",
    )
    original = (dst / "README.md").read_text()
    if deleted:
        (dst / "README.md").unlink()
    git_save(dst)
    build_file_tree({child / "output/extra.txt": "version two"})
    git_save(child, tag="v2.0.0")

    run_update(
        dst,
        answers_file="child.yml",
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )

    if deleted:
        assert "Child: new" in (dst / "README.md").read_text()
    else:
        assert (dst / "README.md").read_text() == original


def test_parent_update_merges_child_output_without_advancing_child(
    tmp_path: Path,
) -> None:
    """A parent answer update merges a child file at the child's saved version."""
    parent, _, dst = _copy_fragment_pair(
        tmp_path,
        parent_config="_accept_children: true\nname: old\n",
        child_output=(
            "User line\nOne\nTwo\nThree\nFour\nFive\n"
            "Parent: {{ _parent.name }}\n"
        ),
    )
    child_commit = yaml.safe_load((dst / "child.yml").read_text())["_commit"]
    (dst / "child.txt").write_text(
        (dst / "child.txt").read_text().replace("User line", "Local line")
    )
    git_save(dst)
    build_file_tree({parent / "extra.txt": "version two"})
    git_save(parent, tag="v2.0.0")

    run_update(
        dst,
        answers_file="parent.yml",
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )

    assert (dst / "child.txt").read_text() == (
        "Local line\nOne\nTwo\nThree\nFour\nFive\nParent: new\n"
    )
    assert yaml.safe_load((dst / "child.yml").read_text())["_commit"] == child_commit


@pytest.mark.parametrize("selected", ["one.yml", "two.yml"])
def test_child_update_merges_only_selected_same_repository_parent(
    tmp_path: Path, selected: str
) -> None:
    """A child remains pinned to its selected parent instance during update."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\nname: parent\n",
        "parents-{{ name }}.md.jinja",
        "Intro\nOne\nTwo\nThree\nFour\nFive\n"
        "{% for child in _children %}"
        "{{ render_child(child, 'part.jinja') }}"
        "{% endfor %}End\n",
    )
    build_file_tree(
        {
            child / "copier.yml": (
                f"_parent_template: {parent}\n_subdirectory: output\nname: old\n"
            ),
            child / "output/{{ _copier_conf.answers_file }}.jinja": (
                "{{ _copier_answers | to_nice_yaml }}"
            ),
            child / "part.jinja": "Child: {{ name }}\n",
            child / "output/child.txt": "child output",
        }
    )
    git_save(child, tag="v1.0.0")
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
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    selected_name = selected.removesuffix(".yml")
    other_name = "two" if selected_name == "one" else "one"
    selected_file = dst / f"parents-{selected_name}.md"
    other_file = dst / f"parents-{other_name}.md"
    other_before = other_file.read_text()
    selected_file.write_text(selected_file.read_text().replace("Intro", "User intro"))
    git_save(dst)
    build_file_tree({child / "output/extra.txt": "version two"})
    git_save(child, tag="v2.0.0")

    run_update(
        dst,
        answers_file="child.yml",
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )

    assert selected_file.read_text().startswith("User intro\n")
    assert "Child: new\n" in selected_file.read_text()
    assert other_file.read_text() == other_before


def test_leaf_update_merges_nested_fragment_in_root_owned_file(
    tmp_path: Path,
) -> None:
    """A leaf update merges through its ancestors without changing a sibling."""
    root, middle, leaf, sibling, dst = (
        tmp_path / name for name in ("root", "middle", "leaf", "sibling", "dst")
    )
    _template(
        root,
        "_accept_children: true\n",
        "README.md.jinja",
        "Intro\nOne\nTwo\nThree\nFour\nFive\n"
        "{% for child in _children %}"
        "{{ render_child(child, 'middle.jinja') }}"
        "{% endfor %}End\n",
    )
    build_file_tree(
        {
            middle / "copier.yml": (
                f"_parent_template: {root}\n_accept_children: true\n"
                "_subdirectory: output\n"
            ),
            middle / "middle.jinja": (
                "{% for child in _children %}"
                "{{ render_child(child, 'leaf.jinja') }}"
                "{% endfor %}"
            ),
            middle / "output/{{ _copier_conf.answers_file }}.jinja": (
                "{{ _copier_answers | to_nice_yaml }}"
            ),
            middle / "output/middle.txt": "middle output",
        }
    )
    git_save(middle, tag="v1.0.0")
    build_file_tree(
        {
            leaf / "copier.yml": (
                f"_parent_template: {middle}\n_subdirectory: output\nname: old\n"
            ),
            leaf / "leaf.jinja": "Leaf: {{ name }}\n",
            leaf / "output/{{ _copier_conf.answers_file }}.jinja": (
                "{{ _copier_answers | to_nice_yaml }}"
            ),
            leaf / "output/leaf.txt": "leaf output",
        }
    )
    git_save(leaf, tag="v1.0.0")
    build_file_tree(
        {
            sibling / "copier.yml": (
                f"_parent_template: {middle}\n_subdirectory: output\nname: side\n"
            ),
            sibling / "leaf.jinja": "Sibling: {{ name }}\n",
            sibling / "output/{{ _copier_conf.answers_file }}.jinja": (
                "{{ _copier_answers | to_nice_yaml }}"
            ),
            sibling / "output/sibling.txt.jinja": "Sibling output: {{ name }}\n",
        }
    )
    git_save(sibling, tag="v1.0.0")
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
        str(sibling),
        dst,
        answers_file="sibling.yml",
        parent_answers_file="middle.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    ancestor_commits = {
        name: yaml.safe_load((dst / name).read_text())["_commit"]
        for name in ("root.yml", "middle.yml")
    }
    sibling_commit = yaml.safe_load((dst / "sibling.yml").read_text())["_commit"]
    sibling_output = (dst / "sibling.txt").read_text()
    (dst / "README.md").write_text(
        (dst / "README.md").read_text().replace("Intro", "User intro")
    )
    git_save(dst)
    build_file_tree({leaf / "output/extra.txt": "version two"})
    git_save(leaf, tag="v2.0.0")
    build_file_tree({sibling / "output/sibling.txt.jinja": "Changed sibling output\n"})
    git_save(sibling, tag="v2.0.0")

    run_update(
        dst,
        answers_file="leaf.yml",
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )

    assert (dst / "README.md").read_text() == (
        "User intro\nOne\nTwo\nThree\nFour\nFive\n"
        "Leaf: new\nSibling: side\nEnd\n"
    )
    assert (dst / "sibling.txt").read_text() == sibling_output
    assert yaml.safe_load((dst / "sibling.yml").read_text())["_commit"] == (
        sibling_commit
    )
    assert {
        name: yaml.safe_load((dst / name).read_text())["_commit"]
        for name in ancestor_commits
    } == ancestor_commits
