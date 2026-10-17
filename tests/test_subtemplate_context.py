"""Acceptance tests for direct linked answer contexts."""

from pathlib import Path

import pytest
import yaml

from copier import run_copy, run_recopy

from .helpers import build_file_tree


def _template(path: Path, config: str, filename: str, content: str) -> None:
    """Create a template with an answers file and one rendered output."""
    build_file_tree(
        {
            path / "copier.yml": config,
            path / "{{ _copier_conf.answers_file }}.jinja": (
                "{{ _copier_answers | to_nice_yaml }}"
            ),
            path / filename: content,
        }
    )


def test_repeated_children_render_in_order_with_separate_answers(
    tmp_path: Path,
) -> None:
    """A parent reads each direct child instance in saved link order."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\n"
        "children_summary:\n"
        "  default: \"{{ _children | map(attribute='name') | join(',') }}\"\n"
        "  when: false\n",
        "children.txt.jinja",
        "{{ children_summary }}|"
        "{% for child in _children %}{{ child.name }};{% endfor %}",
    )
    _template(
        child,
        f"_parent_template: {parent}\nname: child\n",
        "child-{{ name }}.txt.jinja",
        "{{ name }}",
    )
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    for answers_file, name in (("first.yml", "first"), ("second.yml", "second")):
        run_copy(
            str(child),
            dst,
            answers_file=answers_file,
            parent_answers_file="parent.yml",
            data={"name": name},
            defaults=True,
            quiet=True,
            overwrite=True,
        )

    run_recopy(
        dst, answers_file="parent.yml", defaults=True, quiet=True, overwrite=True
    )

    assert (dst / "children.txt").read_text() == "first,second|first;second;"


def test_child_prompt_and_filename_use_selected_parent_fresh_answers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A child sees only its selected parent's current recorded answers."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\nname: default\n",
        "parent-{{ name }}.txt.jinja",
        "{{ name }}",
    )
    _template(
        child,
        f"_parent_template: {parent}\n"
        "_message_after_copy: 'Selected {{ _parent.name }}'\n"
        "parent_name:\n  default: '{{ _parent.name }}'\n",
        "child-{{ _parent.name }}.txt.jinja",
        "{{ parent_name }}",
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
        parent_answers_file="two.yml",
        defaults=True,
        quiet=False,
    )

    assert (dst / "child-two.txt").read_text() == "two"
    assert not (dst / "child-one.txt").exists()
    assert "Selected two" in capsys.readouterr().err


def test_three_level_contexts_include_only_direct_relatives(tmp_path: Path) -> None:
    """Each instance reads its direct parent and children only."""
    root, middle, leaf, dst = (
        tmp_path / name for name in ("root", "middle", "leaf", "dst")
    )
    _template(
        root,
        "_accept_children: true\nname: root\n",
        "root.txt.jinja",
        "{{ _children | map(attribute='name') | join(',') }}",
    )
    _template(
        middle,
        f"_accept_children: true\n_parent_template: {root}\nname: middle\n",
        "middle.txt.jinja",
        "{{ _parent.name }}|{{ _children | map(attribute='name') | join(',') }}",
    )
    _template(
        leaf,
        f"_parent_template: {middle}\nname: leaf\n",
        "leaf.txt.jinja",
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
    run_recopy(
        dst, answers_file="middle.yml", defaults=True, quiet=True, overwrite=True
    )
    run_recopy(dst, answers_file="root.yml", defaults=True, quiet=True, overwrite=True)

    assert (dst / "root.txt").read_text() == "middle"
    assert (dst / "middle.txt").read_text() == "root|leaf"
    assert (dst / "leaf.txt").read_text() == "middle"


def test_templated_answers_path_resolves_children_for_later_prompts(
    tmp_path: Path,
) -> None:
    """Prompts after the path answer see linked children."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\n"
        "_answers_file: '{{ name }}.yml'\n"
        "name:\n  default: parent\n"
        "child_count:\n"
        "  default: '{{ _children | length }}'\n"
        "  when: false\n",
        "count.txt.jinja",
        "{{ child_count }}",
    )
    _template(child, f"_parent_template: {parent}\n", "child.txt.jinja", "child")
    run_copy(str(parent), dst, defaults=True, quiet=True)
    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file="parent.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )

    run_copy(str(parent), dst, defaults=True, quiet=True, overwrite=True)

    assert (dst / "count.txt").read_text() == "1"


@pytest.mark.parametrize("when", ["false", "'{{ false }}'"])
def test_linked_context_excludes_secret_hidden_and_reserved_values(
    tmp_path: Path,
    when: str,
) -> None:
    """Linked mappings contain only recorded visible question answers."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\n"
        "name: visible\n"
        "secret_value:\n  default: private\n  secret: true\n"
        f"hidden_value:\n  default: hidden\n  when: {when}\n",
        "privacy.txt.jinja",
        "{{ _children[0].keys() | list | sort | join(',') if _children else '' }}",
    )
    _template(
        child,
        f"_parent_template: {parent}\nname: child\n"
        "secret_value:\n  default: private\n  secret: true\n"
        f"hidden_value:\n  default: hidden\n  when: {when}\n",
        "child-privacy.txt.jinja",
        "{{ _parent.keys() | list | sort | join(',') }}",
    )
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    parent_answers = yaml.safe_load((dst / "parent.yml").read_text())
    parent_answers.update(
        secret_value="private", hidden_value="hidden", unrelated="unrelated"
    )
    (dst / "parent.yml").write_text(yaml.safe_dump(parent_answers))
    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file="parent.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    child_answers = yaml.safe_load((dst / "child.yml").read_text())
    child_answers.update(
        secret_value="private", hidden_value="hidden", unrelated="unrelated"
    )
    (dst / "child.yml").write_text(yaml.safe_dump(child_answers))
    run_recopy(
        dst, answers_file="parent.yml", defaults=True, quiet=True, overwrite=True
    )

    assert (dst / "privacy.txt").read_text() == "name"
    assert (dst / "child-privacy.txt").read_text() == "name"


@pytest.mark.parametrize(
    "reveal, expected", [(False, "name,reveal"), (True, "hidden_value,name,reveal")]
)
def test_linked_context_evaluates_when_from_recorded_answers(
    tmp_path: Path, reveal: bool, expected: str
) -> None:
    """A linked question's visibility follows its saved condition input."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\nname: visible\n"
        "reveal: false\n"
        "hidden_value:\n  default: hidden\n  when: '{{ reveal }}'\n",
        "parent.txt.jinja",
        "parent",
    )
    _template(
        child,
        f"_parent_template: {parent}\n",
        "visible.txt.jinja",
        "{{ _parent.keys() | list | sort | join(',') }}",
    )
    run_copy(
        str(parent),
        dst,
        answers_file="parent.yml",
        data={"reveal": reveal},
        defaults=True,
        quiet=True,
    )
    parent_answers = yaml.safe_load((dst / "parent.yml").read_text())
    parent_answers["hidden_value"] = "stale" if not reveal else "visible"
    (dst / "parent.yml").write_text(yaml.safe_dump(parent_answers))

    run_copy(
        str(child),
        dst,
        answers_file="child.yml",
        parent_answers_file="parent.yml",
        defaults=True,
        quiet=True,
    )

    assert (dst / "visible.txt").read_text() == expected
