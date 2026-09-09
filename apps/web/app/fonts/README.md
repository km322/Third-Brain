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

The OFL requires its text and copyright notice to travel with every copy of the font
files, and these files are redistributed three ways: in this repository, as
`_next/static/media/*.woff2` on the published site, and inside the web container image.
So each licence lives in `apps/web/public/fonts/` rather than next to the `.woff2` here -
`public/` is the one directory that reaches all three, copied verbatim into the static
export (`out/fonts/`) and into the image. Each is the upstream project's own file, byte
for byte:

| Font           | Licence file                                                                               | Served at                           | Copyright                                   |
| -------------- | ------------------------------------------------------------------------------------------ | ----------------------------------- | ------------------------------------------- |
| Inter          | [`public/fonts/inter-LICENSE.txt`](../../public/fonts/inter-LICENSE.txt)                   | `/fonts/inter-LICENSE.txt`          | (c) 2016 The Inter Project Authors          |
| JetBrains Mono | [`public/fonts/jetbrains-mono-LICENSE.txt`](../../public/fonts/jetbrains-mono-LICENSE.txt) | `/fonts/jetbrains-mono-LICENSE.txt` | (c) 2020 The JetBrains Mono Project Authors |

Replacing a font file means replacing its licence file from the same upstream tag in the
same commit. The OFL is also available with a FAQ at <https://openfontlicense.org>.

## Updating

Fetch the latin-subset variable file the CSS API points at, replacing the family and axis
range as appropriate:

```bash
curl -s -A 'Mozilla/5.0' \
  'https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap'
```

Take the `url(...)` from the `/* latin */` block and save it over the file above. Then run
`npm run build` and confirm the pages still render.
