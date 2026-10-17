"""Acceptance tests for linked child fragments."""

import os
from pathlib import Path

import pytest
from jinja2 import Environment
from jinja2.ext import Extension

from copier import run_copy, run_recopy
from copier.errors import ForbiddenPathError, UnsafeTemplateError, UserMessageError

from .helpers import build_file_tree, git_save


class _SideEffectJinjaExtension(Extension):
    """Record whether an untrusted extension was constructed."""

    def __init__(self, environment: Environment) -> None:
        super().__init__(environment)
        Path(os.environ["COPIER_FRAGMENT_EXTENSION_MARKER"]).write_text("loaded")


def _template(path: Path, config: str, files: dict[str, str]) -> None:
    """Create a template with a generated answers file."""
    config = config.replace("_exclude: [", "_exclude: ['copier.yml', ")
    output_root = path / "output" if "_subdirectory: output" in config else path
    build_file_tree(
        {
            path / "copier.yml": config,
            output_root / "{{ _copier_conf.answers_file }}.jinja": (
                "{{ _copier_answers | to_nice_yaml }}"
            ),
            **{path / name: content for name, content in files.items()},
        }
    )


def test_parent_renders_two_child_instances_with_separate_contexts(
    tmp_path: Path,
) -> None:
    """Each fragment receives its own answers and selected parent."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\nname: parent\n",
        {
            "README.md.jinja": (
                "{% for child in _children %}"
                "{{ render_child(child, 'fragments/item.jinja') }}"
                "{% endfor %}"
            )
        },
    )
    _template(
        child,
        f"_parent_template: {parent}\n_exclude: ['fragments']\nname: child\n",
        {"fragments/item.jinja": "{{ name }}@{{ _parent.name }};"},
    )
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    for answers_file, name in (("one.yml", "one"), ("two.yml", "two")):
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

    assert (dst / "README.md").read_text() == "one@parent;two@parent;"
    assert not (dst / "fragments").exists()


def test_parent_recopy_fragment_sees_new_parent_answer(tmp_path: Path) -> None:
    """A child fragment sees the active parent's newly answered value."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\nname: old\n",
        {
            "README.md.jinja": (
                "{% for child in _children %}"
                "{{ render_child(child, 'fragment.jinja') }}"
                "{% endfor %}"
            )
        },
    )
    _template(
        child,
        f"_parent_template: {parent}\n_exclude: ['fragment']\n",
        {"fragment.jinja": "{{ _parent.name }}"},
    )
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

    run_recopy(
        dst,
        answers_file="parent.yml",
        data={"name": "new"},
        defaults=True,
        quiet=True,
        overwrite=True,
    )

    assert (dst / "README.md").read_text() == "new"
    assert "name: new" in (dst / "parent.yml").read_text()


def test_fragment_uses_child_configuration_and_copier_context(tmp_path: Path) -> None:
    """A child fragment receives its own ordinary Copier render fields."""
    root, middle, leaf, dst = (
        tmp_path / name for name in ("root", "middle", "leaf", "dst")
    )
    _template(
        root,
        "_accept_children: true\nname: root\n",
        {
            "README.md.jinja": (
                "{% if _children %}"
                "{{ render_child(_children[0], 'middle.jinja') }}"
                "{% endif %}"
            )
        },
    )
    _template(
        middle,
        f"_accept_children: true\n_parent_template: {root}\n"
        "_exclude: ['middle']\n"
        "_external_data:\n  extra: middle-extra.yml\nname: middle\n",
        {
            "middle-extra.yml": "token: middle-data\n",
            "middle.jinja": (
                "{{ _copier_conf.answers_file }}|{{ _copier_answers.name }}|"
                "{{ _copier_conf.src_path.name }}|{{ _copier_python != '' }}|"
                "{{ _external_data.extra.token }}|"
                "{% if _children %}"
                "{{ render_child(_children[0], 'leaf.jinja') }}"
                "{% endif %}"
            ),
        },
    )
    _template(
        leaf,
        f"_parent_template: {middle}\n_exclude: ['leaf']\n"
        "_external_data:\n  extra: leaf-extra.yml\nname: leaf\n",
        {
            "leaf-extra.yml": "token: leaf-data\n",
            "leaf.jinja": (
                "{{ _copier_conf.answers_file }}|{{ _copier_answers.name }}|"
                "{{ _copier_conf.src_path.name }}|{{ _copier_python != '' }}|"
                "{{ _external_data.extra.token }}"
            ),
        },
    )
    run_copy(str(root), dst, answers_file="root.yml", defaults=True, quiet=True)
    build_file_tree({dst / "middle-extra.yml": "token: middle-data\n"})
    run_copy(
        str(middle),
        dst,
        answers_file="middle.yml",
        parent_answers_file="root.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )
    build_file_tree({dst / "leaf-extra.yml": "token: leaf-data\n"})
    run_copy(
        str(leaf),
        dst,
        answers_file="leaf.yml",
        parent_answers_file="middle.yml",
        defaults=True,
        quiet=True,
        overwrite=True,
    )

    run_recopy(dst, answers_file="root.yml", defaults=True, quiet=True, overwrite=True)

    assert (dst / "README.md").read_text() == (
        "middle.yml|middle|middle|True|middle-data|leaf.yml|leaf|leaf|True|leaf-data"
    )


def test_three_level_fragment_uses_each_instance_context(tmp_path: Path) -> None:
    """A middle fragment can compose a leaf using direct relative answers."""
    root, middle, leaf, dst = (
        tmp_path / name for name in ("root", "middle", "leaf", "dst")
    )
    _template(
        root,
        "_accept_children: true\nname: root\n",
        {
            "README.md.jinja": (
                "{% if _children %}"
                "{{ render_child(_children[0], 'middle.jinja') }}"
                "{% endif %}"
            )
        },
    )
    _template(
        middle,
        f"_accept_children: true\n_parent_template: {root}\n"
        "_subdirectory: output\nname: middle\n",
        {
            "middle.jinja": (
                "{{ name }}@{{ _parent.name }}:"
                "{% if _children %}"
                "{{ render_child(_children[0], 'leaf.jinja') }}"
                "{% endif %}"
            ),
            "output/middle.txt.jinja": "{{ name }}",
        },
    )
    _template(
        leaf,
        f"_parent_template: {middle}\n_exclude: ['leaf']\nname: leaf\n",
        {"leaf.jinja": "{{ name }}@{{ _parent.name }}"},
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

    run_recopy(dst, answers_file="root.yml", defaults=True, quiet=True, overwrite=True)

    assert (dst / "README.md").read_text() == "middle@root:leaf@middle"
    assert not (dst / "leaf").exists()


def test_fragment_uses_child_jinja_settings_and_recorded_revision(
    tmp_path: Path,
) -> None:
    """The fragment comes from the saved child revision and its Jinja syntax."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\nname: parent\n",
        {
            "README.md.jinja": (
                "{% for child in _children %}"
                "{{ render_child(child, 'fragment.jinja') }}"
                "{% endfor %}"
            )
        },
    )
    build_file_tree(
        {
            child / "copier.yml": (
                f"_parent_template: {parent}\n"
                "_subdirectory: output\n"
                "_envops:\n"
                "  variable_start_string: '[['\n"
                "  variable_end_string: ']]'\n"
                "name: old\n"
            ),
            child / "output/[[ _copier_conf.answers_file ]].jinja": (
                "[[ _copier_answers | to_nice_yaml ]]"
            ),
            child / "fragment.jinja": "[[ name ]]@[[ _parent.name ]]",
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
    (child / "fragment.jinja").write_text("new revision")
    git_save(child, tag="v2.0.0")

    run_recopy(
        dst, answers_file="parent.yml", defaults=True, quiet=True, overwrite=True
    )

    assert (dst / "README.md").read_text() == "old@parent"


def test_untrusted_child_extension_blocks_fragment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Linked answer discovery cannot construct an untrusted extension."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\n",
        {
            "README.md.jinja": (
                "{% for child in _children %}"
                "{{ render_child(child, 'fragment.jinja') }}"
                "{% endfor %}"
            )
        },
    )
    _template(
        child,
        f"_parent_template: {parent}\n_exclude: ['fragment']\n",
        {"fragment.jinja": "child"},
    )
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
    marker = tmp_path / "extension-loaded"
    monkeypatch.setenv("COPIER_FRAGMENT_EXTENSION_MARKER", str(marker))
    (child / "copier.yml").write_text(
        f"_parent_template: {parent}\n_exclude: ['fragment']\n"
        "_jinja_extensions: "
        "['tests.test_subtemplate_fragments._SideEffectJinjaExtension']\n"
    )

    with pytest.raises(UnsafeTemplateError, match="jinja_extensions"):
        run_recopy(
            dst,
            answers_file="parent.yml",
            defaults=True,
            quiet=True,
            overwrite=True,
        )
    assert not marker.exists()


@pytest.mark.parametrize(
    "fragment", ["missing.jinja", "../outside.jinja", "escape.jinja"]
)
def test_bad_fragment_path_fails(tmp_path: Path, fragment: str) -> None:
    """Missing and escaping fragments cannot supply parent output."""
    parent, child, dst = (tmp_path / name for name in ("parent", "child", "dst"))
    _template(
        parent,
        "_accept_children: true\n",
        {
            "README.md.jinja": (
                "{% if _children %}"
                f"{{{{ render_child(_children[0], '{fragment}') }}}}"
                "{% endif %}"
            )
        },
    )
    _template(child, f"_parent_template: {parent}\n", {})
    (tmp_path / "outside.jinja").write_text("PRIVATE")
    run_copy(str(parent), dst, answers_file="parent.yml", defaults=True, quiet=True)
    if fragment == "escape.jinja":
        (child / fragment).symlink_to(tmp_path / "outside.jinja")

    with pytest.raises(
        (UserMessageError, ForbiddenPathError), match="fragment|forbidden"
    ):
        run_copy(
            str(child),
            dst,
            answers_file="child.yml",
            parent_answers_file="parent.yml",
            defaults=True,
            quiet=True,
            overwrite=True,
        )
    assert (dst / "README.md").read_text() == ""
