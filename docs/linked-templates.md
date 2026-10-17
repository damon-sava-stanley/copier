# Linked templates

Linked templates let one template instance use another instance's answers and
fragments. An *instance* is identified by its answers-file path in the generated
project. Several instances can use the same template repository, and each can be
copied, recopied, or updated separately.

## Declare a link

The parent template allows children in its `copier.yml`:

```yaml title="base-template/copier.yml"
_accept_children: true
name: base
```

The child names the parent's **repository**, not a particular instance or version.
Give each instance a distinct answers file. Each template must generate its answers
file. Put this file in the root of every template repository (including the leaf):

```jinja title="{{ _copier_conf.answers_file }}.jinja"
{{ _copier_answers|to_nice_yaml }}
```

The templated filename makes each `-a` selection generate its own answers file.
See [the answers-file guide](configuring.md#the-copier-answersyml-file).

```yaml title="plugin-template/copier.yml"
_parent_template: gh:example/base-template
_exclude: [fragments]
name: plugin
```

A template can declare both `_parent_template` and `_accept_children` when it sits
between a parent and its own children. An ordinary template without either setting
continues to work independently.

## Select the parent instance on first copy

The first copy of a child requires `--parent-answers-file`, even when the parent
uses the default `.copier-answers.yml`. The path is relative to the destination
root. Copier checks that this answers file belongs to the declared parent
repository and that the recorded parent version accepts children.

```shell
copier copy -a base-a.yml -d name=alpha gh:example/base-template my-project
copier copy -a base-b.yml -d name=beta gh:example/base-template my-project
copier copy -a plugin-a.yml --parent-answers-file base-a.yml -d name=first gh:example/plugin-template my-project
copier copy -a plugin-b.yml --parent-answers-file base-a.yml -d name=second gh:example/plugin-template my-project
copier copy -a plugin-c.yml --parent-answers-file base-b.yml -d name=third gh:example/plugin-template my-project
```

`plugin-a.yml` and `plugin-b.yml` are separate instances of the same repository.
The order in `base-a.yml` is their copy order. `plugin-c.yml` belongs only to
`base-b.yml`.

The Python API makes the same selection with `parent_answers_file`:

```python
import copier

copier.run_copy(
    "gh:example/plugin-template",
    "my-project",
    answers_file="plugin-a.yml",
    parent_answers_file="base-a.yml",
    data={"name": "first"},
)
```

Copier records reciprocal links alongside the normal answers:

```yaml title="base-a.yml"
_src_path: gh:example/base-template
_children:
  - plugin-a.yml
  - plugin-b.yml
name: alpha
```

```yaml title="plugin-a.yml"
_src_path: gh:example/plugin-template
_parent: base-a.yml
name: first
```

Versioned repositories also have their usual `_commit` entry. Do not edit link
metadata by hand. When you recopy or update an instance, select it with
`--answers-file`; Copier follows its saved link without another parent selection:

```shell
copier recopy --answers-file plugin-a.yml my-project
copier update --answers-file plugin-a.yml my-project
```

## Use linked answers and fragments

The parent receives `_children` in copy order. Each item has that direct child's
recorded, nonsecret question answers. The child receives `_parent` with its
immediate parent's answers. These values work in prompts, filenames, file contents,
and template messages. Copier metadata, hidden answers, and secret answers are not
exposed across a link.

Here the base template writes one README per `name`, so the two base instances
own different output paths:

```jinja title="base-template/{{ name }}/README.md.jinja"
# {{ name }}
{% for child in _children %}
## {{ child.name }}
{{ render_child(child, 'fragments/plugin.jinja') }}
{% endfor %}
```

```jinja title="plugin-template/fragments/plugin.jinja"
{{ name }} belongs to {{ _parent.name }}.
```

`render_child(child, path)` renders a fragment from that exact child instance at
its recorded template revision. It uses the child's answers and Jinja settings,
including its own `_parent` and `_children` when present. The path is relative to
the child template root. A fragment can be outside `_subdirectory` or excluded
from ordinary output with `_exclude`. The path must remain inside the child
template, and the child template must meet Copier's trust requirements.

### Three-level chain

For a three-level chain, configure the plugin template to accept children
**before** copying `plugin-a.yml`:

```yaml title="plugin-template/copier.yml"
_parent_template: gh:example/base-template
_accept_children: true
_exclude: [fragments]
name: plugin
```

The leaf names that middle repository:

```yaml title="leaf-template/copier.yml"
_parent_template: gh:example/plugin-template
_exclude: [fragments]
name: leaf
```

With `base-a.yml` and `plugin-a.yml` created using that configuration, attach
a leaf to the middle instance:

```shell
copier copy -a leaf-a.yml --parent-answers-file plugin-a.yml -d name=leaf-one gh:example/leaf-template my-project
```

The root's README can render a middle fragment, which renders its own child:

```jinja title="plugin-template/fragments/plugin.jinja"
{{ name }} belongs to {{ _parent.name }}.
{% for child in _children %}
{{ render_child(child, 'fragments/leaf.jinja') }}
{% endfor %}
```

```jinja title="leaf-template/fragments/leaf.jinja"
{{ name }} belongs to {{ _parent.name }}.
```

The root sees only `plugin-a.yml` and `plugin-b.yml` as direct children. The
middle instance sees `base-a.yml` as its parent and `leaf-a.yml` as its child.
The leaf sees only the middle instance as its parent.

## Recopy and update

A primary copy, recopy, or update renders the selected instance, then rerenders
its descendants from parent to child and its ancestors from child to parent.
Other branches stay as they were. Dependent instances keep their recorded answers
and template revisions; they do not ask questions or run tasks or migrations.
For example, changing `plugin-a.yml` rerenders `leaf-a.yml` and `base-a.yml`,
while `plugin-b.yml` and `base-b.yml` are unaffected.

On copy or recopy, dependent files follow the normal copy overwrite and
`skip_if_exists` rules. On update, Copier renders each dependent at its **same
recorded revision** with the old and new linked answers, then merges that
change into the project. Independent project edits are preserved when possible.
Overlapping edits use the usual `--conflict=inline` or `--conflict=rej` behavior.
The instance that owns a generated file owns its merge: a root README containing
a leaf fragment belongs to the root. Linked templates must produce distinct
output paths; Copier rejects collisions between instances.
