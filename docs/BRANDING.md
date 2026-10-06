# Branding

The integration's icon and logo, where they live, and what they must not look like.

## Not an official mark

KSeF Notification is an **unofficial** integration. Its icon and logo deliberately borrow
nothing from the KSeF logo or from any Ministry of Finance mark: no lettering in the badge, no
national colours, no emblem, no typeface taken from either. The only place the word "KSeF"
appears is the wordmark, set in a plain typeface, because it is part of the integration's
name. Anyone changing these images keeps it that way. The README says the same to users.

## Where they live

```
custom_components/ksef_notification/brand/
├── icon.png           256x256    the badge
├── icon@2x.png        512x512
├── logo.png          1300x256    badge + "KSeF Notification" wordmark, dark ink
├── logo@2x.png       2598x512
├── dark_logo.png     1300x256    same logo, light ink, for dark themes
└── dark_logo@2x.png  2598x512
```

They ship **inside the integration**. Since Home Assistant 2026.3 a custom integration carries
its own brand images in a `brand/` folder next to `manifest.json`; Home Assistant serves them
from `/api/brands/integration/ksef_notification/<image>`, ahead of the brands CDN. Nothing has
to be submitted anywhere — the
[home-assistant/brands](https://github.com/home-assistant/brands) repository no longer accepts
custom integrations
([announcement](https://developers.home-assistant.io/blog/2026/02/24/brands-proxy-api)).

HACS's `brands` validation check looks for `custom_components/<domain>/brand/icon.png` before it
falls back to the brands repository, so `.github/workflows/validate.yml` runs that check.

**Known gap:** the HACS *store* listing still fetches icons from the HACS CDN and shows a
placeholder for integrations that ship their own
([hacs/integration#5171](https://github.com/hacs/integration/issues/5171)). Inside Home
Assistant the real icon is shown everywhere.

## The design

A teal badge with a white invoice sheet — folded corner, three text lines, the last one short
like a total — and an amber disc with a bell over its lower right corner. "An invoice" and "a
notification" are the two things the integration is about, and both still read at the 24 pixels
the frontend draws an integration icon at. A ring of badge colour separates the disc from the
sheet so the two shapes do not merge when shrunk.

The wordmark keeps the untranslated name: `manifest.json` carries "KSeF Notification" in every
language, and Home Assistant cannot swap a brand image per language. The Polish name,
"Powiadomienia KSeF", is the `title` in `translations/pl.json`.

Everything is drawn by [`scripts/make_branding.py`](../scripts/make_branding.py) — no image here
is hand-edited, so a colour or a proportion is changed by editing a constant and re-running:

```
python scripts/make_branding.py
```

It needs Pillow (in the dev environment through Home Assistant) and DejaVu Sans Bold for the
wordmark; it prints where it looked if the font is missing.

## The rules these files satisfy

The brand-image specification of the brands repository still describes what Home Assistant's
frontend expects (checked on 2026-10-06); `tests/test_branding.py` checks the committed files:

- **Icons** are square: 256x256, and 512x512 for `@2x`, with transparent corners.
- **Logos** are landscape and their shortest side — the height — is 256, or 512 for `@2x`
  (allowed: 128–256 and 256–512).
- PNG, transparent background, trimmed to the content: no empty margin.
- Nothing is derived from Home Assistant, KSeF or Ministry of Finance artwork; everything is
  drawn from primitives by the script.

Not met: the specification prefers interlaced PNGs, which Pillow cannot write. Nothing rejects
them for it. `dark_icon.png` is absent on purpose: the badge reads on a dark background as it
is; only the wordmark's ink had to change.

## What it costs

About 360 KB, downloaded once per install and per update, read by Home Assistant only when the
frontend asks for an image.
