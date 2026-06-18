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

This fork also adds `Ideogram 4 Visual Fingerprint KJ`, a deterministic
reference-translation node. It accepts a visual fingerprint protocol output and
turns it into Ideogram structured JSON without doing camera attribution or hidden
vision analysis.

`Ideogram 4 Artist Controls KJ` currently supports:

- look recipes
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

`Ideogram 4 Visual Fingerprint KJ` supports:

- full paste of a 1-6 visual fingerprint protocol
- separate visual fingerprint, counter-spec, drift risks, prompt, negative
  constraints, and optional shorthand references
- text-to-image and image-to-image control wording
- separate positive structured JSON output and negative constraint output

It maps the protocol into:

- `high_level_description` for the prompt block and preservation intent
- `style_description.aesthetics` for fingerprint, counter-spec, color behavior,
  drift risks, shorthand limits, and avoid text
- `style_description.lighting` for light, shadow, highlight, bloom, contrast,
  humidity, flash, and exposure behavior
- `style_description.photo` for edge behavior, sharpness falloff, texture,
  grain, noise, compression, scan, lens, skin, and material rendering
- `style_description.color_palette` when hex colors are present

The important design rule is that this node does not ask "what camera is this?"
It asks "what observable rendering behavior must survive generation?"

## Look Grammar

The current roadmap is informed by a filmmaker-style benchmark pattern:
separate the look into causes that artists can reason about.

Useful axes:

- capture system: Leica M3/M6 rangefinder, Canon G7X Mark II flash digicam,
  ARRI Alexa daylight cinema frame, phone/raw sensor, compact digital flash
- film or sensor response: Portra 400, CineStill 800T, clean digital cinema,
  2010s compact JPEG, phone computational image
- light behavior: cool diffuse ambient, north-window daylight, tungsten
  practicals, on-camera flash plus sun, bright blown daytime highlights
- process and scan: clean Frontier-style scan, pushed one stop, professional
  drum scan, JPEG flash white balance
- texture policy: natural pores, vellus hair, fabric weave, fine grain, no
  crunchy scan artifacts, no fake scratches, no plastic skin
- local color behavior: shadow hue bias, highlight hue bias, split tone distance,
  halation tied to real light sources, local color covariance instead of broad
  digital tinting

This is why the node now exposes a single `look_recipe` control first. A recipe
should be a coherent capture assumption, not a mood label. For example, `Canon
G7X Mark II flash digicam` implies direct flash, compact-camera perspective,
JPEG color response, saturated summer blues/greens, and no HDR. The separate
lens, color, and surface controls are then overrides for users who need more
specific direction.

## Current Look Recipes

- `Leica M6 clean coral-green editorial`
- `Leica M3 natural rangefinder grit`
- `Canon G7X Mark II flash digicam`
- `ARRI Alexa daylight rolloff`
- `Kodak Portra 400 clean Frontier scan`
- `CineStill 800T tungsten practical`

These recipes intentionally map into normal Ideogram fields rather than adding
custom JSON keys. The output remains inspectable text.

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
