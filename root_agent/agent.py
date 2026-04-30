"""ADK agent — connects to the calendar MCP server. The MCP server owns OAuth."""

import os

from dotenv import load_dotenv
from google.adk.agents import Agent
from google.adk.tools.mcp_tool import MCPToolset, StreamableHTTPConnectionParams

load_dotenv()

MODEL          = os.environ.get("MODEL", "gemini-2.5-flash")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8001/mcp")

mcp_toolset = MCPToolset(
    connection_params=StreamableHTTPConnectionParams(url=MCP_SERVER_URL)
)

root_agent = Agent(
    name="root_agent",
    model=MODEL,
    tools=[mcp_toolset],
    instruction="""
You are a helpful assistant with access to the user's Google Calendar via a
remote MCP server.

Tools (from the MCP server):
  - list_calendar_events  : lists upcoming calendar events
  - start_google_auth     : returns a Google sign-in URL

WORKFLOW:
  1. When the user asks about their calendar, call list_calendar_events.
  2. If the tool returns status "auth_required", call start_google_auth.
  3. Show the authorization_url to the user and ask them to sign in.
  4. Once they confirm, retry list_calendar_events.
  5. Present events as a readable list with title, date/time, and location.
  6. If an error occurs, explain it clearly.
""",
    description=(
        "Calendar assistant that connects to a remote MCP server. "
        "The MCP server owns the Google OAuth flow."
    ),
)