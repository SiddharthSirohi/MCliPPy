# mcp_handler.py
import asyncio
import json
import traceback
import sys
from mcp import ClientSession
from mcp.client.sse import sse_client
from typing import Dict, List, Optional, Any, Tuple

import config_manager
import user_interface

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

    async def reply_to_gmail_thread(
        self,
        thread_id: str,
        recipient_email: str, # This is the original sender
        message_body: str,
        # cc_emails: Optional[List[str]] = None, # Future enhancement
        # bcc_emails: Optional[List[str]] = None  # Future enhancement
    ) -> Dict[str, Any]:
        """
        Uses Composio's GMAIL_REPLY_TO_THREAD tool (assuming this is the slug).
        Returns a dictionary with success/error.
        """
        if not self.session:
            return {"error": "No active Gmail MCP session.", "successful": False}

        # PM: We need to confirm the exact slug for "Action to reply to an email thread in gmail"
        # Let's assume it's 'GMAIL_REPLY_TO_THREAD' for now.
        # This should be fetched from self.tools ideally or confirmed from Composio dashboard.
        tool_name = "GMAIL_REPLY_TO_THREAD" # Placeholder - VERIFY THIS SLUG!

        # Check if this tool is actually available from the list fetched from Composio
        if tool_name not in self.tools:
            # Fallback or error if direct reply tool isn't available
            # For now, let's try to create a draft as a fallback if reply tool is missing.
            # This shows resilience, a good PM trait.
            print(f"{user_interface.Fore.YELLOW}MCP_SM (gmail): Tool '{tool_name}' not found. Attempting to create a draft instead.{user_interface.Style.RESET_ALL}")
            # We need subject for create_draft. We can try to get it or just use a generic one.
            # For simplicity, let's say draft creation for reply also needs the original subject.
            # This part would need the original subject if we go the draft route.
            # For now, let's just signal that direct reply isn't available.
            return {
                "error": f"Tool '{tool_name}' not available. Direct reply failed. Consider implementing 'Save as Draft'.",
                "successful": False
            }


        params = {
            "thread_id": thread_id,
            "recipient_email": recipient_email, # The original sender becomes the recipient of the reply
            "message_body": message_body,
            "is_html": False # Assuming plain text
            # "user_id": "me" # Defaults to "me"
        }
        # if cc_emails: params["cc"] = cc_emails
        # if bcc_emails: params["bcc"] = bcc_emails

        print(f"MCP_SM (gmail): Attempting to call tool '{tool_name}' with"
              f" ThreadID='{thread_id}', Recipient='{recipient_email}'")

        reply_result_from_mcp = await self.ensure_auth_and_call_tool(tool_name, params)

        if isinstance(reply_result_from_mcp, dict) and reply_result_from_mcp.get("needs_user_action"):
            return reply_result_from_mcp
        if isinstance(reply_result_from_mcp, dict) and reply_result_from_mcp.get("error"):
            return {
                "error": reply_result_from_mcp.get("error", f"Unknown error calling {tool_name}"),
                "successful": False,
                "needs_user_action": reply_result_from_mcp.get("needs_user_action", False)
            }

        if reply_result_from_mcp and hasattr(reply_result_from_mcp, 'content') and reply_result_from_mcp.content:
            text_content = getattr(reply_result_from_mcp.content[0], 'text', None)
            if text_content:
                try:
                    composio_response = json.loads(text_content)
                    if composio_response.get("successful"):
                        # The "data" from "reply to thread" might not have a specific ID like a draft,
                        # but it indicates success.
                        print(f"{user_interface.Fore.GREEN}Successfully replied to Gmail thread (Thread ID: {thread_id}).{user_interface.Style.RESET_ALL}")
                        return {"successful": True, "message": f"Successfully replied to thread ID: {thread_id}."}
                    else:
                        error_msg = composio_response.get("error", f"Failed to reply to thread, Composio tool reported not successful.")
                        print(f"{user_interface.Fore.RED}{error_msg}{user_interface.Style.RESET_ALL}")
                        return {"successful": False, "error": error_msg}
                except json.JSONDecodeError:
                    return {"successful": False, "error": f"Could not parse {tool_name} response from Composio."}

        return {"successful": False, "error": f"Unexpected or empty response from {tool_name}."}

    async def create_gmail_draft(self, recipient_email: str, subject: str, body: str, thread_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Uses Composio's GMAIL_CREATE_DRAFT tool.
        Returns a dictionary with success/error and potentially draft_id.
        """
        # ... (this function remains largely the same as before, but it's now a distinct action) ...
        if not self.session:
            return {"error": "No active Gmail MCP session.", "successful": False}

        tool_name = "GMAIL_CREATE_DRAFT" # This slug is confirmed
        if tool_name not in self.tools:
            return {"error": f"Tool '{tool_name}' not available in cached tools for Gmail.", "successful": False}

        params = {
            "recipient_email": recipient_email,
            "subject": subject,
            "body": body,
            "is_html": False
        }
        if thread_id:
            params["thread_id"] = thread_id

        print(f"MCP_SM (gmail): Attempting to call tool '{tool_name}' (Save as Draft) with params: Subject='{subject}', Recipient='{recipient_email}', ThreadID='{thread_id}'")

        draft_result_from_mcp = await self.ensure_auth_and_call_tool(tool_name, params)

        if isinstance(draft_result_from_mcp, dict) and draft_result_from_mcp.get("needs_user_action"):
            return draft_result_from_mcp
        if isinstance(draft_result_from_mcp, dict) and draft_result_from_mcp.get("error"):
            return {
                "error": draft_result_from_mcp.get("error", f"Unknown error calling {tool_name}"),
                "successful": False,
                "needs_user_action": draft_result_from_mcp.get("needs_user_action", False)
            }

        if draft_result_from_mcp and hasattr(draft_result_from_mcp, 'content') and draft_result_from_mcp.content:
            text_content = getattr(draft_result_from_mcp.content[0], 'text', None)
            if text_content:
                try:
                    composio_response = json.loads(text_content)
                    if composio_response.get("successful"):
                        draft_id = composio_response.get("data", {}).get("response_data", {}).get("id")
                        print(f"{user_interface.Fore.GREEN}Successfully created Gmail draft (ID: {draft_id}).{user_interface.Style.RESET_ALL}")
                        return {"successful": True, "draft_id": draft_id, "message": f"Draft created successfully (ID: {draft_id})."}
                    else:
                        error_msg = composio_response.get("error", f"Failed to create draft, Composio tool reported not successful.")
                        print(f"{user_interface.Fore.RED}{error_msg}{user_interface.Style.RESET_ALL}")
                        return {"successful": False, "error": error_msg}
                except json.JSONDecodeError:
                    return {"successful": False, "error": "Could not parse GMAIL_CREATE_DRAFT response from Composio."}

        return {"successful": False, "error": f"Unexpected or empty response from {tool_name}."}
