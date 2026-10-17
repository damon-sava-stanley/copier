"""Acceptance tests for linked template instance metadata."""

from pathlib import Path

import pytest
import yaml

from copier import run_copy
from copier._cli import CopierApp
from copier.errors import UserMessageError

from .helpers import build_file_tree, git_save


def _template(path: Path, config: str) -> None:
    """Create a template that writes its selected answers file."""
    build_file_tree(
        {
            path / "copier.yml": config,
            path
            / "{{ _copier_conf.answers_file }}.jinja": "{{ _copier_answers | to_nice_yaml }}",
        }
    )


@pytest.mark.parametrize("selected", ["first.yml", "second.yml"])
def test_child_pins_selected_parent_instance(tmp_path: Path, selected: str) -> None:
    """A child attaches only to the explicitly selected parent instance."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n")
    _template(child, f"_parent_template: {parent}\n")
    for name in ("first.yml", "second.yml"):
        run_copy(str(parent), dst, answers_file=name, defaults=True, quiet=True)

    with pytest.raises(UserMessageError, match="parent-answers-file"):
        run_copy(str(child), dst, answers_file="child.yml", defaults=True, quiet=True)
    assert not (dst / "child.yml").exists()

    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file=selected,
        defaults=True,
        quiet=True,
    )
    assert yaml.safe_load((dst / "child.yml").read_text())["_parent"] == selected
    for name in ("first.yml", "second.yml"):
        parent_answers = yaml.safe_load((dst / name).read_text())
        assert parent_answers.get("_children", []) == (
            ["child.yml"] if name == selected else []
        )


def test_relative_git_parent_source_matches_saved_absolute_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative Git declaration links to the same absolute saved source."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n")
    git_save(parent)
    _template(child, "_parent_template: parent\n")
    monkeypatch.chdir(tmp_path)
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)

    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file="parent.yml",
        defaults=True,
        quiet=True,
    )

    assert yaml.safe_load((dst / "child.yml").read_text())["_parent"] == "parent.yml"


def test_templated_answers_path_keeps_saved_parent_link(tmp_path: Path) -> None:
    """Removing a parent declaration cannot erase a linked child's metadata."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n")
    _template(
        child,
        f"_parent_template: {parent}\n_answers_file: '{{{{ name }}}}.yml'\nname:\n  default: child\n",
    )
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    run_copy(
        str(child),
        dst,
        parent_answers_file="parent.yml",
        defaults=True,
        quiet=True,
    )
    original = (dst / "child.yml").read_text()
    _template(child, "_answers_file: '{{ name }}.yml'\nname:\n  default: child\n")

    with pytest.raises(UserMessageError, match="Parent repository mismatch"):
        run_copy(str(child), dst, defaults=True, quiet=True, overwrite=True)

    assert (dst / "child.yml").read_text() == original
    assert yaml.safe_load((dst / "parent.yml").read_text())["_children"] == [
        "child.yml"
    ]


def test_unlinked_templated_answers_path_still_copies(tmp_path: Path) -> None:
    """A standalone template can render its answers path from a default."""
    src, dst = (tmp_path / name for name in ("src", "dst"))
    _template(src, "_answers_file: '{{ name }}.yml'\nname:\n  default: solo\n")

    run_copy(str(src), dst, defaults=True, quiet=True)

    assert yaml.safe_load((dst / "solo.yml").read_text())["name"] == "solo"


def test_repeated_child_instances_keep_link_order(tmp_path: Path) -> None:
    """One parent records each child instance by its distinct answers path."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n")
    _template(child, f"_parent_template: {parent}\n")
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    for child_path in ("one.yml", "two.yml"):
        run_copy(
            str(child),
            dst,
            answers_file=child_path,
            parent_answers_file="parent.yml",
            defaults=True,
            quiet=True,
        )
    assert yaml.safe_load((dst / "parent.yml").read_text())["_children"] == [
        "one.yml",
        "two.yml",
    ]
    with pytest.raises(UserMessageError, match="already used"):
        run_copy(
            str(child),
            dst,
            answers_file="one.yml",
            parent_answers_file="parent.yml",
            defaults=True,
            quiet=True,
        )
    assert yaml.safe_load((dst / "parent.yml").read_text())["_children"] == [
        "one.yml",
        "two.yml",
    ]


def test_three_level_link_chain(tmp_path: Path) -> None:
    """A middle instance records both its parent and child links."""
    root, middle, leaf, dst = (
        tmp_path / name for name in ("root", "middle", "leaf", "dst")
    )
    _template(root, "_accept_children: true\n")
    _template(middle, f"_accept_children: true\n_parent_template: {root}\n")
    _template(leaf, f"_parent_template: {middle}\n")
    run_copy(str(root), dst, answers_file="root.yml", defaults=True, quiet=True)
    run_copy(
        str(middle),
        dst,
        answers_file="middle.yml",
        parent_answers_file="root.yml",
        defaults=True,
        quiet=True,
    )
    run_copy(
        str(leaf),
        dst,
        answers_file="leaf.yml",
        parent_answers_file="middle.yml",
        defaults=True,
        quiet=True,
    )
    assert yaml.safe_load((dst / "middle.yml").read_text()) == {
        "_src_path": str(middle),
        "_parent": "root.yml",
        "_children": ["leaf.yml"],
    }


def test_copy_cli_selects_parent_instance(tmp_path: Path) -> None:
    """The copy command passes its explicit parent selection to the API."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n")
    _template(child, f"_parent_template: {parent}\n")
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    _, status = CopierApp.run(
        [
            "copier",
            "copy",
            "--quiet",
            "--defaults",
            "--answers-file",
            "child.yml",
            "--parent-answers-file",
            "parent.yml",
            str(child),
            str(dst),
        ],
        exit=False,
    )
    assert status == 0
    assert yaml.safe_load((dst / "child.yml").read_text())["_parent"] == "parent.yml"


@pytest.mark.parametrize("failure", ["missing", "mismatch", "nonaccept", "escape"])
def test_invalid_parent_selection_does_not_write_child(
    tmp_path: Path, failure: str
) -> None:
    """An invalid parent selection fails before child output is written."""
    parent, other, child, dst = (
        tmp_path / name for name in ("parent", "other", "child", "dst")
    )
    _template(parent, "_accept_children: true\n")
    _template(other, "_accept_children: false\n")
    _template(child, f"_parent_template: {parent}\n")
    if failure == "mismatch":
        run_copy(str(other), dst, answers_file="parent.yml", defaults=True, quiet=True)
        selected = "parent.yml"
    elif failure == "nonaccept":
        _template(other, "_accept_children: false\n")
        _template(child, f"_parent_template: {other}\n")
        run_copy(str(other), dst, answers_file="parent.yml", defaults=True, quiet=True)
        selected = "parent.yml"
    elif failure == "escape":
        selected = "../outside.yml"
    else:
        selected = "missing.yml"
    with pytest.raises(UserMessageError):
        run_copy(
            str(child),
            dst,
            answers_file="child.yml",
            parent_answers_file=selected,
            defaults=True,
            quiet=True,
        )
    assert not (dst / "child.yml").exists()


def test_cycle_in_saved_links_fails_before_copy(tmp_path: Path) -> None:
    """A saved reciprocal cycle blocks rendering before output changes."""
    a, b, dst = (tmp_path / name for name in ("a", "b", "dst"))
    _template(a, f"_accept_children: true\n_parent_template: {b}\n")
    _template(b, f"_accept_children: true\n_parent_template: {a}\n")
    build_file_tree(
        {
            dst / "a.yml": yaml.safe_dump(
                {
                    "_src_path": str(a),
                    "_parent": "b.yml",
                    "_children": ["b.yml"],
                }
            ),
            dst / "b.yml": yaml.safe_dump(
                {
                    "_src_path": str(b),
                    "_parent": "a.yml",
                    "_children": ["a.yml"],
                }
            ),
        }
    )
    before = (dst / "a.yml").read_text()
    with pytest.raises(UserMessageError, match="Cycle"):
        run_copy(str(a), dst, answers_file="a.yml", defaults=True, quiet=True)
    assert (dst / "a.yml").read_text() == before


@pytest.mark.parametrize("child_link", ["missing.yml", "../outside.yml", "child.yml"])
def test_invalid_saved_link_blocks_parent_copy(tmp_path: Path, child_link: str) -> None:
    """Missing, escaping, and inconsistent saved links block destination writes."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(parent, "_accept_children: true\n")
    _template(child, f"_parent_template: {parent}\n")
    build_file_tree(
        {
            dst / "parent.yml": yaml.safe_dump(
                {"_src_path": str(parent), "_children": [child_link]}
            ),
            dst / "child.yml": yaml.safe_dump({"_src_path": str(child)}),
        }
    )
    before = (dst / "parent.yml").read_text()
    with pytest.raises(UserMessageError):
        run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    assert (dst / "parent.yml").read_text() == before
