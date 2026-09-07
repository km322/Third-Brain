# Vendored fonts

These two files are loaded by `app/layout.tsx` through `next/font/local`.

They are vendored deliberately. The app previously used `next/font/google`, which downloads
the fonts during `next build`. That made every image build depend on reaching
`fonts.googleapis.com`: it fails outright on an air-gapped or egress-restricted network, and
it timed out and failed the release when the arm64 image was built under emulation. Third
Brain is meant to be buildable and runnable on infrastructure you control, so the build must
not need Google.

| File                            | Font           | Version | Subset | Axes            |
| ------------------------------- | -------------- | ------- | ------ | --------------- |
| `inter-variable.woff2`          | Inter          | v20     | latin  | `wght 100..900` |
| `jetbrains-mono-variable.woff2` | JetBrains Mono | v24     | latin  | `wght 100..800` |

Both are the exact latin-subset variable files the Google Fonts CSS API serves, so rendering
is unchanged from the previous `next/font/google` setup.

## Licence

Both fonts are licensed under the SIL Open Font License 1.1, which permits redistribution
alongside this repository's Apache-2.0 source. The OFL is a separate licence covering only
these font files; it does not apply to any other part of Third Brain.

- Inter - Copyright (c) 2016 The Inter Project Authors <https://github.com/rsms/inter>
- JetBrains Mono - Copyright (c) 2020 The JetBrains Mono Project Authors
  <https://github.com/JetBrains/JetBrainsMono>

Full licence text: <https://openfontlicense.org>

## Updating

Fetch the latin-subset variable file the CSS API points at, replacing the family and axis
range as appropriate:

```bash
curl -s -A 'Mozilla/5.0' \
  'https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap'
```

Take the `url(...)` from the `/* latin */` block and save it over the file above. Then run
`npm run build` and confirm the pages still render.
