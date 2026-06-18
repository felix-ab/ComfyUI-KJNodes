# Ideogram Artist Prompt Builder

This fork treats the Ideogram prompt builder as a creative instrument, not a form
with every possible knob exposed. More control is only an upgrade when the control
surface stays legible under real creative pressure.

## Product Goal

Build a pro artist prompt builder for Ideogram 4 that gives direct, predictable
control over:

- composition and bounding boxes
- local color and palette roles
- lens behavior and focus behavior
- lighting, exposure, and color grade
- surface/material texture
- local object intent without over-constraining the whole image

The output must remain plain structured Ideogram JSON. The builder must not add
LLM latency or hidden network steps.

## UX Principles

1. Keep the canvas primary.
   The box editor is the source of truth. Controls should explain and tune the
   composition, not bury it.

2. Progressive disclosure over dense panels.
   A beginner should see simple composition, background, style, and palette
   controls. Power users should be able to open lens, color, surface, and local
   controls only when needed.

3. Prefer named artist concepts over raw prompt text.
   Controls like `portrait telephoto`, `wet neon night`, and `natural skin and
   fabric` are faster and less error-prone than asking users to rewrite long
   paragraphs.

4. Keep every control inspectable.
   Generated text should be visible, copyable, and editable. No silent prompt
   injection.

5. Make failure modes visible.
   The UI should warn about overlapping boxes, extreme aspect ratios, too many
   regions, tiny anatomy boxes, missing background, and overlong prompts.

6. Do not add latency to prompting.
   All default controls should be deterministic local transformations. Optional
   LLM helpers can exist later, but the core builder must be instant.

## Current First Step

This fork adds `Ideogram 4 Artist Controls KJ`, a deterministic companion node
that accepts the JSON output of `Ideogram 4 Prompt Builder KJ` and appends
concise control language into standard Ideogram fields.

It currently supports:

- lens profiles
- color profiles
- surface/material profiles
- optional artist notes
- compact or pretty JSON output

It intentionally does not add new non-standard JSON keys. Instead it augments:

- `style_description.photo`
- `style_description.aesthetics`
- `style_description.lighting`
- `high_level_description`

This is a compatibility-first bridge while the full canvas UX is redesigned.

## Planned UI Redesign

### Canvas Layer

- box list with clear region names and depth order
- visible bbox grid and aspect guides
- per-region lock, duplicate, hide, and solo actions
- anatomy/composition warnings for suspicious boxes

### Color Layer

- global palette roles: highlight, midtone, shadow, accent, material, skin
- per-region palette roles instead of anonymous swatches
- optional hex targets without forcing the user to write JSON manually
- local variation controls: hue drift, saturation limit, highlight warmth

### Lens Layer

- focal length family
- aperture feel
- perspective compression
- focus plane and falloff
- lens softness, bloom, halation, and distortion limits

### Surface Layer

- material presets: skin, fabric, ceramic, chrome, wet pavement, glass, paper
- local texture strength
- over-sharpening and plastic-skin guardrails

### Prompt Health

- token estimate
- repeated instruction detection
- missing background warning
- too many small local boxes warning
- conflicting style/lens/color warnings

## Non-Goals

- A giant prompt wall hidden behind a button.
- Controls that cannot be traced to output JSON.
- LLM-only prompt generation as the default path.
- A node that produces better text but makes the editor slower or harder to use.
