"""Form layout primitive: :class:`Fieldset`.

A :class:`Fieldset` groups model fields into a labeled section the UI can
render as a panel, tab, or collapsible block. Declared statically on a
:class:`~asterion.registry.ModelAdmin`::

    class PostAdmin(ModelAdmin):
        fieldsets = [
            Fieldset("Content", fields=["title", "slug", "body"]),
            Fieldset("Publishing", fields=["status", "published_at"]),
            Fieldset("SEO", fields=["seo_title", "seo_description"], collapsed=True),
        ]

The contract builder validates each Fieldset against the model (drops
unknown fields, drops protected fields) and emits a
:class:`~asterion.contract.service.FieldsetMeta` for the wire.

Tabs / dependent fields / conditional visibility are deliberately out
of scope for A6 — those land later once we have a UI that needs them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Fieldset:
    """One labeled group of fields for the admin form.

    * ``label`` — display string for the section header. Required.
    * ``fields`` — ordered list of model attribute names that belong
      to this section. Fields not present on the model, or fields
      filtered by ``protected_fields`` / ``all_protected``, are silently
      dropped by the contract builder so a misconfigured fieldset
      degrades to a partial render rather than a 500.
    * ``collapsed`` — UI hint: render the section collapsed by default.
    * ``description`` — optional one-liner shown under the header.
    """

    label: str
    fields: list[str] = field(default_factory=list)
    collapsed: bool = False
    description: str | None = None
