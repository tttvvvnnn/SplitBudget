---
name: logo-design
description: "Logo and brand-mark design: 55+ logo styles, 30 color palettes, 25 industry guides searchable offline, design briefs, and optional AI image generation (Gemini, Atlas Cloud, MuAPI). Use when designing a logo, brand mark, bot avatar, app icon concept or visual identity."
argument-hint: "[brand] [context]"
license: MIT
---

# Logo Design

Logo part of the `design` skill from nextlevelbuilder/ui-ux-pro-max-skill (MIT, v2.15.0).
Search and design briefs work offline; generation needs an API key.

Script paths are relative to the directory that contains this SKILL.md. Keep the
working directory at the project root and build the full path from the skill's base
directory, e.g. `python3 .claude/skills/logo-design/scripts/logo/search.py ...`.

## Design Brief (start here)

```bash
python3 scripts/logo/search.py "tech startup modern" --design-brief -p "BrandName"
```

## Search Domains

```bash
python3 scripts/logo/search.py "minimalist clean" --domain style
python3 scripts/logo/search.py "tech professional" --domain color
python3 scripts/logo/search.py "healthcare medical" --domain industry
```

## Generate Logo (optional, needs an API key)

**ALWAYS** use a white background for generated logos. Pass `--output-dir` outside the
repository (or under an ignored path) so generated images are not committed by accident.

```bash
python3 scripts/logo/generate.py --brand "TechFlow" --style minimalist --industry tech
python3 scripts/logo/generate.py --prompt "coffee shop vintage badge" --style vintage
python3 scripts/logo/generate.py --brand "TechFlow" --provider atlas
python3 scripts/logo/generate.py --brand "TechFlow" --provider muapi --muapi-model nano-banana-pro
```

Options: `--style`, `--industry`, `--prompt`, `--provider`, `--atlas-model`, `--muapi-model`, `--batch`, `--output-dir`.

Without an API key, design the logo as hand-written SVG from the brief instead: simple
geometry, works at 16–32px, a monochrome version, and readable on light and dark
backgrounds. Show the options side by side in an HTML preview, using the `ui-ux-pro-max`
skill for palette and typography.

**IMPORTANT:** When scripts fail, try to fix them directly.

## Available Styles

| Category | Styles |
|----------|--------|
| General | Minimalist, Wordmark, Lettermark, Pictorial Mark, Abstract Mark, Mascot, Emblem, Combination Mark |
| Aesthetic | Vintage/Retro, Art Deco, Luxury, Playful, Corporate, Organic, Neon, Grunge, Watercolor |
| Modern | Gradient, Flat Design, 3D/Isometric, Geometric, Line Art, Duotone, Motion-Ready |
| Clever | Negative Space, Monoline, Split/Fragmented, Responsive/Adaptive |

## Color Psychology

| Color | Psychology | Best For |
|-------|------------|----------|
| Blue | Trust, stability | Finance, tech, healthcare |
| Green | Growth, natural | Eco, wellness, organic |
| Red | Energy, passion | Food, sports, entertainment |
| Gold | Luxury, premium | Fashion, jewelry, hotels |
| Purple | Creative, innovative | Beauty, creative, tech |

## Industry Defaults

| Industry | Style | Colors | Typography |
|----------|-------|--------|------------|
| Tech | Minimalist, Abstract | Blues, purples, gradients | Geometric sans |
| Healthcare | Professional, Line Art | Blues, greens, teals | Clean sans |
| Finance | Corporate, Emblem | Navy, gold | Serif or clean sans |
| Food | Vintage Badge, Mascot | Warm reds, oranges | Friendly, script |
| Fashion | Wordmark, Luxury | Black, gold, white | Elegant serif |

## Workflow

1. Generate a design brief → `scripts/logo/search.py --design-brief`
2. Generate logo variations → `scripts/logo/generate.py --brand --style --industry` (or SVG by hand, see above)
3. Show the variations side by side and let the user pick
4. Use the `ui-ux-pro-max` skill for the HTML gallery

## Detailed References

- `references/logo-style-guide.md` — detailed style descriptions
- `references/logo-color-psychology.md` — color meanings and combinations
- `references/logo-prompt-engineering.md` — AI generation prompts

## Setup (generation only)

```bash
export GEMINI_API_KEY="your-key"     # https://aistudio.google.com/apikey
pip install google-genai
export ATLASCLOUD_API_KEY="your-key" # optional Atlas Cloud provider
export MUAPI_API_KEY="your-key"      # optional MuAPI provider
```

`generate.py` also reads keys from `.env` next to this skill, `~/.claude/skills/.env`
or `~/.claude/.env`. Never commit keys.
