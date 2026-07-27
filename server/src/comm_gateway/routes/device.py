"""Device-authorization sign-in (RFC 8628) for the agent/CLI onboarding.

Flow:
1. The agent calls ``POST /v1/auth/device/start`` (no auth) and shows the
   developer the returned verification link.
2. The developer opens ``GET /device?code=...``, signs in with Google
   (Supabase Auth) in the browser, which calls ``POST /v1/auth/device/approve``.
3. The agent polls ``POST /v1/auth/device/token`` with the device_code until it
   returns the account's API key. From then on the key lives in ``.env`` and is
   reused — the developer never signs in again for that project.

Everything is hosted on the gateway so the whole flow works against
the gateway host with no separate frontend deploy.
"""

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import get_session, hash_key
from ..crypto import _decrypt, _encrypt
from ..ids import new_id
from ..models import ApiKey, DeviceAuth, utcnow
from .usage import _email_from_session, get_or_create_account

router = APIRouter()

DEVICE_CODE_TTL = timedelta(minutes=15)
POLL_INTERVAL = 5
# Human-typable code alphabet — no 0/O/1/I to avoid confusion.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _base_url(request: Request) -> str:
    """Base URL derived from the request host (matches skill.py), so the sign-in
    link uses whatever domain the agent hit — independent of public_base_url."""
    base = str(request.base_url).rstrip("/")
    if request.headers.get("x-forwarded-proto") == "https" and base.startswith("http://"):
        base = "https://" + base.removeprefix("http://")
    return base


def _user_code() -> str:
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


class StartIn(BaseModel):
    # The agent's current anonymous key, if it already built with one. On sign-in
    # we bind that project to the account so nothing built so far is lost.
    api_key: str | None = None


@router.post("/v1/auth/device/start")
def device_start(
    request: Request,
    body: StartIn | None = None,
    session: Session = Depends(get_session),
):
    """Begin a device-auth flow. No auth required — this is the agent entry."""
    settings = request.app.state.settings
    if not (settings.supabase_url and settings.supabase_anon_key):
        raise HTTPException(status_code=503, detail="Sign-in is not configured")

    link_project_id = None
    link_api_key_enc = None
    if body and body.api_key:
        row = session.execute(
            select(ApiKey).where(ApiKey.key_hash == hash_key(body.api_key))
        ).scalar_one_or_none()
        if row is not None:
            link_project_id = row.project_id
            link_api_key_enc = _encrypt({"api_key": body.api_key})

    user_code = _user_code()
    device_code = secrets.token_urlsafe(32)
    session.add(
        DeviceAuth(
            id=new_id("dev"),
            user_code=user_code,
            device_code=device_code,
            status="pending",
            link_project_id=link_project_id,
            link_api_key_enc=link_api_key_enc,
            expires_at=utcnow() + DEVICE_CODE_TTL,
        )
    )
    session.commit()

    base = _base_url(request)
    return {
        "device_code": device_code,
        "user_code": user_code,
        "verification_uri": f"{base}/device",
        "verification_uri_complete": f"{base}/device?code={user_code}",
        "interval": POLL_INTERVAL,
        "expires_in": int(DEVICE_CODE_TTL.total_seconds()),
    }


class ApproveIn(BaseModel):
    user_code: str
    access_token: str


@router.post("/v1/auth/device/approve")
def device_approve(
    body: ApproveIn, request: Request, session: Session = Depends(get_session)
):
    """Called by the browser page after the developer signs in with Google."""
    settings = request.app.state.settings
    email = _email_from_session(request, body.access_token)

    code = (body.user_code or "").strip().upper()
    row = session.execute(
        select(DeviceAuth).where(DeviceAuth.user_code == code)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown or expired code")
    if row.expires_at < utcnow():
        row.status = "expired"
        session.commit()
        raise HTTPException(status_code=410, detail="This sign-in request expired")

    link_api_key = _decrypt(row.link_api_key_enc)["api_key"] if row.link_api_key_enc else None
    project_id, api_key = get_or_create_account(
        session, email, settings,
        link_project_id=row.link_project_id, link_api_key=link_api_key,
    )
    row.status = "approved"
    row.email = email
    row.project_id = project_id
    row.api_key_enc = _encrypt({"api_key": api_key})
    session.commit()
    return {"ok": True, "email": email}


class TokenIn(BaseModel):
    device_code: str


@router.post("/v1/auth/device/token")
def device_token(
    body: TokenIn, request: Request, session: Session = Depends(get_session)
):
    """Polled by the agent. Returns the API key once the developer has approved."""
    row = session.execute(
        select(DeviceAuth).where(DeviceAuth.device_code == body.device_code)
    ).scalar_one_or_none()
    if row is None:
        return {"status": "not_found"}
    if row.status == "approved" and row.api_key_enc:
        return {
            "status": "approved",
            "api_key": _decrypt(row.api_key_enc)["api_key"],
            "project_id": row.project_id,
            "email": row.email,
            "base_url": _base_url(request),
        }
    if row.expires_at < utcnow():
        if row.status != "expired":
            row.status = "expired"
            session.commit()
        return {"status": "expired"}
    return {"status": "pending", "interval": POLL_INTERVAL}


@router.get("/device", response_class=HTMLResponse)
def device_page(request: Request, code: str = ""):
    """Browser page: sign in with Google, then approve the device."""
    settings = request.app.state.settings
    supabase_url = settings.supabase_url or ""
    supabase_anon = settings.supabase_anon_key or ""
    base = _base_url(request)
    safe_code = "".join(c for c in code if c.isalnum() or c == "-")[:20].upper()
    return HTMLResponse(_PAGE.format(
        supabase_url=supabase_url,
        supabase_anon=supabase_anon,
        base=base,
        code=safe_code,
        dashboard=settings.billing_dashboard_url,
    ))


_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in to Caspian</title>
<style>
  :root{{color-scheme:dark}}
  *{{box-sizing:border-box}}
  body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
    background:#08080a;color:#fafafa;font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
  .card{{width:min(92vw,420px);background:#101013;border:1px solid #26262b;border-radius:16px;
    padding:34px 30px;text-align:center}}
  h1{{font-size:1.35rem;margin:0 0 6px}}
  p{{color:#a1a1aa;margin:6px 0}}
  .code{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;letter-spacing:2px;font-size:1.4rem;
    background:#000;border:1px solid #26262b;border-radius:10px;padding:12px 16px;margin:18px 0;display:inline-block}}
  button{{width:100%;margin-top:14px;padding:13px 18px;border-radius:10px;border:0;cursor:pointer;
    font-size:1rem;font-weight:600;background:#fc2c83;color:#fff}}
  button:disabled{{opacity:.6;cursor:default}}
  .g{{background:#fff;color:#111;display:flex;align-items:center;justify-content:center;gap:10px}}
  .ok{{color:#34d399}} .err{{color:#f87171}}
  .muted{{font-size:.85rem;color:#71717a;margin-top:16px}}
</style></head>
<body>
  <div class="card">
    <h1>Sign in to Caspian</h1>
    <div id="body">
      <p>Confirm this code matches your terminal:</p>
      <div class="code" id="uc">{code}</div>
      <button class="g" id="signin">Sign in with Google</button>
    </div>
  </div>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script>
  var SUPABASE_URL="{supabase_url}", SUPABASE_ANON="{supabase_anon}", BASE="{base}";
  // Keep the code across the Google round-trip so redirectTo stays a clean
  // "/device" (no query string) and matches the Supabase redirect allow list.
  var CODE=new URLSearchParams(location.search).get('code')||localStorage.getItem('caspian_dc')||"{code}";
  if(CODE) localStorage.setItem('caspian_dc', CODE);
  var bodyEl=document.getElementById('body');
  function msg(html){{ bodyEl.innerHTML=html; }}
  if(!SUPABASE_URL||!SUPABASE_ANON){{ msg('<p class="err">Sign-in is not configured.</p>'); }}
  else {{
    var sb=supabase.createClient(SUPABASE_URL, SUPABASE_ANON, {{auth:{{detectSessionInUrl:true,persistSession:true}}}});
    async function approve(token){{
      msg('<p>Finishing sign-in…</p>');
      try{{
        var r=await fetch(BASE+'/v1/auth/device/approve',{{method:'POST',
          headers:{{'Content-Type':'application/json'}},
          body:JSON.stringify({{user_code:CODE, access_token:token}})}});
        var j=await r.json();
        if(r.ok){{ localStorage.removeItem('caspian_dc'); msg('<p class="ok" style="font-size:2rem">✓</p><h1 style="margin:.2em 0">You\\'re signed in</h1><p>Signed in as '+(j.email||'')+'. Taking you to your dashboard to add credit…</p>'); setTimeout(function(){{ location.href="{dashboard}/?section=billing"; }}, 1500); }}
        else {{ msg('<p class="err">'+(j.detail||'Could not complete sign-in')+'</p>'); }}
      }}catch(e){{ msg('<p class="err">Network error. Please try again.</p>'); }}
    }}
    (async function(){{
      var s=(await sb.auth.getSession()).data.session;
      if(s && s.access_token){{ approve(s.access_token); }}
      else {{
        var btn=document.getElementById('signin');
        if(btn) btn.onclick=function(){{
          btn.disabled=true; btn.textContent='Redirecting…';
          sb.auth.signInWithOAuth({{provider:'google',
            options:{{redirectTo: BASE+'/device'}}}});
        }};
      }}
    }})();
  }}
</script>
</body></html>"""
