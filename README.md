# Public Access for Home Assistant

[![hacs][hacs-badge]][hacs-url]
[![validate][validate-badge]][validate-url]

**Share one Home Assistant dashboard publicly, read-only, on your own domain.**

You build a dashboard, choose a path, and it becomes readable at
`https://home.example.com/<your-path>` — no account, no login, and no way for a visitor to change
anything in your house.

---

## The problem this solves

Home Assistant puts every dashboard behind the login. That is exactly right for a system that can
unlock your door — but it leaves no way to show a dashboard to someone who should only ever *look*.

There is still no built-in option for it. The usual workarounds are heavy (mirror everything into
InfluxDB and publish Grafana instead) or outright dangerous (hand out a long-lived access token, which
grants full control of the instance to whoever holds it).

Public Access adds one capability and nothing else: **one dashboard, one public path, read-only, with
no credentials anywhere.**

## What people publish with it

Anything you can express as a dashboard. A few examples:

* **Energy and solar production** — show your PV output to neighbours or an energy community.
* **A weather station or air-quality sensor** — publish the readings your neighbourhood actually cares
  about, on your own page instead of someone else's platform.
* **Community and municipal projects** — river levels, noise, temperature in a public building, a
  village's shared monitoring.
* **Farms, greenhouses, apiaries** — soil moisture, tank levels, hive weight, for co-owners or customers.
* **Holiday rentals and B&Bs** — a guest info screen with indoor climate, pool temperature and the
  house rules, opened from a QR code with nothing to log into.
* **Shops, offices, clubs** — an opening-hours and status board, a marina's berth availability, a
  makerspace's machine status, a sports club's court occupancy.
* **Status screens and kiosks** — a wall display or a tablet that must show live data without ever
  holding a credential, so a stolen device gives away nothing.

Long-term statistics, live sensor values, charts and text are all supported, so most "show these
numbers to people" dashboards work. Energy dashboards happen to be very well covered, but they are one
use case, not the point.

<!-- TODO before launch: add screenshots of two or three different published dashboards here. -->

---

## Quick start

1. **HACS → three-dot menu → Custom repositories.** Add `https://github.com/dllfpp/ha-public-access`
   with category **Integration**.
2. Install **Public Access**, then **restart Home Assistant**.
3. **Create the dashboard you want to publish** (Settings → Dashboards → Add dashboard), add your
   cards, and **save it**. A dashboard that has never been saved has no stored configuration and
   cannot be published — the picker will not offer it.
4. **Settings → Devices & Services → Add Integration → Public Access.** Enter your subscription key,
   choose the dashboard and the view, then choose the public path.
5. Open `https://your-home-assistant/<your-path>` **in a private window** to see exactly what the
   public sees.

> While the licence service is being built, any key starting with `DEV-` unlocks the plugin locally.

**Build a dashboard for the public on purpose.** Do not point this at your main dashboard: publish a
dashboard you assembled deliberately, containing only what you are happy for strangers to read.

---

## How it works

```
visitor ──GET /your-path──▶ Home Assistant ──▶ Public Access
                                                │
                                reads your dashboard's stored config (internally)
                                sanitizes it against a whitelist
                                derives an entity + statistic allowlist from what survived
                                serves a static page + read-only JSON, cached
```

Four properties follow from that design:

* **No token exists.** The integration reads Home Assistant from the inside. Nothing is created that
  could be stolen, and no credential is ever sent to a browser.
* **The visitor cannot ask for anything.** The browser may pick a period (`day`, `week`, `month`,
  `year`) and nothing else. It cannot name an entity, a statistic or a date range — every id is
  resolved server-side from your sanitized dashboard. Adding `?statistic_ids=…` to a request changes
  nothing.
* **Only GET exists.** Every other verb returns `405`.
* **One view, published deliberately.** Other views of the same dashboard are dropped, so you can keep
  private views right next to the public one.

---

## Security model

This is an unauthenticated endpoint on your home server, so the sanitizer is the product's real risk
surface. It lives in the open at
[`sanitize.py`](custom_components/public_access/sanitize.py) so you can read it before trusting it, and
it is a whitelist, not a filter:

| Guarantee | How |
| --- | --- |
| Dangerous cards never render | `iframe`, `webpage`, `picture-elements`, `picture-glance`, `map`, `media-control`, `button`, `thermostat`, `light`, … are dropped outright |
| Unknown cards leak nothing | Any card type not on the supported list becomes a neutral placeholder; its configuration is discarded |
| No action can be triggered | `tap_action`, `hold_action`, `service`, `target`, `url`, `navigation_path`, `webhook`, `badges`, … are stripped at every nesting level |
| No stray config escapes | Only keys explicitly allowed for each card type are emitted |
| No hidden entity data escapes | The entity allowlist is derived from the cards that survived sanitizing |
| No sensitive attributes escape | Only `friendly_name`, `unit_of_measurement`, `device_class`, `state_class`, `icon`, `min`, `max`, `step` are published — attribute dictionaries routinely carry latitude/longitude, entity pictures and access tokens, and none of those leave your instance |
| Nothing can be written | `data.py` is the only module that touches Home Assistant and every call in it is a read |

The test suite enforces these as invariants rather than trusting review: it scans the package for any
write-capable call, asserts the public view implements no verb but `GET`, asserts the browser cannot
choose ids, and runs the sanitizer against a deliberately hostile dashboard.

```bash
python -m pytest tests -q
```

**Check what you are publishing at any time.** Settings → Devices & Services → Public Access →
*Download diagnostics* reports the published title, the exact entity and statistic allowlists, and
everything the sanitizer removed.

### Choosing the public path

A route registered by an integration outranks Home Assistant's own frontend routing, so a careless
path such as `config` or `energy` would hide part of your own interface. The integration will not let
that happen:

* reserved paths and any currently registered panel or dashboard are rejected;
* the path must contain **no hyphen**. Home Assistant requires every dashboard `url_path` to contain
  one, so a hyphen-free public path structurally cannot collide with a dashboard of yours — now or in
  the future.

---

## Supported cards

| Category | Cards |
| --- | --- |
| **Text and layout** | `markdown`, `heading`, `grid`, `vertical-stack`, `horizontal-stack` |
| **Current values** | `tile`, `entities`, `glance`, `gauge`, `sensor` |
| **Charts and history** | `statistics-graph`, `history-graph`, `statistic` |
| **Energy** | `energy-usage-graph`, `energy-solar-graph`, `energy-gas-graph`, `energy-water-graph`, `energy-distribution`, `energy-sources-table`, `energy-devices-graph`, `energy-devices-detail-graph`, `energy-self-consumption-gauge`, `energy-grid-neutrality-gauge`, `energy-carbon-consumed-gauge`, `energy-date-selection` |

Both the section and the classic (masonry) dashboard layouts are read. Anything not on the list renders
as a placeholder, so your layout stays honest about what is missing.

**Custom HACS cards are not supported, by design.** A community card expects a live, authenticated
Home Assistant connection with full access — precisely what a public page must never have. Build your
public dashboard from the cards above.

Both the current and the legacy Home Assistant energy configurations are understood, so the energy
cards work whether or not your stored preferences have been migrated to the unified grid schema.

---

## Configuration

| Option | Default | What it does |
| --- | --- | --- |
| Serve the public dashboard | on | Off makes the path answer `404`, as if it had never been configured |
| Dashboard / view path | — | What gets published |
| Public path | `public` | Lowercase letters, digits and underscores; no hyphen |
| Ask search engines not to index | on | Sends `X-Robots-Tag: noindex, nofollow` |
| Include device consumption | on | Off hides the per-device energy breakdown |
| Cache duration | 300 s | How long public responses are cached, so traffic never reaches your recorder |
| Allowed embedding origins | `'self'` | The `Content-Security-Policy: frame-ancestors` value |

Requests are rate-limited to 60 per minute per client IP; beyond that the endpoint answers `429` with
a `Retry-After` header.

---

## Embedding the page in your own website

Home Assistant applies `X-Frame-Options: SAMEORIGIN` to every response *after* this integration runs,
and it cannot be overridden from inside an integration. To embed the dashboard in another site, drop
that header at your reverse proxy and let the integration's CSP govern who may frame the page.

**Nginx / Nginx Proxy Manager** (Advanced → Custom Nginx Configuration), on the public path only:

```nginx
location /your-path {
    proxy_pass http://homeassistant:8123;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Home Assistant's SAMEORIGIN would block embedding; the integration's
    # frame-ancestors policy takes over.
    proxy_hide_header X-Frame-Options;
}
```

Then set **Allowed embedding origins** to the site that will embed it, for example
`'self' https://www.example.com`. Leave it at `'self'` if you do not want the page framed anywhere.

---

## Troubleshooting

**The public URL returns 404.**
Either the integration is disabled in its options, or the public path was changed without restarting.
A route cannot be removed at runtime, so a new path only starts answering after a restart — a repair
notice appears in Home Assistant to remind you.

**"This dashboard is not available".**
The subscription is inactive, expired past its grace period, or no key is configured. Diagnostics show
the exact licence status. Your page keeps working through a licence-server outage: a cached
entitlement is honoured until it expires, then for a grace period on top.

**The dashboard picker is empty.**
No dashboard has a saved configuration yet. Create one, add at least one card, and save it — an
auto-generated dashboard has nothing stored to publish.

**The path was rejected.**
It collides with a Home Assistant panel or one of your dashboards, or it contains a hyphen. See
[Choosing the public path](#choosing-the-public-path); this check is protecting your own interface.

**The charts say "No statistics for this period yet".**
The published cards reference long-term statistics that your recorder has not collected. Only entities
with a `state_class` get statistics; for an energy dashboard, check Settings → Dashboards → Energy first.

**A card shows a placeholder.**
That card type is not supported. See [Supported cards](#supported-cards).

---

## Subscription and licence

Public Access is a commercial product with a monthly subscription. This repository holds the open,
auditable part — the integration, the sanitizer and the public HTTP surface — because asking anyone to
expose an unauthenticated endpoint from a closed binary would not be reasonable. The full renderer is
delivered as a signed payload to active subscribers, and the plugin verifies its signature offline
against a pinned key, so your public page survives a licence-server outage.

The code here is **source-available, not open source**: it is licensed under
[PolyForm Shield 1.0.0](LICENSE). You may read it, audit it, run it, and modify it for your own use —
what you may not do is use it to build a competing product. If you want to do something the licence
does not allow, ask.

Because that is not an OSI-approved licence, this integration is installed as a **HACS custom
repository** (as in [Quick start](#quick-start)) rather than from the HACS default store.

<!-- TODO before launch: pricing page, terms of service, privacy note covering what the heartbeat
     sends. -->

---

## Development

```bash
git clone git@github.com:dllfpp/ha-public-access.git
cd ha-public-access
python -m pytest tests -q          # sanitizer + no-write invariants, no HA install needed
```

The reference environment is a throwaway Home Assistant container seeded with synthetic statistics and
a deliberately hostile dashboard — one containing an `iframe`, a `picture-elements` with a
service-call action, action-carrying badges, stray keys, an unknown custom card and a second private
view — so every release is checked against the payloads a real attacker would look for.

Copy `custom_components/public_access` into your Home Assistant `config/custom_components/` directory
and restart to test a change.

## Reporting a security issue

Please do not open a public issue for a vulnerability in the sanitizer or the public endpoints. Report
it privately through the repository's security advisories so it can be fixed before it is described.

<!-- TODO before launch: enable private vulnerability reporting on the repository. -->

---

<sub>Not affiliated with the Home Assistant project or Nabu Casa.</sub>

[hacs-badge]: https://img.shields.io/badge/HACS-custom-41BDF5.svg
[hacs-url]: https://hacs.xyz
[validate-badge]: https://github.com/dllfpp/ha-public-access/actions/workflows/validate.yml/badge.svg
[validate-url]: https://github.com/dllfpp/ha-public-access/actions/workflows/validate.yml
