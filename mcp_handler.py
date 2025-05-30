# mcp_handler.py
import asyncio
import json
import traceback
import sys
from mcp import ClientSession
from mcp.client.sse import sse_client

import config_manager

COMPOSIO_AUTH_INIT_TOOL = "COMPOSIO_INITIATE_CONNECTION"

async def call_composio_initiate_connection(session: ClientSession, app_name: str, user_id_for_logging: str):
    # (This function remains the same as the one from my previous response that correctly parsed the redirect_url)
    # ... (ensure it has the robust redirect_url parsing)
    print(f"MCP_HANDLER: Calling {COMPOSIO_AUTH_INIT_TOOL} for app '{app_name}' and user '{user_id_for_logging}'.")
    init_conn_params = {"tool": app_name}
    print(f"  Parameters for {COMPOSIO_AUTH_INIT_TOOL}: {init_conn_params}")
    redirect_url_from_tool = None
    try:
        auth_tool_result = await session.call_tool(COMPOSIO_AUTH_INIT_TOOL, init_conn_params)
        # print(f"--- Result from {COMPOSIO_AUTH_INIT_TOOL} ---") # Optional debug
        if hasattr(auth_tool_result, 'content') and auth_tool_result.content:
            for item in auth_tool_result.content:
                text_content = getattr(item, 'text', '')
                if text_content:
                    try:
                        data = json.loads(text_content)
                        if isinstance(data, dict) and data.get("successful") is True:
                            response_data = data.get("data", {}).get("response_data", {})
                            redirect_url = response_data.get("redirect_url")
                            if redirect_url:
                                redirect_url_from_tool = redirect_url
                                break
                        elif isinstance(data, dict) and data.get("successful") is False and data.get("error"):
                            print(f"MCP_HANDLER: Error from {COMPOSIO_AUTH_INIT_TOOL}: {data.get('error')}")
                    except json.JSONDecodeError:
                         if "https://backend.composio.dev/api/v3/s/" in text_content:
                             # Basic extraction if not clean JSON
                             start_index = text_content.find("https://backend.composio.dev/api/v3/s/")
                             if start_index != -1:
                                 end_index = text_content.find("\"", start_index) # Assuming it's in quotes
                                 if end_index == -1: end_index = text_content.find(" ", start_index) # Or space
                                 if end_index == -1: end_index = len(text_content)
                                 redirect_url_from_tool = text_content[start_index:end_index].strip()
                             break
        if redirect_url_from_tool:
            print(f"\n>>>> ACTION REQUIRED FOR {app_name.upper()} <<<<")
            print(f"To use {app_name}, please open this URL in your browser to authenticate with Google:")
            print(f"  {redirect_url_from_tool}")
            print(f"After authenticating, please RE-RUN THE ASSISTANT.\n")
        else:
            print(f"MCP_HANDLER: Could not find redirectUrl from {COMPOSIO_AUTH_INIT_TOOL} response.")
            print(f"  Raw response: {auth_tool_result}")
        return redirect_url_from_tool
    except Exception as e_auth:
        print(f"MCP_HANDLER: Exception calling {COMPOSIO_AUTH_INIT_TOOL}: {e_auth}")
        traceback.print_exc()
        return None

class McpSessionManager:
    def __init__(self, mcp_base_url: str, user_id: str, app_name: str):
        self.mcp_base_url = mcp_base_url
        self.user_id = user_id
        self.app_name = app_name
        self.full_mcp_url = f"{self.mcp_base_url}&user_id={self.user_id}"
        self._sse_client_cm = None
        self._transport_streams = None
        self.session: ClientSession | None = None
        self.tools = {}

    async def __aenter__(self):
        # (Same as previous correct version with sys.exc_info())
        print(f"MCP_SM ({self.app_name}): Connecting to {self.full_mcp_url}")
        try:
            self._sse_client_cm = sse_client(self.full_mcp_url)
            self._transport_streams = await self._sse_client_cm.__aenter__()
            self.session = ClientSession(self._transport_streams[0], self._transport_streams[1])
            await self.session.__aenter__()
            print(f"MCP_SM ({self.app_name}): Initializing session...")
            await self.session.initialize()
            print(f"MCP_SM ({self.app_name}): Session initialized.")
            await self._list_and_cache_tools() # List tools on connect
            return self
        except Exception as e:
            print(f"MCP_SM ({self.app_name}): Error during __aenter__: {e}")
            traceback.print_exc()
            exc_info = sys.exc_info()
            if self.session: await self.session.__aexit__(*exc_info)
            if self._sse_client_cm: await self._sse_client_cm.__aexit__(*exc_info)
            self.session = None # Ensure session is None if setup fails
            self._sse_client_cm = None
            raise

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # (Same as previous correct version)
        print(f"MCP_SM ({self.app_name}): Closing session...")
        if self.session: await self.session.__aexit__(exc_type, exc_val, exc_tb)
        if self._sse_client_cm: await self._sse_client_cm.__aexit__(exc_type, exc_val, exc_tb)
        print(f"MCP_SM ({self.app_name}): Session closed.")

    async def _list_and_cache_tools(self): # Added this method
        if not self.session: return
        print(f"MCP_SM ({self.app_name}): Listing tools...")
        try:
            tools_response = await self.session.list_tools()
            self.tools = {tool.name: tool for tool in tools_response.tools}
            print(f"MCP_SM ({self.app_name}): Found {len(self.tools)} tools.")
        except Exception as e:
            print(f"MCP_SM ({self.app_name}): Error listing tools: {e}")
            self.tools = {}

    async def ensure_auth_and_call_tool(self, tool_name: str, params: dict):
        if not self.session:
            print(f"MCP_SM ({self.app_name}): No active session for tool '{tool_name}'. Cannot proceed.")
            return {"error": f"No active MCP session for {self.app_name}.", "needs_reconnect": True}

        print(f"MCP_SM ({self.app_name}): Attempting to call tool '{tool_name}' with params {params}...")
        try:
            tool_result = await self.session.call_tool(tool_name, params)

            if hasattr(tool_result, 'content') and tool_result.content:
                            first_content_item_text = getattr(tool_result.content[0], 'text', None) # Ensure default is None
                            data = None # Initialize data
                            if first_content_item_text:
                                try:
                                    data = json.loads(first_content_item_text)
                                except json.JSONDecodeError:
                                    print(f"MCP_SM ({self.app_name}): Content text is not valid JSON: {first_content_item_text[:100]}...")
                                    pass # data remains None or previous value

                            if isinstance(data, dict): # Only proceed if data is a dictionary
                                error_message = data.get("error", "") # error_message will be a string
                                is_successful_false = data.get("successful") is False

                                connection_not_found_err = f"Could not find a connection with app='{self.app_name}' and entity='{self.user_id}'"
                                refresh_token_err_substring = "credentials do not contain the necessary fields need to refresh the access token"
                                google_401_err_substring = "401 Client Error: Unauthorized for url: https://www.googleapis.com"

                                # Ensure error_message is not None before 'in' check, though get with default "" should handle this.
                                # For extra safety:
                                if error_message is None: error_message = ""

                                if error_message == connection_not_found_err \
                                   or refresh_token_err_substring in error_message \
                                   or (is_successful_false and google_401_err_substring in error_message):

                                    print(f"MCP_SM ({self.app_name}): Auth needed or refresh/API call failed for '{tool_name}'. Error snippet: '{error_message[:100]}...'. Initiating connection process.")
                                    redirect_url = await call_composio_initiate_connection(self.session, self.app_name, self.user_id)
                                    # ... (rest of the auth initiation logic) ...
                                    if redirect_url:
                                        return {"error": f"Authentication required for {self.app_name}.", "redirect_url": redirect_url, "needs_user_action": True}
                                    else:
                                        return {"error": f"Auth initiation for {self.app_name} called, but no redirect URL obtained.", "needs_user_action": False, "auth_initiation_failed": True}
                                elif is_successful_false and error_message: # Other Composio reported error
                                    print(f"MCP_SM ({self.app_name}): Composio error during '{tool_name}' call: {error_message}")
                                    return {"error": f"Composio error for {self.app_name}: {error_message}", "composio_error": True}

                        # If no specific auth/Composio error detected in content, assume it's a valid tool result
                            return tool_result

        except Exception as e:
            print(f"MCP_SM ({self.app_name}): Exception calling tool '{tool_name}': {e}")
            traceback.print_exc()
            # Check if it's a known MCP error that might indicate auth issue, though less likely here
            if "Method not found" in str(e) and COMPOSIO_AUTH_INIT_TOOL in str(e): # Highly unlikely
                 print(f"MCP_SM ({self.app_name}): Auth tool itself not found, check MCP server config.")
            return {"error": str(e), "exception": True}
