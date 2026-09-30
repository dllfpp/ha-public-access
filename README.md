<div align="center">
  <img src="https://raw.githubusercontent.com/dllfpp/ha-public-access/main/custom_components/public_access/brand/icon@2x.png" width="112" alt="Public Access logo" />
  <h1>Public Access</h1>
  <p><strong>Share one Home Assistant dashboard with anyone — read-only, on your own address, with no login.</strong></p>
  <p>Your real dashboard, live, behind glass: visitors see it exactly as you designed it and cannot change anything.</p>

[![Release](https://img.shields.io/github/v/release/dllfpp/ha-public-access?style=flat-square)](https://github.com/dllfpp/ha-public-access/releases)
[![Validate](https://img.shields.io/github/actions/workflow/status/dllfpp/ha-public-access/validate.yml?branch=main&label=validate&style=flat-square)](https://github.com/dllfpp/ha-public-access/actions/workflows/validate.yml)
[![HACS custom](https://img.shields.io/badge/HACS-custom%20repository-41BDF5?style=flat-square)](https://hacs.xyz/)
![Home Assistant 2026.1+](https://img.shields.io/badge/Home%20Assistant-2026.1%2B-18bcf2?style=flat-square)
[![License MIT](https://img.shields.io/badge/license-MIT-2f855a?style=flat-square)](LICENSE)
![Free](https://img.shields.io/badge/price-free-2f855a?style=flat-square)
[![Donate with PayPal](https://img.shields.io/badge/PayPal-Donate-00457C?style=flat-square&logo=paypal&logoColor=white)](https://www.paypal.me/filippodaelli)
</div>

<img width="1920" height="1200" alt="A credential-free wall screen showing a Home Assistant dashboard, read-only, published with Public Access" src="https://github.com/user-attachments/assets/c942ee39-31ac-41fa-a4ca-4bef4612ecce" />

## One dashboard, for everyone to see

| Your real dashboard | Nothing to press | Only what you chose |
| --- | --- | --- |
| Your theme, your layout, your custom HACS cards, history charts and animations. | Every command, save and script is refused before it reaches Home Assistant. | One view of one dashboard; every other tab and dashboard stays private. |
| **On your own address** | **Nothing to configure** | **Your data stays home** |
| A link like `https://home.example.com/solar`: no account, no token, no app. | Works behind Cloudflare and Nginx Proxy Manager as it is. | Pages are served by your Home Assistant; nothing is sent anywhere else. |

## Why Public Access

- Solar production for the neighbours, a weather station for the village, a guest screen for a holiday rental.
- River levels or noise for a community, a greenhouse or apiary status page, an office status board.
- A credential-free wall display or kiosk: a stolen tablet gives away nothing.
- Visitors get Home Assistant's own frontend, without header, sidebar or anything to press.
- Two independent locks keep it read-only: a read-only message filter and a read-only system user.
- Built-in limits keep a popular page from overloading your instance.

## Install with HACS

Requires **Home Assistant 2026.1 or later**.

[![Open the repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dllfpp&repository=ha-public-access&category=integration)

1. Open the link above, or in HACS open **⋮ → Custom repositories** and add `https://github.com/dllfpp/ha-public-access` with category **Integration**.
2. Download **Public Access** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Public Access**, then pick the dashboard, the view and the public path.
4. Open the public link in a private window to see exactly what your visitors see.

> **Tip:** create a dashboard just for the public, with only what strangers may see. Don't publish your main dashboard.

> **Public Access does not appear under Add integration?** Most often Home Assistant was not restarted after the download, or the repository was added with a category other than **Integration**.

## At a glance

| | |
| --- | --- |
| **What is public** | One view of one dashboard, at a path you choose on your own address |
| **What visitors can do** | Look, switch periods, read tooltips; never press, save or run anything |
| **Visitors at once** | Up to 25, and 4 per address; past that, new visitors are asked to retry |
| **Requests** | 60 a minute per visitor address |
| **Search engines** | Asked not to index the page (can be turned off) |
| **What leaves your home** | Nothing: no account, no license server, no telemetry |
| **Price** | Free, open source (MIT). [Donations welcome](https://www.paypal.me/filippodaelli) |

## The two things you choose

Everything you publish is described by two names. Say your Home Assistant is at `https://home.example.com`, and you have a dashboard called **Energy** with three tabs at the top: *Overview*, *Solar* and *Costs*.

| | What it is | In the example |
| --- | --- | --- |
| **The view** | *What* is public: one of the tabs along the top of a dashboard. The other tabs, and every other dashboard, are not even sent to the visitor's browser. | *Solar* |
| **The public path** | *Where* it is public: the word added after your Home Assistant address to make the link you give people. | `solar` → `https://home.example.com/solar` |

> A view can be picked if it has an address of its own (the view's settings, *URL*), except the first view of the dashboard, which always can.

The public path uses lowercase letters, digits and underscores, with **no hyphen**: every Home Assistant dashboard address contains a hyphen, so a public path without one can never clash with a dashboard of yours. Paths Home Assistant already uses (`config`, `energy`, `history`, …) are refused, to keep your own interface intact.

## Security

- **Only reading is forwarded.** The visitor's page talks to Public Access, not to Home Assistant, and only messages that *read* (states, statistics, the dashboard itself) are passed on. Commands, saves, scripts and templates are refused.
- **A read-only user underneath.** What is passed on runs as a system user in Home Assistant's own read-only group, so even a message that slipped through would be refused by Home Assistant.
- **Only the published entities.** Only the entities the published view shows are visible, and no password or token ever reaches the visitor's browser.
- **Your own login is untouched.** The public page never writes to the browser storage your Home Assistant uses.
- **Auditable.** All the code is in this repository, including the glue that makes Home Assistant's frontend work behind the glass (`mirror_core.py`), which runs only after the checks above, so it can adapt what a visitor sees but never widen it. Nothing is downloaded at runtime.

> **What is on the view is visible, though:** if the view shows a camera or a map, visitors see it. Build the public view on purpose.

The [`tests/`](tests) folder holds the automated checks GitHub runs on every change and release, among them that the integration contains no code able to write to Home Assistant. Home Assistant never downloads it: HACS installs only `custom_components/public_access`.

## Behind Cloudflare or Nginx Proxy Manager

**Nothing to configure.** Public Access tells visitors apart by itself: behind Cloudflare it reads the visitor's address from the `CF-Connecting-IP` header that Cloudflare sets (and only when the connection really comes from Cloudflare), behind your own front server, such as Nginx Proxy Manager, from the address that server recorded. Visitor caps and rate limits work out of the box, and the public page never sends a login that could fail, so it cannot get anyone banned.

**Optional, for your Home Assistant in general.** Unrelated to Public Access: if Home Assistant is reachable through a front server it does not trust (Cloudflare, Nginx Proxy Manager), it sees every visitor as that server, and a few failed logins from anyone (a stale tab of your own dashboard is enough) ban it for everybody. If you ever meet random `403: Forbidden`, mark those servers as trusted: on recent versions in **Settings → System → Network**, on older ones in YAML. With Cloudflare in front, list its ranges too (<https://www.cloudflare.com/ips/>).

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

## Options

| Option | Default | What it does |
| --- | --- | --- |
| Serve the public dashboard | on | Off: the address answers "not found", as if never configured |
| Dashboard, view to publish | — | What is public |
| Public path | `public` | Where it is public |
| Ask search engines not to index it | on | Keeps the page out of Google and friends |
| Allowed embedding origins | — | Sites allowed to show the page in a frame (see [For the curious](#for-the-curious)) |

## Troubleshooting

| Problem | What to do |
| --- | --- |
| **The address shows "not found"** | The integration is switched off in its options, or you changed the public path: a new path starts working after a Home Assistant restart (a notice reminds you). |
| **My dashboard is not in the list** | Only dashboards saved at least once can be published. Open it, make any change, save. |
| **My view is not in the list** | Give it an address: open the view's settings and fill in *URL*. |
| **The path was refused** | It contains a hyphen or is already used by Home Assistant. Pick another word. |
| **The page loads forever** | Reload with the cache cleared (Ctrl/Cmd + Shift + R). If it persists, [open an issue](https://github.com/dllfpp/ha-public-access/issues) with your Home Assistant version. |
| **Everyone gets `403: Forbidden` now and then** | Home Assistant banned your front server's address after failed logins: see [Behind Cloudflare or Nginx Proxy Manager](#behind-cloudflare-or-nginx-proxy-manager). Remove that server's addresses from `ip_bans.yaml`, restart, and reload any tab still showing the public page from a version before 0.4.2. |
| **After opening the public page, my own Home Assistant answers 403** | Versions before September 2026 stored the public page's placeholder login in your browser, where your real Home Assistant then rejected it until its login protection blocked the address. Current versions never store it and clean up what older ones left. Remove the address from `ip_bans.yaml` (or restore a backup) and restart; then clear the site data of your Home Assistant address in any browser that opened the public page. |

## Free and open source

Public Access is **free for everyone**, with no trial, no account and no limits beyond the safety ones above. The code is released under the [MIT license](LICENSE): use it, study it, change it, share it.

> Upgrading from a version before 0.5? Nothing to do: your published dashboard keeps working, the license key you entered is simply no longer used.

## Support the project

If Public Access is useful to you, a donation helps me keep it working with every new Home Assistant release.

[![Donate with PayPal](https://img.shields.io/badge/PayPal-Donate-00457C?style=for-the-badge&logo=paypal&logoColor=white)](https://www.paypal.me/filippodaelli)

## Support and feedback

- [Report a bug or ask for a feature](https://github.com/dllfpp/ha-public-access/issues)
- [Discuss it on the Home Assistant forum](https://community.home-assistant.io/t/public-access-publish-one-dashboard-publicly-read-only-on-your-own-domain/1026819)
- Report a vulnerability privately through the repository's *Security → Report a vulnerability*, never in a public issue
- Anything private: awiteva28@gmail.com

## For the curious

<details>
<summary><b>Embedding the page in your own website</b></summary>

Home Assistant forbids framing its pages (`X-Frame-Options: SAMEORIGIN`), and an integration cannot
change that. Drop the header at the server in front of Home Assistant, on the public path only — for Nginx or Nginx Proxy
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
