# Local webfonts

Downloaded from Google Fonts on 2026-10-23 and served from this directory so the
console never makes a third-party request at runtime.

| File | Family | Source | License |
|---|---|---|---|
| `bricolage-grotesque-latin*.woff2` | Bricolage Grotesque (variable, 400–800) | https://fonts.google.com/specimen/Bricolage+Grotesque | SIL Open Font License 1.1 (`OFL.txt`) |
| `manrope-latin*.woff2` | Manrope (variable, 400–800) | https://fonts.google.com/specimen/Manrope | SIL Open Font License 1.1 (`OFL.txt`) |
| `jetbrains-mono-latin*.woff2` | JetBrains Mono (variable, 400–700) | https://fonts.google.com/specimen/JetBrains+Mono | SIL Open Font License 1.1 (`OFL.txt`) |

Chinese text falls back to the platform UI font (`PingFang SC`, `Noto Sans CJK SC`,
`Microsoft YaHei`) because a full CJK webfont is several MB and not worth the
initial load for a local admin console.
