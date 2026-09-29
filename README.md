# Public Access for Home Assistant

**Share one Home Assistant dashboard with anyone — read-only, on your own address, with no login.**

[![Open your Home Assistant instance and add this repository in HACS][my-badge]][my-url]
[![validate][validate-badge]][validate-url]

![A Home Assistant dashboard as a visitor sees it: no header, no sidebar, nothing to press](https://publicaccess.dllfpp.cloud/assets/ha-overview.png)

Your solar production for the neighbours, a weather station for the village, a guest screen for a
holiday rental: pick a dashboard, give it an address, share the link. Visitors see it live, exactly
as you designed it, and **cannot change anything** in your home. No account, no token, no app.

- **Your real dashboard** — your theme, your layout, your custom cards.
- **Nothing to press** — every command, save and script is refused before it reaches Home Assistant.
- **Only what you chose** — one view of one dashboard; everything else stays private.

Free 5-day trial, then €2.99/month or €30/year → **[publicaccess.dllfpp.cloud](https://publicaccess.dllfpp.cloud)**

---

## Get started

**1. Install.** Click the button above, or in HACS open *⋮ → Custom repositories*, add
`https://github.com/dllfpp/ha-public-access` with type **Integration**, then install **Public Access**
and restart Home Assistant.

**2. Get a key.** Ask for a free trial key at
[publicaccess.dllfpp.cloud](https://publicaccess.dllfpp.cloud). It arrives by email in a minute.

**3. Set it up.** *Settings → Devices & services → Add integration → Public Access*. The setup
asks four things: your key, the dashboard, the view, and the public address. Then open the address in a
private window to see exactly what your visitors see.

> **Tip:** create a dashboard just for the public, with only what strangers may see. Don't publish
> your main dashboard.

---

## The two things you choose

Everything you publish is described by two names. Here is what each one means, with an example.

Say your Home Assistant is at `https://home.example.com`, and you have a dashboard called
**Energy** with three tabs at the top: *Overview*, *Solar* and *Costs*.

### The view — *what* is public

A dashboard can have several **views**: the tabs along its top. Public Access publishes **exactly
one** of them. The other tabs, and every other dashboard, stay private — they are not even sent to the
visitor's browser.

In the setup you simply pick it from a list, by name: *Solar*.

> Each view also has an address of its own (in the view's settings, *URL*), which is what
> Home Assistant uses to find it — `solar` in this example, shown next to its name in the list.
> A view can only be picked if it has one, except the first view of the dashboard.

### The public path — *where* it is public

The **public path** is the word you add after your Home Assistant address to make the public link.
It is the address you give people:

| You choose | Visitors open |
| --- | --- |
| `solar` | `https://home.example.com/solar` |
| `weather` | `https://home.example.com/weather` |

The setup shows the link with your real address. Use lowercase letters, digits and underscores, with
**no hyphen** — every Home Assistant dashboard address contains a hyphen, so a public path without one
can never clash with a dashboard of yours. Paths Home Assistant already uses (`config`, `energy`,
`history`, …) are refused, to keep your own interface intact.

**So:** the *view* is the content, the *public path* is the link. In the example, the *Solar* tab of
*Energy* is published at `https://home.example.com/solar`.

---

## Is it safe?

A public page on your home server has to be. Two independent locks make it read-only:

1. **Only reading is forwarded.** The visitor's page talks to Public Access, not to Home Assistant, and
   only messages that *read* — states, statistics, the dashboard itself — are passed on. Commands,
   saves, scripts and templates are refused.
2. **A read-only user underneath.** What is passed on runs as a system user in Home Assistant's own
   read-only group, so even a message that slipped through would be refused by Home Assistant.

Only the entities the published view actually shows are visible, and no password or token ever
reaches the visitor's browser. **What is on the view is visible, though:** if the view shows a camera
or a map, visitors see it — so build the public view on purpose.

A crowd cannot overload your instance either: at most 25 visitors are connected at once, and 4 per
address; past that, new visitors are asked to retry in a few seconds.

The code that enforces this is in this repository, so you can read it before trusting it. What
makes Home Assistant's frontend work behind the glass, and has to follow each frontend release,
arrives with your subscription in a package signed by us; the plugin checks the signature before
using it, and runs it only after the checks above, so it can adapt what a visitor sees but never
widen it.
The [`tests/`](tests) folder holds the automated checks GitHub runs on every change and release —
among them, that the integration contains no code able to write to Home Assistant. Home Assistant
never downloads it: HACS installs only `custom_components/public_access`.

---

## Behind Cloudflare or a reverse proxy

**Nothing to configure.** Public Access tells visitors apart by itself: behind Cloudflare it reads
the visitor's address from the `CF-Connecting-IP` header that Cloudflare sets (and only when the
connection really comes from Cloudflare), behind your own reverse proxy from the address that proxy
recorded. Visitor caps and rate limits work out of the box, and the public page never sends a login
that could fail, so it cannot get anyone banned.

**Optional, for your Home Assistant in general.** Unrelated to Public Access: if Home Assistant is
reachable through a proxy it does not trust, it sees every visitor as the proxy, and a few failed
logins from anyone (a stale tab of your own dashboard is enough) ban the proxy for everybody. If you
ever meet random `403: Forbidden`, list your proxies as trusted: on recent versions in **Settings →
System → Network**, on older ones in YAML. With Cloudflare in front, list its ranges too
(<https://www.cloudflare.com/ips/>):

<details><summary>YAML for older Home Assistant versions</summary>

```yaml
http:
  use_x_forwarded_for: true
  trusted_proxies:
    - 172.30.33.0/24        # Home Assistant OS add-ons (NPM, cloudflared), if you use one
    # Cloudflare, when the DNS record is proxied (orange cloud): https://www.cloudflare.com/ips/
    - 173.245.48.0/20
    - 103.21.244.0/22
    - 103.22.200.0/22
    - 103.31.4.0/22
    - 141.101.64.0/18
    - 108.162.192.0/18
    - 190.93.240.0/20
    - 188.114.96.0/20
    - 197.234.240.0/22
    - 198.41.128.0/17
    - 162.158.0.0/15
    - 104.16.0.0/13
    - 104.24.0.0/14
    - 172.64.0.0/13
    - 131.0.72.0/22
    - 2400:cb00::/32
    - 2606:4700::/32
    - 2803:f800::/32
    - 2405:b500::/32
    - 2405:8100::/32
    - 2a06:98c0::/29
    - 2c0f:f248::/32
```

</details>

## How the page is made

The visitor gets **Home Assistant's own frontend**, showing your dashboard live — same theme, same
layout, custom cards and animations included — without header, sidebar or anything to press. Nothing
is redrawn or approximated: it is your dashboard, read-only.

## Options

| Option | Default | What it does |
| --- | --- | --- |
| Serve the public dashboard | on | Off: the address answers "not found", as if never configured |
| Dashboard, view to publish | — | What is public |
| Public path | `public` | Where it is public |
| Ask search engines not to index it | on | Keeps the page out of Google and friends |
| Allowed embedding origins | — | Sites allowed to show the page in a frame (see below) |

Each visitor address is limited to 60 requests a minute.

---

## Something is not right?

**The setup refuses my key.** The message says why. *"This instance has already had its free
trial"* means a trial was already used on this Home Assistant: subscribe with the link in the message
and the same key starts working.

**The address shows "not found".** The integration is switched off in its options, or you changed the
public path: a new path starts working after a Home Assistant restart (a notice reminds you).

**"This dashboard is not available".** The trial or subscription has ended. Your dashboard is not
deleted; subscribing brings the page back.

**My dashboard is not in the list.** Only dashboards saved at least once can be published. Open it,
make any change, save.

**My view is not in the list.** Give it an address: open the view's settings and fill in *URL*.

**The path was refused.** It contains a hyphen or is already used by Home Assistant. Pick another word.

**The page loads forever.** Reload with the cache cleared (Ctrl/Cmd + Shift + R). If it persists,
[open an issue](https://github.com/dllfpp/ha-public-access/issues) with your Home Assistant version.

**Everyone gets `403: Forbidden` now and then.** Home Assistant banned your proxy's address after
failed logins: see the optional note in [Behind Cloudflare or a reverse
proxy](#behind-cloudflare-or-a-reverse-proxy). Remove the proxy's addresses from `ip_bans.yaml`,
restart, and reload any tab still showing the public page from a version before 0.4.2.

**After opening the public page, my own Home Assistant answers 403.** Versions before September 2026
stored the public page's placeholder login in your browser, where your real Home Assistant then found
and rejected it until its login protection blocked the address. Current versions never store it and
clean up what older ones left. To recover: remove the address from `ip_bans.yaml` in your
configuration folder (or restore a backup) and restart; then clear the site data of your Home
Assistant address in any browser that opened the public page.

---

## Price and license

A free **5-day trial** — no card — then **€2.99/month or €30/year** per Home Assistant instance.
Payments, VAT and invoices are handled by Lemon Squeezy. When a trial or subscription ends the public
page stops; nothing is deleted.

The integration's code is **source-available** under [PolyForm Shield 1.0.0](LICENSE): read it, audit
it, run it, modify it for your own use — just don't use it to build a competing product. That is why
it is installed as a HACS *custom repository* rather than from the default store.

Support: [GitHub issues](https://github.com/dllfpp/ha-public-access/issues). Payments, refunds and
personal data: awiteva28@gmail.com.

---

## For the curious

<details>
<summary><b>How the key and the subscription work</b></summary>

The key is checked when you enter it. Once accepted, Home Assistant keeps a signed permission that it
verifies on its own, so your page keeps working if our server is briefly unreachable. Our server only
ever receives the key, a one-way fingerprint of your Home Assistant instance, and version numbers —
nothing about your home. A trial works on one instance; a paid key can move to a new one when you
reinstall.

</details>

<details>
<summary><b>Embedding the page in your own website</b></summary>

Home Assistant forbids framing its pages (`X-Frame-Options: SAMEORIGIN`), and an integration cannot
change that. Drop the header at your reverse proxy, on the public path only — for Nginx or Nginx Proxy
Manager:

```nginx
location /your-path {
    proxy_pass http://homeassistant:8123;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_hide_header X-Frame-Options;
}
```

Then set **Allowed embedding origins** in the options, e.g. `'self' https://www.example.com`.

</details>

<details>
<summary><b>Security details and reporting a vulnerability</b></summary>

The allowlist of forwarded messages is in
[`mirror.py`](custom_components/public_access/mirror.py); the checks on views and the default
dashboard in [`sanitize.py`](custom_components/public_access/sanitize.py). The test suite checks, among other
things, that the package contains no write-capable call, that a missing view publishes nothing rather
than another view, and that the owner's default dashboard never reaches the visitor.

Please report a vulnerability privately through the repository's *Security → Report a
vulnerability*, not in a public issue.

</details>

<details>
<summary><b>Development</b></summary>

```bash
git clone https://github.com/dllfpp/ha-public-access.git
cd ha-public-access
python -m pytest tests -q
```

Copy `custom_components/public_access` into your `config/custom_components/` and restart Home
Assistant to try a change.

</details>

---

<sub>Not affiliated with the Home Assistant project, the Open Home Foundation or Nabu Casa.</sub>

[my-badge]: https://my.home-assistant.io/badges/hacs_repository.svg
[my-url]: https://my.home-assistant.io/redirect/hacs_repository/?owner=dllfpp&repository=ha-public-access&category=integration
[validate-badge]: https://github.com/dllfpp/ha-public-access/actions/workflows/validate.yml/badge.svg
[validate-url]: https://github.com/dllfpp/ha-public-access/actions/workflows/validate.yml
