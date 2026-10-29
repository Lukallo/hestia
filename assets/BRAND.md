# Hestia visual identity

One idea runs through everything: **a fire through its daily cycle**. Hestia kept the hearth fire going, and the
fire doesn't burn out because it's tended. Every image here is a stage of that fire, so a reader learns the
product from the pictures alone.

Icons are named after the **stage of the fire**, not the command, so they survive command renames.

| Icon | Stage | Meaning | Command today | Planned name |
|---|---|---|---|---|
| `icons/tend.svg` | Tend | the working day, a steady flame | normal work | |
| `icons/rest.svg` | Rest | the fire turned low at midday | | `/hestia:break` |
| `icons/bank.svg` | Bank | embers covered with ash for the night | `/hestia:shutdown` | `/hestia:done` |
| `icons/keep.svg` | Keep | one ember guarded under the moon | the overnight pause | |
| `icons/relight.svg` | Relight | sunrise over the hearth | the morning handover | |
| `icons/spark.svg` | Spark | striking a light early | `/hestia:resume` | `/hestia:back` |
| `icons/embers.svg` | Embers | reading how warm it's running | `/hestia:status` | `/hestia:today` |
| `icons/home.svg` | Home | the home the fire warms: who the work is for | | `/hestia:why` |
| `icons/kindling.svg` | Kindling | one new thing, not the woodpile | | `/hestia:new` |

## Files

| File | Size | Use |
|---|---|---|
| `banner.svg` | 960×300 | top of the README |
| `logo.svg` | 128×128 | avatar, social preview, small placements |
| `plugins/hestia/.claude-plugin/icon.png` | 1024×1024 | plugin directory listing; `logo.svg` rendered at rest (no flicker) on a full coal square, since the directory doesn't accept SVG |
| `cycle.svg` | 960×330 | "how it works" diagram |
| `meander.svg` | 960×24 | section divider (Greek key) |
| `icons/*.svg` | 64×64 | one per command; show at 40–64 px |

## Palette

| Token | Hex | Role |
|---|---|---|
| Coal | `#1C1714` | every tile background |
| Coal raised | `#2A211C` | tiles inside tiles |
| Coal edge | `#3A2E27` | tile border, hearth ring |
| Ash | `#6B615B` | ash, stone, arrows |
| Ash light | `#A89E97` | body text on coal |
| Ember | `#E8743B` | the flame; primary accent |
| Ember deep | `#C2502A` | a low or guarded flame |
| Gold | `#F4B740` | flame core, sun, highlights, commands |
| Clay | `#A4472B` | kindling, Greek key |
| Cream | `#F6EDE3` | headings, outlines |

## Rules

- **Always on a coal tile.** GitHub has light and dark themes, and a self-contained tile looks the same on both. Never
  put a bare glyph on a transparent background.
- **One flame shape.** All flames use the same path (see `icons/tend.svg`), scaled. Don't draw new ones.
- **Fire colours mean fire.** Ember and gold are only for flame, embers, sun and commands. They're never used for warnings, as
  Hestia has no alerts.
- **Type:** headings are serif (Georgia, the classical side) and body text is the system sans. Commands are in monospace and gold.
- **Motion:** only the flame in `logo.svg` and `banner.svg` moves, as a slow flicker, and it stops for
  `prefers-reduced-motion`. Nothing else animates.
- **No fake output.** Images explain the idea. Terminal output in the README is pasted from a real run, never
  drawn.
