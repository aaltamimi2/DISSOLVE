# Proposal: shared publication style module

Proposed module path: `figures/publication_style.py`. This file is a proposal
only; the module has not been built. It creates no figure and contains no data.

## Scope

The module would own deterministic visual constants, registered artist
factories, export settings, and source-manifest writing for every manuscript
panel. It would not import `dissolve`, read an engine asset, choose a result, or
register an agent tool. Figure-specific scripts would remain responsible for
loading an admitted artifact and for naming every plotted field.

## Typography and geometry

- `DejaVu Sans`, because it ships with Matplotlib and reproduces without a
  machine-specific font lookup.
- One 10 pt size for title, panel headings, axis labels, tick labels, legends,
  annotations, and route labels. Hierarchy uses semibold weight and spacing,
  never a different size or coloured text.
- Black (`#000000`) for every text artist. Neutral axes/spines may use dark
  gray, but no word or numeral may inherit that gray.
- Double-column width 7.25 in and single-column width 3.50 in; height is a
  figure-specific admitted parameter. PNG export at 300 dpi and vector PDF
  export with creation/modification dates removed.
- Minimum rendered text height 7 pt; minimum data-mark maximum extent 7 pt;
  2 pt text-to-text clearance and 1 pt clearance between other registered
  elements. These are the current mechanical thresholds in
  `figures/standards_checker.py`.

## Palette

Colour carries data only. The initial colour-blind-aware data cycle would be:

| Token | Hex | Intended use |
|---|---|---|
| `blue` | `#0072B2` | primary series / selected path |
| `orange` | `#E69F00` | comparison series / alternate path |
| `green` | `#009E73` | third series / supported state |
| `vermillion` | `#D55E00` | rejected or contradicted data state |
| `purple` | `#CC79A7` | fourth independent series |
| `sky` | `#56B4E9` | fifth independent series |
| `yellow` | `#F0E442` | highlight only, with a dark edge |
| `neutral` | `#B8BEC5` | unavailable/background data state |

Lines would also have distinct dash and marker cycles so colour is not the only
series discriminator. A final polymer-to-colour mapping must be frozen only
after the §1 spec names the recurring polymer set; figure scripts may not assign
colours ad hoc.

## Proposed API

```python
STYLE = PublicationStyle(...)
fig, axes, registry = STYLE.figure(layout=..., height_in=...)
STYLE.axes(ax, ..., registry=registry)  # fixes/records ticks and axis labels
STYLE.text(ax, ..., registry=registry, container=...)
STYLE.line(ax, ..., registry=registry, series_id=...)
STYLE.patch(ax, ..., registry=registry, collision=True)
STYLE.allow_layer(registry, background, foreground)
report = STYLE.check(fig, registry, title=..., content_artists=...)
STYLE.save(fig, stem=..., sources=SourceManifest(...), check_report=report)
```

All annotation text creation would pass through `STYLE.text`, which fixes
family, size, and black colour and registers the artist. `STYLE.axes` would set
fixed tick locators/formatters and register Matplotlib-generated tick and axis
label artists after the first draw; automatic ticks must not become an
unregistered escape hatch. Shape factories would require an explicit collision
role. Intentional layers such as a bar fill over its track would be admitted
only as an exact artist pair, not by region or artist class. The final `check`
would delegate to `figures/standards_checker.py`, whose text discovery makes a
directly-created, unregistered label a failure.

## Provenance sidecar

`STYLE.save` would refuse to export without a `SourceManifest` containing:

- absolute or repository-relative source path;
- expected SHA-256 for every numeric artifact;
- figure-script SHA-256 and git commit;
- admitted analysis-spec path and SHA-256;
- exact deterministic reproduction command; and
- a field-level map from panel/series/annotation to source artifact and key,
  table/column, or named function output field.

It would re-hash sources before rendering and write `<stem>.provenance.json`
beside PDF and PNG, then hash both outputs. A wrong source digest is a hard
failure. The provenance tests must include a deliberately wrong digest and show
that export failing, matching the charter's must-fire rule.

## Admission questions

Before implementing this module, the auditor should rule clause by clause on:

1. the 10 pt universal type size and print dimensions;
2. the palette and redundant dash/marker encodings;
3. the rendered-size and clearance thresholds;
4. automatic registration of every text and collision-relevant artist; and
5. the mandatory source manifest and deterministic PDF/PNG metadata.

No publication figure should depend on the module until those clauses receive
`GATE: ADMIT` and the module's positive and must-fire controls are independently
reproduced.
