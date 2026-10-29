# Hestia visual identity

One idea runs through everything: **a fire through its daily cycle**. Hestia kept the hearth fire going, and the
fire doesn't burn out because it's tended. Every image here is a stage of that fire, so a reader learns the
product from the pictures alone.

| Stage | Meaning | Icon | Used for |
|---|---|---|---|
| **Tend** | the working day, a steady flame | `icons/tend.svg` | normal work |
| **Bank** | embers covered with ash for the night | `icons/bank.svg` | `/hestia:shutdown` |
| **Keep** | one ember guarded under the moon | `icons/keep.svg` | the overnight pause |
| **Relight** | sunrise over the hearth | `icons/relight.svg` | the morning recap |
| Spark | striking a light early | `icons/resume.svg` | `/hestia:resume` |
| Read the embers | how warm it's running | `icons/status.svg` | `/hestia:status` |
| The home it warms | who the work is for | `icons/plain.svg` | `/hestia:plain` |
| One piece of kindling | one new thing, not the woodpile | `icons/digest.svg` | `/hestia:digest` |

## Files

| File | Size | Use |
|---|---|---|
| `banner.svg` | 960×300 | top of the README |
| `logo.svg` | 128×128 | avatar, social preview, small placements |
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
