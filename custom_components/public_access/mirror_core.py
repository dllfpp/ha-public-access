"""Mirror mode's frontend glue.

The open integration keeps everything that makes the mirror *safe*: which
message types are forwarded at all, the read-only user, the entity and
statistic filters, the single published view, the panel list reduced to the
public dashboard, the owner's default dashboard hidden. It calls this module
only after those filters have run, so nothing here can widen what a visitor
reaches.

What lives here is what makes Home Assistant's own frontend *work* behind the
glass, and what has to follow the frontend release after release:

* the page scripts: a fake token, the websocket steered to the public
  endpoint, the header and sidebar hidden, editing dialogs blocked;
* request rewrites: the dashboard is asked for by its public name and fetched
  under its real one;
* answer rewrites: the "lovelace" alias panel the frontend looks up while
  booting, the viewer presented as a plain, non-admin user;
* subscriptions to events a viewer may not see answered as subscriptions that
  never fire, instead of errors that become unhandled rejections in the page.

The integration checks API_VERSION before using this module.
"""

from __future__ import annotations

import json
import re
from typing import Any

API_VERSION = 1

HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")


# -- the page --------------------------------------------------------------------


def head_script(public_path: str) -> str:
    """Runs before the frontend: fake credentials, and a websocket that comes here.

    The public page shares its origin with the owner's Home Assistant, and so
    its localStorage. Nothing may be written there: a fake token saved in it
    replaced the owner's real login, their Home Assistant then failed to
    authenticate again and again, and ip_ban locked the instance out (403 for
    everyone behind the reverse proxy). The fake token, the default panel and
    the hidden sidebar live only in this page's memory; the frontend reads and
    writes those keys through an in-memory shadow, never the real storage.
    Values left by older versions are removed on the first visit.
    """
    ws_path = f"/{public_path}/ws"
    panel = json.dumps(json.dumps(public_path))
    return (
        "<script>(function(){"
        "var origin=location.origin,S=Storage.prototype,g=S.getItem,s=S.setItem,r=S.removeItem;"
        # Clean what earlier versions persisted in the owner's storage.
        "try{var ls=window.localStorage,t=g.call(ls,'hassTokens');"
        "if(t&&(JSON.parse(t)||{}).access_token==='public')r.call(ls,'hassTokens');"
        f"if(g.call(ls,'defaultPanel')==={panel})r.call(ls,'defaultPanel');"
        "if(g.call(ls,'dockedSidebar')==='\"always_hidden\"')r.call(ls,'dockedSidebar');}catch(e){}"
        # The shadow: these keys never touch the real localStorage.
        "var mem={hassTokens:JSON.stringify({access_token:'public',token_type:'Bearer',"
        "expires_in:1800,hassUrl:origin,clientId:origin+'/',expires:Date.now()+315360000000,"
        f"refresh_token:'public'}}),dockedSidebar:'\"always_hidden\"',defaultPanel:{panel}}};"
        "function mine(o,k){try{return o===window.localStorage&&Object.prototype.hasOwnProperty.call(mem,k);}catch(e){return false;}}"
        "S.getItem=function(k){return mine(this,k)?mem[k]:g.call(this,k);};"
        "S.setItem=function(k,v){if(mine(this,k)){mem[k]=String(v);return;}return s.call(this,k,v);};"
        "S.removeItem=function(k){if(mine(this,k)){return;}return r.call(this,k);};"
        "var W=window.WebSocket;"
        "window.WebSocket=function(u,p){try{var x=new URL(u,origin);"
        f"if(x.pathname==='/api/websocket'){{x.pathname={json.dumps(ws_path)};u=x.toString();}}}}catch(e){{}}"
        "return p===undefined?new W(u):new W(u,p);};"
        "window.WebSocket.prototype=W.prototype;"
        "var F=window.fetch;"
        "window.fetch=function(i,o){var u=typeof i==='string'?i:(i&&i.url)||'';"
        "if(u.indexOf('/auth/token')>=0){return Promise.resolve(new Response("
        "JSON.stringify({access_token:'public',expires_in:1800,token_type:'Bearer'}),"
        "{status:200,headers:{'Content-Type':'application/json'}}));}"
        # History over REST (ApexCharts and friends): to the public endpoint,
        # which answers only for the published view's entities.
        "try{var h=new URL(u,origin);if(h.origin===origin&&h.pathname.indexOf('/api/history/period')===0){"
        f"h.pathname={json.dumps('/' + public_path)}+h.pathname;"
        "var a=Array.prototype.slice.call(arguments);a[0]=typeof i==='string'?h.toString():new Request(h.toString(),i);"
        "return F.apply(this,a);}}catch(e){}"
        "return F.apply(this,arguments);};"
        "})();</script>"
    )


GLASS_SCRIPT = r"""
<script>(function(){
  // Cosmetic layer only: security is enforced server-side. Hides the header
  // and sidebar so the page is the view and nothing else, and blocks the edit
  // and settings dialogs from ever opening.
  function deep(sel, root){root=root||document;var st=[root];while(st.length){var n=st.pop();
    var f=n.querySelector&&n.querySelector(sel);if(f)return f;
    var all=n.querySelectorAll?n.querySelectorAll('*'):[];for(var i=0;i<all.length;i++)if(all[i].shadowRoot)st.push(all[i].shadowRoot);}return null;}
  function hide(e){if(e)e.style.setProperty('display','none','important');}
  function apply(){
    hide(deep('ha-sidebar'));
    var root=deep('hui-root');var sh=root&&root.shadowRoot;
    if(sh){hide(sh.querySelector('.header'));hide(sh.querySelector('.toolbar'));
      var v=sh.querySelector('#view');if(v){v.style.setProperty('padding-top','0','important');v.style.setProperty('margin-top','0','important');}}
  }
  var n=0;var t=setInterval(function(){apply();if(++n>120)clearInterval(t);},250);
  window.addEventListener('show-dialog',function(e){var d=e.detail&&e.detail.dialogTag||'';
    if(/edit|config|settings|search|quick-bar|voice|assist/i.test(d)){e.stopImmediatePropagation();}},true);
  document.addEventListener('keydown',function(e){if(e.key==='e'||e.key==='c'||e.key==='a'||e.key==='m')e.stopImmediatePropagation();},true);
})();</script>
"""


def loading_style(color: str | None) -> str:
    """The launch screen in the owner's colour instead of the frontend's.

    Home Assistant paints its launch screen near-white (#fafafa) unless the
    visitor's device is in dark mode. On a wall screen or a digital signage
    player that flash shows on every reload. Only a validated hex colour is
    accepted, so nothing else can reach the page. On a dark colour the
    attribution switches to the variant the frontend uses in dark mode.
    """
    if not color or not HEX_COLOR.fullmatch(color):
        return ""
    digits = color[1:]
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    r, g, b = (int(digits[i : i + 2], 16) for i in (0, 2, 4))
    css = f"html:root,html:root body #ha-launch-screen{{background-color:{color}}}"
    if 0.2126 * r + 0.7152 * g + 0.0722 * b < 128:
        css += (
            "#ha-launch-screen{color:#e1e1e1}"
            "#ha-launch-screen .ohf-logo img{content:url(/static/images/open-home-foundation-on-dark.svg)}"
        )
    return f"<style>{css}</style>"


def assemble_page(index_html: str, public_path: str, loading_background: str | None = None) -> str:
    """Home Assistant's own index page with the scripts put in place."""
    early = head_script(public_path) + loading_style(loading_background)
    head = index_html.find("<head>")
    if head < 0:
        return early + index_html + GLASS_SCRIPT
    insert = head + len("<head>")
    return index_html[:insert] + early + index_html[insert:] + GLASS_SCRIPT


# -- a visitor's connection --------------------------------------------------------


class Session:
    """Per-connection adaptation, used by the open integration's proxy."""

    def __init__(self, *, public_path: str, dashboard: str) -> None:
        self.public_path = public_path
        self.dashboard = dashboard
        self._silent: set[int] = set()

    def inbound(self, kind: str, msg: dict[str, Any]) -> dict[str, Any]:
        """Rewrite an allowed request before it is forwarded."""
        if kind == "lovelace/config":
            # The frontend asks for the dashboard by its public name.
            return {**msg, "url_path": self.dashboard}
        return msg

    def silent_subscription(self, msg_id: int) -> Any:
        """A subscription to events the viewer may not receive: acknowledged,
        and it simply never fires. Returns the result to send back."""
        self._silent.add(msg_id)
        return None

    def local_answer(self, kind: str, msg: dict[str, Any]) -> tuple[bool, Any]:
        """Answer here instead of forwarding. (answered, result)."""
        if kind == "unsubscribe_events" and msg.get("subscription") in self._silent:
            self._silent.discard(msg.get("subscription"))
            return True, None
        return False, None

    def outbound(self, kind: str, message: dict[str, Any]) -> dict[str, Any]:
        """Adapt an answer the integration has already filtered."""
        result = message.get("result")
        if message.get("type") != "result" or not isinstance(result, dict):
            return message
        if kind == "get_panels" and self.public_path in result:
            # The frontend also looks up its default panel by the fixed name
            # "lovelace" while booting and stops if it is missing: an alias of
            # the same published dashboard.
            alias = {**result[self.public_path], "url_path": "lovelace"}
            message["result"] = {**result, "lovelace": alias}
        elif kind == "auth/current_user":
            result["is_admin"] = False
            result["is_owner"] = False
        return message
