# adk-mcp-oauth-example

A minimal example of a [Google ADK](https://google.github.io/adk-docs/) agent that talks to a remote **MCP server**, where the MCP server owns the OAuth flow end-to-end. The agent has zero OAuth code — it just calls tools.

This is the **"MCP server as OAuth client"** pattern: the MCP server has its own redirect URI, exchanges authorization codes for tokens, stores tokens in memory, and refreshes them transparently. The agent only sees structured tool responses (`auth_required` or `success`).

## Architecture

```
┌──────────────────┐   MCP / streamable HTTP   ┌──────────────────────┐
│   ADK agent      │ ───────────────────────▶  │   MCP server         │
│   (adk web)      │                           │   (FastMCP)          │
│                  │ ◀───────────────────────  │   :8001/mcp          │
│   No OAuth code  │      tool responses       │                      │
└──────────────────┘                           │   Owns OAuth flow ▼  │
                                               │   :8001/callback     │
                                               └──────────┬───────────┘
                                                          │
                                                          ▼
                                                 Google OAuth + Calendar API
```

## Tools exposed by the MCP server

- `start_google_auth` — returns a Google authorization URL
- `list_calendar_events` — lists upcoming events from the user's primary calendar

## Auth flow

1. Agent calls `list_calendar_events` → MCP server has no token → returns `auth_required`.
2. Agent calls `start_google_auth` → MCP server returns an authorization URL.
3. Agent shows the URL to the user. User signs in.
4. Google redirects to `http://localhost:8001/callback?code=...`.
5. MCP server exchanges the code for a token, stores it in memory.
6. User tells the agent they're done; agent retries `list_calendar_events` → success.

Token refresh happens silently inside the MCP server when the access token expires.

## Prerequisites

- Python 3.10+
- A Google Cloud project with the **Calendar API** enabled
- An OAuth 2.0 **Web Application** client (Console → APIs & Services → Credentials)
  - Authorised redirect URI: `http://localhost:8001/callback`
- `gcloud auth application-default login` (for Vertex AI model access)

## Setup

```bash
git clone https://github.com/<your-username>/adk-mcp-oauth-example.git
cd adk-mcp-oauth-example

python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

pip install -r requirements.txt

cp .env.example .env
# Edit .env and fill in your values
```

## Configuration

Set these in `.env`:

| Variable | Description |
|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | `TRUE` to use Vertex AI for the model |
| `GOOGLE_CLOUD_PROJECT` | Your GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI region (e.g. `global`, `us-central1`) |
| `GOOGLE_OAUTH_CLIENT_ID` | OAuth 2.0 Web Application client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | OAuth 2.0 Web Application client secret |
| `MCP_SERVER_URL` | MCP endpoint URL. Default: `http://localhost:8001/mcp` |
| `MCP_SERVER_PORT` | Port the MCP server listens on. Default: `8001` |
| `MODEL` | (optional) Model name. Default: `gemini-2.5-flash` |

## Running

In one terminal, start the MCP server:

```bash
python mcp_server.py
```

In another terminal, start the agent:

```bash
adk web
```

Open the URL printed by `adk web`, ask *"What's on my calendar?"*, and follow the sign-in link the agent shows you.

## Project layout

```
.
├── README.md
├── requirements.txt
├── mcp_server.py
└── root_agent
    ├── __init__.py
    └── agent.py
```

`root_agent/__init__.py` contains `from . import agent` so `adk web` discovers the agent when run from the project root.

You'll also need a local `.env` (not committed) — see Configuration above.

## Key design decisions

| Decision | Rationale |
|---|---|
| Agent has no OAuth code | The MCP server owns all credential logic. The agent only sees structured tool responses. |
| `stateless_http=True` on FastMCP | Each MCP request is independent; the only persistent state is the in-memory token store. |
| `mcp.run()` directly (not uvicorn) | FastMCP's own server entry point ensures correct transport setup. |
| `@mcp.custom_route` for `/callback` | Adds the OAuth callback alongside the MCP routes without an external web framework. |
| `StreamableHTTPConnectionParams` | The current ADK transport. `SseConnectionParams` is the older SSE-based transport. |
| `access_type=offline` + `prompt=consent` | Forces Google to issue a refresh token so the access token can be silently renewed. |
| In-memory token store | Simple for single-user dev. Replace with Redis or a database for multi-user production. |