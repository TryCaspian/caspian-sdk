"""WhatsApp Embedded Signup onboarding.

Three endpoints turn Meta's browser consent popup into a provisioned connection:

  POST /v1/connections/whatsapp/onboarding-session  (API key)  -> signed session + launcher URL
  GET  /connect/whatsapp?session=…                  (browser)  -> the launcher page
  POST /v1/connections/whatsapp/embedded-signup     (session)  -> exchange code, create connection

The session token (see onboarding_token.py) keeps the project's API key out of the
browser: an authenticated call mints it, the launcher echoes it back with the
`code` Meta returned, and the exchange endpoint trusts the token instead of a key.
"""

import html

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import onboarding_token
from ..auth import get_project, get_session
from ..crypto import write_credentials
from ..ids import new_id
from ..jobs import enqueue
from ..models import Connection, Project
from ..providers.meta_onboarding import MetaOnboardingError
from ..schemas import (
    ConnectionOut,
    EmbeddedSignupIn,
    WhatsAppOnboardingSessionCreate,
    WhatsAppOnboardingSessionOut,
)
from ..serialize import connection_out
from .api import _default_scope, _resolve_manifest

router = APIRouter()

_ACTIVE = ["provisioning", "active"]


def _meta_config(request: Request):
    """The configured meta-whatsapp provider + settings, or a 400 if Embedded
    Signup isn't set up on this deployment."""
    settings = request.app.state.settings
    provider = request.app.state.providers.get("meta-whatsapp")
    missing = [
        name
        for name, value in (
            ("COMM_META_APP_ID", settings.meta_app_id),
            ("COMM_META_APP_SECRET", settings.meta_app_secret),
            ("COMM_META_ES_CONFIG_ID", settings.meta_es_config_id),
            ("COMM_PUBLIC_BASE_URL", settings.public_base_url),
        )
        if not value
    ]
    if provider is None or missing:
        raise HTTPException(
            status_code=400,
            detail="WhatsApp Embedded Signup is not configured "
            + (f"(missing {', '.join(missing)})" if missing else "(no meta-whatsapp provider)"),
        )
    return provider, settings


@router.post(
    "/v1/connections/whatsapp/onboarding-session",
    response_model=WhatsAppOnboardingSessionOut,
    status_code=201,
)
def create_onboarding_session(
    request: Request,
    body: WhatsAppOnboardingSessionCreate,
    session: Session = Depends(get_session),
    project: Project = Depends(get_project),
) -> dict:
    _, settings = _meta_config(request)  # 400/503 first if WhatsApp signup isn't configured
    # Gate after confirming availability, before minting the Meta signup launcher:
    # WhatsApp is paid, so require the one-time sign-in (401) and credit (402) up
    # front rather than after the developer clicks through Meta's Embedded Signup.
    from ..billing import ensure_credit

    ensure_credit(request, session, project, "whatsapp")
    customer, agent = _resolve_scope(session, project, body)
    session.commit()  # persist any default customer/agent created above

    ttl = 900
    token = onboarding_token.mint(
        settings.meta_app_secret,
        {
            "project_id": project.id,
            "customer_id": customer.id,
            "agent_id": agent.id,
            "display_name": body.display_name,
            "capabilities": body.capabilities,
        },
        ttl=ttl,
    )
    base = settings.public_base_url.rstrip("/")
    return {
        "session": token,
        "launcher_url": f"{base}/connect/whatsapp?session={token}",
        "expires_in": ttl,
    }


@router.get("/connect/whatsapp", response_class=HTMLResponse)
def whatsapp_launcher(request: Request, session: str = "") -> HTMLResponse:
    provider, settings = _meta_config(request)
    if not session:
        raise HTTPException(status_code=400, detail="Missing session")
    return HTMLResponse(
        _LAUNCHER_HTML.format(
            app_id=html.escape(settings.meta_app_id),
            config_id=html.escape(settings.meta_es_config_id),
            graph_version=html.escape(settings.meta_graph_version),
            session=html.escape(session),
        )
    )


@router.post(
    "/v1/connections/whatsapp/embedded-signup",
    response_model=ConnectionOut,
    status_code=201,
)
def complete_embedded_signup(
    request: Request,
    body: EmbeddedSignupIn,
    session: Session = Depends(get_session),
) -> dict:
    provider, settings = _meta_config(request)
    try:
        claims = onboarding_token.verify(settings.meta_app_secret, body.session)
    except onboarding_token.SessionError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid onboarding session: {exc}") from exc

    project = session.get(Project, claims["project_id"])
    if project is None:
        raise HTTPException(status_code=401, detail="Invalid onboarding session")

    try:
        access_token = provider.exchange_signup_code(
            settings.meta_app_id, settings.meta_app_secret, body.code
        )
    except MetaOnboardingError as exc:
        raise HTTPException(status_code=502, detail=f"Meta code exchange failed: {exc}") from exc

    credentials = {
        "phone_number_id": body.phone_number_id,
        "waba_id": body.waba_id,
        "access_token": access_token,
    }
    return _create_connection(
        session,
        project,
        customer_id=claims["customer_id"],
        agent_id=claims["agent_id"],
        provider_name=provider.name,
        capabilities=_resolve_manifest(provider, claims.get("capabilities")),
        resource_id=body.phone_number_id,
        credentials=credentials,
        display_name=claims.get("display_name"),
    )


def _resolve_scope(session: Session, project: Project, body):
    if body.customer_id is None and body.agent_id is None:
        return _default_scope(session, project)
    if body.customer_id is None or body.agent_id is None:
        raise HTTPException(
            status_code=422, detail="Provide both customer_id and agent_id, or neither"
        )
    from ..models import Agent, Customer

    customer = session.get(Customer, body.customer_id)
    if customer is None or customer.project_id != project.id:
        raise HTTPException(status_code=404, detail="Customer not found")
    agent = session.get(Agent, body.agent_id)
    if agent is None or agent.project_id != project.id:
        raise HTTPException(status_code=404, detail="Agent not found")
    return customer, agent


def _create_connection(
    session, project, *, customer_id, agent_id, provider_name, capabilities,
    resource_id, credentials, display_name,
) -> dict:
    # Idempotent: reuse a live connection for this scope + number.
    existing = session.execute(
        select(Connection).where(
            Connection.project_id == project.id,
            Connection.customer_id == customer_id,
            Connection.agent_id == agent_id,
            Connection.channel == "whatsapp",
            Connection.provider == provider_name,
            Connection.provider_resource_id == resource_id,
            Connection.status.in_(_ACTIVE),
        )
    ).scalars().first()
    if existing is not None:
        return connection_out(existing)

    # One WhatsApp number = one connection, across all projects (inbound routes
    # by phone_number_id, so a shared number would cross-deliver messages).
    taken = session.execute(
        select(Connection).where(
            Connection.provider == provider_name,
            Connection.provider_resource_id == resource_id,
            Connection.status.in_(_ACTIVE),
        )
    ).scalars().first()
    if taken is not None:
        raise HTTPException(
            status_code=409, detail="This WhatsApp number is already connected"
        )

    connection = Connection(
        id=new_id("conn"),
        project_id=project.id,
        customer_id=customer_id,
        agent_id=agent_id,
        channel="whatsapp",
        capabilities=capabilities,
        status="provisioning",
        provider=provider_name,
        provider_resource_id=resource_id,
    )
    write_credentials(connection, credentials)
    session.add(connection)
    enqueue(session, "provision_connection", {"connection_id": connection.id,
                                              "display_name": display_name})
    session.commit()
    return connection_out(connection)


_LAUNCHER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Connect WhatsApp</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 28rem; margin: 4rem auto;
         padding: 0 1rem; color: #111; }}
  button {{ font-size: 1rem; padding: 0.75rem 1.25rem; border: 0; border-radius: 0.5rem;
           background: #25D366; color: #fff; cursor: pointer; }}
  button:disabled {{ background: #9ca3af; cursor: default; }}
  #status {{ margin-top: 1rem; color: #555; white-space: pre-wrap; }}
</style>
</head>
<body>
<h1>Connect your WhatsApp Business account</h1>
<p>You'll grant access in a Meta popup. Nothing else is required.</p>
<button id="start">Connect WhatsApp</button>
<div id="status"></div>
<script>
  const SESSION = "{session}";
  const CONFIG_ID = "{config_id}";
  const APP_ID = "{app_id}";
  const GRAPH_VERSION = "{graph_version}";
  const statusEl = document.getElementById("status");
  const btn = document.getElementById("start");
  let signup = null;

  function say(msg) {{ statusEl.textContent = msg; }}

  window.fbAsyncInit = function () {{
    FB.init({{ appId: APP_ID, autoLogAppEvents: true, xfbml: true, version: GRAPH_VERSION }});
  }};
  (function (d, s, id) {{
    var js, fjs = d.getElementsByTagName(s)[0];
    if (d.getElementById(id)) return;
    js = d.createElement(s); js.id = id;
    js.src = "https://connect.facebook.net/en_US/sdk.js";
    fjs.parentNode.insertBefore(js, fjs);
  }})(document, "script", "facebook-jssdk");

  // Embedded Signup posts the WABA + phone number id via window messages.
  window.addEventListener("message", function (event) {{
    if (!String(event.origin).endsWith("facebook.com")) return;
    try {{
      const data = JSON.parse(event.data);
      if (data.type === "WA_EMBEDDED_SIGNUP" && data.event === "FINISH") {{
        signup = {{ phone_number_id: data.data.phone_number_id, waba_id: data.data.waba_id }};
      }}
    }} catch (e) {{ /* non-JSON messages from the SDK: ignore */ }}
  }});

  btn.addEventListener("click", function () {{
    btn.disabled = true;
    say("Opening Meta…");
    FB.login(function (response) {{
      const code = response.authResponse && response.authResponse.code;
      if (!code) {{
        btn.disabled = false;
        say("Sign-up was cancelled. Try again.");
        return;
      }}
      if (!signup) {{
        btn.disabled = false;
        say("Meta authorized but sent no WhatsApp number. The Embedded Signup "
            + "config may be wrong, or you didn't pick/create a number in the popup.");
        return;
      }}
      say("Finishing setup…");
      fetch("/v1/connections/whatsapp/embedded-signup", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ session: SESSION, code: code,
          phone_number_id: signup.phone_number_id, waba_id: signup.waba_id }}),
      }}).then(function (r) {{
        return r.json().then(function (b) {{ return {{ ok: r.ok, b: b }}; }});
      }})
        .then(function (res) {{
          say(res.ok ? "✅ WhatsApp connected. You can close this window."
                     : "Error: " + (res.b.detail || "could not connect"));
        }})
        .catch(function () {{ btn.disabled = false; say("Network error. Try again."); }});
    }}, {{
      config_id: CONFIG_ID,
      response_type: "code",
      override_default_response_type: true,
      // Without `extras`, FB.login on a WhatsApp config falls back to a plain
      // re-auth (no WABA/number wizard, no WA_EMBEDDED_SIGNUP postMessage).
      // sessionInfoVersion "3" makes Meta run the signup and post back the ids.
      extras: {{ setup: {{}}, featureType: "", sessionInfoVersion: "3" }},
    }});
  }});
</script>
</body>
</html>"""
