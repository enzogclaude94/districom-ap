# Brand fonts (docs/BRAND.md)

Served by `next/font/local` from `src/app/layout.tsx`: the build never downloads fonts.

| File                     | Font                                                                             | Source                                                                                                            | License                        |
| ------------------------ | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| `Inter-opsz14.woff2`     | Inter 4.001 (`git-66647c0bb`), variable weight 100–900, optical size fixed at 14 | `ofl/inter/Inter[opsz,wght].ttf` of [google/fonts](https://github.com/google/fonts) (SHA-256 `29160a80…dc559031`) | SIL OFL 1.1, `OFL-Inter.txt`   |
| `Manrope-SemiBold.woff2` | Manrope 4.505, SemiBold 600                                                      | `ofl/manrope/Manrope[wght].ttf` of google/fonts (SHA-256 `3ae11c49…d6ae6`)                                        | SIL OFL 1.1, `OFL-Manrope.txt` |

**Same as Google Fonts served them** (downloaded 2026-11-01): Google Fonts serves Inter as a weight-variable font with
the optical size fixed at 14, and Manrope 600 as a static instance. These files are made the same way from the
official sources, with every glyph kept (Google splits them by script). Checked glyph by glyph against the files
Google Fonts served for the latin subset: identical outlines and advance widths at 400, 500 and 600.

Regenerate (fontTools):

```bash
uvx --from 'fonttools[woff]' python - <<'PY'
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
inter = instancer.instantiateVariableFont(TTFont("Inter[opsz,wght].ttf"), {"opsz": 14})
inter.flavor = "woff2"; inter.save("Inter-opsz14.woff2")
manrope = instancer.instantiateVariableFont(TTFont("Manrope[wght].ttf"), {"wght": 600}, updateFontNames=False)
manrope.flavor = "woff2"; manrope.save("Manrope-SemiBold.woff2")
PY
```

The OFL lets the fonts be bundled and served with the app; it forbids selling them alone and using the reserved
names for modified versions. These files are not modified beyond fixing axes (instancing), which the OFL allows.
