"""
MCP Server — Google Calendar with OAuth (localhost callback)

Tools:
    start_google_auth    — returns the Google authorization URL
    list_calendar_events — lists upcoming Google Calendar events

Auth flow:
    1. Agent calls list_calendar_events → no token → returns auth_required
    2. Agent calls start_google_auth → gets authorization URL
    3. Agent shows URL to user; user visits it in browser
    4. Google redirects to http://localhost:8001/callback?code=...
    5. MCP server exchanges code for token, stores in memory
    6. Agent retries list_calendar_events → token found → returns events

Running:
    pip install fastmcp httpx google-auth google-api-python-client
    python mcp_server.py

ADK agent .env:
    MCP_SERVER_URL=http://localhost:8001/mcp

Google Cloud Console:
    OAuth client type : Web Application
    Redirect URI      : http://localhost:8001/callback
"""

import json
import os
import urllib.parse
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from starlette.requests import Request
from starlette.responses import HTMLResponse
from mcp.server.fastmcp import FastMCP
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

CLIENT_ID     = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
PORT          = int(os.environ.get("MCP_SERVER_PORT", "8001"))

REDIRECT_URI  = f"http://localhost:{PORT}/callback"
AUTH_URL      = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL     = "https://oauth2.googleapis.com/token"
SCOPES        = ["https://www.googleapis.com/auth/calendar.readonly"]

# ── In-memory token store ─────────────────────────────────────────────────────

SESSION_KEY = "default"
_token_store: dict[str, dict] = {}

# ── FastMCP instance — matches the working pattern exactly ───────────────────

mcp = FastMCP(
    "calendar-mcp-server",
    stateless_http=True,
    json_response=True,
    port=PORT,
    host="0.0.0.0",
)

# ── OAuth helpers ─────────────────────────────────────────────────────────────

def _build_auth_url() -> str:
    params = {
        "client_id":     CLIENT_ID,
        "redirect_uri":  REDIRECT_URI,
        "response_type": "code",
        "scope":         " ".join(SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


async def _exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_URL, data={
            "code":          code,
            "client_id":     CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri":  REDIRECT_URI,
            "grant_type":    "authorization_code",
        })
        resp.raise_for_status()
        return resp.json()


def _get_valid_credentials() -> Credentials | None:
    raw = _token_store.get(SESSION_KEY)
    if not raw:
        return None

    creds = Credentials(
        token=raw.get("access_token"),
        refresh_token=raw.get("refresh_token"),
        token_uri=TOKEN_URL,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        scopes=SCOPES,
    )

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        _token_store[SESSION_KEY] = json.loads(creds.to_json())
        return creds

    _token_store.pop(SESSION_KEY, None)
    return None


# ── MCP tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def start_google_auth() -> dict:
    """
    Starts the Google OAuth flow.

    Returns an authorization URL for the user to visit in their browser.
    After signing in, Google redirects to /callback which completes the
    token exchange automatically. The agent should then retry
    list_calendar_events.
    """
    url = _build_auth_url()
    return {
        "status": "auth_required",
        "authorization_url": url,
        "message": (
            f"Please visit the following URL to authorize Google Calendar access:\n\n"
            f"{url}\n\n"
            "After signing in, the browser will show a success message. "
            "Then retry your original request."
        ),
    }


@mcp.tool()
async def list_calendar_events(max_results: int = 10) -> dict:
    """
    List upcoming events from the user's primary Google Calendar.

    Returns up to max_results upcoming events ordered by start time.
    If not yet authorized, returns auth_required — call start_google_auth first.

    Args:
        max_results: Maximum number of events to return (1-50).
    """
    creds = _get_valid_credentials()
    if creds is None:
        return {
            "status": "auth_required",
            "message": (
                "Google Calendar access is not authorized. "
                "Please call start_google_auth to begin the sign-in flow."
            ),
        }

    try:
        service = build("calendar", "v3", credentials=creds)
        result = (
            service.events()
            .list(
                calendarId="primary",
                maxResults=min(max_results, 50),
                singleEvents=True,
                orderBy="startTime",
                timeMin=datetime.now(timezone.utc).isoformat(),
            )
            .execute()
        )
        events = result.get("items", [])
        return {
            "status": "success",
            "count": len(events),
            "events": [
                {
                    "id":       e["id"],
                    "summary":  e.get("summary", "(no title)"),
                    "start":    e["start"].get("dateTime", e["start"].get("date")),
                    "end":      e["end"].get("dateTime",   e["end"].get("date")),
                    "location": e.get("location", ""),
                }
                for e in events
            ],
        }

    except HttpError as exc:
        if exc.resp.status in (401, 403):
            _token_store.pop(SESSION_KEY, None)
            return {
                "status": "auth_required",
                "message": (
                    "Authorization expired or revoked. "
                    "Please call start_google_auth to sign in again."
                ),
            }
        return {"status": "error", "message": str(exc)}


# ── OAuth callback — registered as a custom route on the FastMCP instance ─────
#
# mcp.custom_route() adds arbitrary HTTP endpoints alongside the MCP protocol
# routes, so mcp.run() can be used directly (no Starlette wrapper needed).

@mcp.custom_route("/callback", methods=["GET"])
async def oauth_callback(request: Request) -> HTMLResponse:
    """
    Handles the Google OAuth redirect:
      GET /callback?code=AUTH_CODE
    Exchanges the code for a token and stores it in memory.
    """
    error = request.query_params.get("error")
    if error:
        return HTMLResponse(
            f"<h2>Authorization failed</h2><p>Error: {error}</p>"
            "<p>You can close this tab and try again.</p>",
            status_code=400,
        )

    code = request.query_params.get("code")
    if not code:
        return HTMLResponse(
            "<h2>Missing authorization code</h2>"
            "<p>No code received from Google. Please try again.</p>",
            status_code=400,
        )

    try:
        token_data = await _exchange_code(code)
        _token_store[SESSION_KEY] = token_data
        return HTMLResponse(
            "<h2>&#x2705; Authorization successful!</h2>"
            "<p>You can close this tab and return to the agent.</p>"
            "<p>Your Google Calendar access has been granted.</p>"
        )
    except httpx.HTTPStatusError as exc:
        return HTMLResponse(
            f"<h2>Token exchange failed</h2><p>{exc.response.text}</p>",
            status_code=500,
        )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"MCP server starting on http://localhost:{PORT}")
    print(f"MCP endpoint  : http://localhost:{PORT}/mcp")
    print(f"OAuth callback: http://localhost:{PORT}/callback")
    print(f"Redirect URI to register: {REDIRECT_URI}")
    mcp.run(transport="streamable-http")