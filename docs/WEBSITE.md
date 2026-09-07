# Project website

The public project site - the marketing landing page and the docs page - is built from
this repo as **plain static files**. No server, no runtime, no database: the output is a
directory of HTML, CSS, JS and media that any file server can hand out. That is what runs
at [third-brain.ai](https://third-brain.ai).

It is the same source as the app's own marketing routes (`apps/web/app/(marketing)`), so
the site and the product can never drift apart.

- [Build it](#build-it)
- [What it contains](#what-it-contains)
- [Serve it anywhere](#serve-it-anywhere)
- [Cloudflare Pages](#cloudflare-pages)
- [How the build works](#how-the-build-works)

---

## Build it

```bash
cd apps/web
npm ci
npm run build:site       # -> apps/web/out
```

Requires Node 20 or newer (the repo builds on Node 22). The output directory is
`apps/web/out` and is gitignored - it is a build artifact, never committed.

One optional setting: `NEXT_PUBLIC_SITE_URL` is the absolute origin used for the
canonical and Open Graph URLs. It defaults to `https://third-brain.ai`, so a fork
publishing under its own domain should set it:

```bash
NEXT_PUBLIC_SITE_URL=https://example.org npm run build:site
```

`npm run build` is untouched by all of this: it still produces the standalone Next server
bundle the Docker image runs.

## What it contains

```
out/
  index.html            landing page
  docs/index.html       documentation page
  404.html              not-found page (also as 404/index.html)
  favicon.ico  icon.svg
  opengraph-image       link-preview card (PNG, no extension - see below)
  media/                the recorded demo (two renditions) and its poster
  _next/static/         hashed CSS, JS and font files
  _headers              per-path response headers, read by Cloudflare Pages and Netlify
```

The dashboard and the auth pages are **deliberately absent**. They are shells around an
API, so publishing them on a site with no API behind it would be a dead end. For the same
reason the site's navbar and footer carry no "Sign in" link - the on-ramp is the
[quick start](https://third-brain.ai/docs#quick-start), which tells you how to run your
own instance. The build fails if any page other than the ones listed above is emitted.

## Serve it anywhere

The output is self-contained and origin-agnostic. Anything that serves files works:

```bash
cd apps/web/out
python3 -m http.server 8000        # or: npx serve .   |   busybox httpd -f -p 8000
```

Then open <http://localhost:8000>. Every route is exported as `<route>/index.html`, so
`/docs` resolves on servers that do no clean-URL rewriting at all.

For nginx, the whole configuration is a root and a directory index:

```nginx
server {
    root /srv/third-brain-site;
    index index.html;

    location / {
        try_files $uri $uri/ $uri/index.html =404;
    }

    # The demo renditions are content-addressed by filename; freeze them.
    location /media/ {
        add_header Cache-Control "public, max-age=31536000, immutable";
    }

    # Next exports the Open Graph card without a file extension, so name its type
    # explicitly or link previews break.
    location = /opengraph-image {
        default_type image/png;
    }

    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header Referrer-Policy strict-origin-when-cross-origin;
    add_header Content-Security-Policy "frame-ancestors 'none'";
}
```

The generated `out/_headers` file expresses exactly those rules in the format Cloudflare
Pages and Netlify read. Hosts that do not understand it just serve it as another file;
the site is correct either way, minus the headers.

## Cloudflare Pages

Connect the GitHub repository to a Pages project and set:

| Setting                 | Value                  |
| ----------------------- | ---------------------- |
| Framework preset        | None                   |
| Root directory          | `apps/web`             |
| Build command           | `npm run build:site`   |
| Build output directory  | `out`                  |
| Environment variable    | `NODE_VERSION` = `22`  |

`apps/web` has its own `package-lock.json`, so Pages installs and builds there without
any monorepo wiring. Nothing else is needed - no secrets, no API keys, no functions. To
publish under a different domain, add `NEXT_PUBLIC_SITE_URL` as a second environment
variable.

For a custom apex domain, add the domain in **Custom domains** on the Pages project;
Cloudflare creates the `CNAME` record for the apex itself (CNAME flattening). Any older
record for the same name - an `A` record or a tunnel `CNAME` from a previous deployment -
must be deleted first, or the site keeps resolving to the dead origin.

## How the build works

`apps/web/scripts/build-site.mjs`, run by `npm run build:site`:

1. Copies `apps/web` into a temp directory, minus `app/(auth)` and `app/dashboard`,
   `node_modules` (symlinked instead), build output and local `.env` files.
2. Runs `next build` there with `STATIC_EXPORT=1`, which switches `next.config.mjs` to
   `output: "export"` plus `trailingSlash: true` and drops the `rewrites()` and
   `headers()` that only a Next server can honour.
3. Checks the exported pages against the allowlist described above, writes `_headers`,
   and copies the result to `apps/web/out`.

Staging a copy is what keeps the two builds independent: the checkout is never modified,
nothing has to be restored afterwards, and an interrupted or concurrent site build cannot
corrupt the tree. `STATIC_EXPORT` also sets `NEXT_PUBLIC_STATIC_SITE`, which is how the
marketing components know to drop the sign-in affordances - in the app build the flag is
empty and those links behave exactly as before.

Adding a page to the site means adding it under `app/(marketing)` and listing its
exported HTML in `PUBLISHED_PAGES` in the build script.
