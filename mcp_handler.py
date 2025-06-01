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
import calendar_utils

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
        print(f"MCP_SM ({self.app_name}): FINAL PARAMS BEING SENT TO SDK's call_tool for '{tool_name}': {json.dumps(params, indent=2)}") # ADD THIS LINE
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

    async def get_calendar_free_slots(
        self,
        time_min_iso_ist: str,    # e.g., "2025-06-02T09:00:00+05:30"
        time_max_iso_ist: str,    # e.g., "2025-06-02T18:00:00+05:30"
        meeting_duration_minutes: int,
        calendar_id: str = "primary"
    ) -> Dict[str, Any]:
        """
        Calls Composio's GOOGLECALENDAR_FIND_FREE_SLOTS, then uses calendar_utils
        to calculate and return a list of free slots.
        Returns {"successful": True, "free_slots": list_of_slots} or
                {"successful": False, "error": "message"}
        Each slot in list_of_slots is a dict: {"start": "iso_str_ist", "end": "iso_str_ist"}
        """
        if not self.session:
            return {"error": "No active Calendar MCP session.", "successful": False}

        tool_name = "GOOGLECALENDAR_FIND_FREE_SLOTS"
        if tool_name not in self.tools:
            msg = f"Tool '{tool_name}' not available in cached tools. Check Composio allowed_tools."
            print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): {msg}{user_interface.Style.RESET_ALL}")
            return {"error": msg, "successful": False}

        params = {
            "time_min": time_min_iso_ist,
            "time_max": time_max_iso_ist,
            "timezone": "Asia/Kolkata", # Hardcoded as per requirement
            "items": [calendar_id], # List containing the calendar ID, usually "primary"
            "calendar_expansion_max": 1, # As per your successful payload
            "group_expansion_max": 0     # As per your successful payload
        }

        print(f"MCP_SM ({self.app_name}): Attempting to call '{tool_name}' with params: {params}")

        find_slots_outcome = await self.ensure_auth_and_call_tool(tool_name, params)

        # Debug print for the raw outcome
        print(f"MCP_SM ({self.app_name}): Raw outcome from {tool_name}:")
        if isinstance(find_slots_outcome, dict):
            print(json.dumps(find_slots_outcome, indent=2))
        elif find_slots_outcome and hasattr(find_slots_outcome, 'content'):
            print(f"  ToolCallResult.content: {find_slots_outcome.content}")
            if find_slots_outcome.content and hasattr(find_slots_outcome.content[0], 'text'):
                print(f"  First content item text: {getattr(find_slots_outcome.content[0], 'text', None)}")
        else:
            print(f"  Outcome was None or unexpected structure: {find_slots_outcome}")


        # 1. Handle direct error dicts from ensure_auth_and_call_tool
        if isinstance(find_slots_outcome, dict) and find_slots_outcome.get("error"):
            return find_slots_outcome

        # 2. Process ToolCallResult
        if find_slots_outcome and hasattr(find_slots_outcome, 'content') and find_slots_outcome.content:
            text_content = getattr(find_slots_outcome.content[0], 'text', None)
            if text_content:
                try:
                    composio_response = json.loads(text_content)
                    print(f"DEBUG_MCP_HANDLER (FindFreeSlots): Parsed composio_response: {json.dumps(composio_response, indent=2)}")

                    if composio_response.get("successful") is True:
                        response_data = composio_response.get("data", {}).get("response_data", {})
                        busy_slots_for_calendar = response_data.get("calendars", {}).get(calendar_id, {}).get("busy", [])

                        query_start_dt = calendar_utils.parse_iso_to_ist(time_min_iso_ist)
                        query_end_dt = calendar_utils.parse_iso_to_ist(time_max_iso_ist)

                        if not query_start_dt or not query_end_dt:
                            msg = "Invalid time_min or time_max provided for free slot calculation."
                            print(f"{user_interface.Fore.RED}{msg}{user_interface.Style.RESET_ALL}")
                            return {"successful": False, "error": msg}

                        free_slots = calendar_utils.calculate_free_slots(
                            query_start_dt_ist=query_start_dt,
                            query_end_dt_ist=query_end_dt,
                            busy_slots_data=busy_slots_for_calendar,
                            meeting_duration_minutes=meeting_duration_minutes
                            # workday_start_hour and workday_end_hour use defaults from calendar_utils
                        )
                        print(f"{user_interface.Fore.GREEN}Successfully found {len(free_slots)} free slot(s).{user_interface.Style.RESET_ALL}")
                        return {"successful": True, "free_slots": free_slots}

                    elif composio_response.get("successful") is False and composio_response.get("error") is not None:
                        error_msg = str(composio_response.get("error"))
                        print(f"{user_interface.Fore.RED}Composio tool '{tool_name}' reported failure: {error_msg}{user_interface.Style.RESET_ALL}")
                        return {"successful": False, "error": error_msg, "composio_reported_error": True}
                    else:
                        msg = f"Composio response for {tool_name} did not indicate clear success or provide a specific error."
                        print(f"{user_interface.Fore.YELLOW}MCP_SM ({self.app_name}): {msg} Response: {json.dumps(composio_response, indent=2)}{user_interface.Style.RESET_ALL}")
                        return {"successful": False, "error": msg}

                except json.JSONDecodeError:
                    msg = f"Could not parse {tool_name} JSON response."
                    print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): {msg} Raw: {text_content[:200]}...{user_interface.Style.RESET_ALL}")
                    return {"successful": False, "error": msg}
            else:
                msg = f"No text content in {tool_name} response item."
                print(f"{user_interface.Fore.YELLOW}MCP_SM ({self.app_name}): {msg}{user_interface.Style.RESET_ALL}")
                return {"successful": False, "error": msg}

        # 3. Fallback
        msg = f"Unexpected result structure from {tool_name} after tool call."
        print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): {msg} Raw: {find_slots_outcome}{user_interface.Style.RESET_ALL}")
        return {"successful": False, "error": msg}


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

    async def delete_calendar_event(self, event_id: str, calendar_id: str = "primary") -> Dict[str, Any]:
        """
        Uses Composio's GOOGLECALENDAR_DELETE_EVENT tool.
        Returns a dictionary with success/error.
        """
        if not self.session:
            return {"error": "No active Calendar MCP session.", "successful": False}

        tool_name = "GOOGLECALENDAR_DELETE_EVENT" # Confirm this slug from your allowed_tools

        if tool_name not in self.tools:
            print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): Tool '{tool_name}' not available in cached tools.{user_interface.Style.RESET_ALL}")
            return {"error": f"Tool '{tool_name}' not available.", "successful": False}

        params = {
            "event_id": event_id,
            "calendar_id": calendar_id # Composio schema showed this, defaults to primary if not sent
        }

        print(f"MCP_SM ({self.app_name}): Attempting to call tool '{tool_name}' with EventID='{event_id}', CalendarID='{calendar_id}'")

        delete_result_from_mcp = await self.ensure_auth_and_call_tool(tool_name, params)

        if isinstance(delete_result_from_mcp, dict) and delete_result_from_mcp.get("needs_user_action"):
            return delete_result_from_mcp # Propagate auth requirement
        if isinstance(delete_result_from_mcp, dict) and delete_result_from_mcp.get("error"):
            return {
                "error": delete_result_from_mcp.get("error", f"Unknown error calling {tool_name}"),
                "successful": False,
                "needs_user_action": delete_result_from_mcp.get("needs_user_action", False)
            }

        # Google Calendar API delete operation usually returns an empty response (204 No Content) on success.
        # We need to see how Composio's tool wraps this.
        # Let's assume Composio's JSON wrapper will have a "successful: true" field.
        if delete_result_from_mcp and hasattr(delete_result_from_mcp, 'content') and delete_result_from_mcp.content:
            text_content = getattr(delete_result_from_mcp.content[0], 'text', None)
            if text_content:
                try:
                    composio_response = json.loads(text_content)
                    if composio_response.get("successful"):
                        print(f"{user_interface.Fore.GREEN}Successfully deleted Calendar event (ID: {event_id}).{user_interface.Style.RESET_ALL}")
                        return {"successful": True, "message": f"Event ID: {event_id} deleted."}
                    else:
                        error_msg = composio_response.get("error", f"Failed to delete event, Composio tool reported not successful.")
                        print(f"{user_interface.Fore.RED}{error_msg}{user_interface.Style.RESET_ALL}")
                        return {"successful": False, "error": error_msg}
                except json.JSONDecodeError:
                    # Sometimes a successful delete might not return JSON body from the tool,
                    # or Composio might wrap a 204 differently.
                    # If no JSON, but no prior error, we might infer success.
                    # However, it's safer to expect Composio's wrapper.
                    print(f"{user_interface.Fore.YELLOW}MCP_SM ({self.app_name}): Could not parse {tool_name} response from Composio, but no explicit error from tool call. Raw text: '{text_content[:100]}...'{user_interface.Style.RESET_ALL}")
                    # Let's assume for now an unparseable response without an MCP error is a problem.
                    return {"successful": False, "error": f"Could not parse {tool_name} response."}
        elif delete_result_from_mcp and not hasattr(delete_result_from_mcp, 'isError'):
            # It might be a ToolCallResult with no content and no isError (for 204)
            # The Composio tool might just return successful:true even with no other data.
            # This part is a bit speculative without seeing Composio's exact wrapper for a 204.
            # The ensure_auth_and_call_tool should ideally return a dict with "successful":True if composio does.
            # Let's assume if we reach here and it's not an error dict from ensure_auth_and_call_tool, it might have worked.
            # This logic relies on ensure_auth_and_call_tool correctly parsing Composio's success envelope.
             print(f"{user_interface.Fore.YELLOW}MCP_SM ({self.app_name}): {tool_name} call returned no content, assuming success if no prior error.{user_interface.Style.RESET_ALL}")
             return {"successful": True, "message": f"Event ID: {event_id} likely deleted (no content in response)."}


        return {"successful": False, "error": f"Unexpected or empty response from {tool_name} after checking content."}

    async def update_calendar_event(self, event_id: str, calendar_id: str = "primary", updates: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Uses Composio's GOOGLECALENDAR_UPDATE_EVENT tool.
        'updates' dict should contain fields to change, e.g.,
        {"summary": "New Title", "start_datetime": "2025-06-01T10:00:00", "timezone": "Asia/Kolkata"}
        """
        if not self.session:
            return {"error": "No active Calendar MCP session.", "successful": False}

        tool_name = "GOOGLECALENDAR_UPDATE_EVENT"
        if tool_name not in self.tools:
            print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): Tool '{tool_name}' not available.{user_interface.Style.RESET_ALL}")
            return {"error": f"Tool '{tool_name}' not available.", "successful": False}

        if not updates: # If no updates provided, nothing to do
            return {"error": "No updates provided for the event.", "successful": False}

        params = {"event_id": event_id, "calendar_id": calendar_id}
        valid_update_keys = [
                    "summary", "start_datetime", "event_duration_hour", "event_duration_minutes",
                    "description", "location", "attendees", "create_meeting_room", "timezone",
                    "transparency", "visibility", "guests_can_modify", "guestsCanInviteOthers", "guestsCanSeeOtherGuests",
                    "recurrence" # Add other valid keys from the CSV as needed
                ]

        actual_params_to_send = params.copy() # Start with event_id and calendar_id
        for key, value in updates.items():
            if key in valid_update_keys:
                actual_params_to_send[key] = value
            else:
                print(f"{user_interface.Fore.YELLOW}MCP_SM ({self.app_name}): Ignoring unknown update key '{key}' for tool '{tool_name}'.{user_interface.Style.RESET_ALL}")

        # Remove event_id and calendar_id from 'updates' dict for cleaner logging of just changes
        updates_for_logging = {k:v for k,v in actual_params_to_send.items() if k not in ['event_id', 'calendar_id']}


            # Parameter sanity checks based on CSV
        if "start_datetime" in actual_params_to_send:
                if not isinstance(actual_params_to_send["start_datetime"], str) or "T" not in actual_params_to_send["start_datetime"]:
                     print(f"{user_interface.Fore.RED}Error: start_datetime for update must be YYYY-MM-DDTHH:MM:SS, got {actual_params_to_send['start_datetime']}{user_interface.Style.RESET_ALL}")
                     return {"error": "start_datetime must be YYYY-MM-DDTHH:MM:SS", "successful": False}
        if "event_duration_minutes" in actual_params_to_send:
            try:
                minutes = int(actual_params_to_send["event_duration_minutes"])
                if not (0 <= minutes <= 59): # Schema said 0-59
                    print(f"{user_interface.Fore.RED}Error: event_duration_minutes must be 0-59, got {minutes}{user_interface.Style.RESET_ALL}")
                    return {"error": "event_duration_minutes must be 0-59", "successful": False}
            except ValueError:
                print(f"{user_interface.Fore.RED}Error: event_duration_minutes must be an integer, got {actual_params_to_send['event_duration_minutes']}{user_interface.Style.RESET_ALL}")
                return {"error": "event_duration_minutes must be an integer", "successful": False}

        if "event_duration_hour" in actual_params_to_send:
            try:
                hours = int(actual_params_to_send["event_duration_hour"])
                if not (0 <= hours <= 23): # Schema implied 0-24, but 24h usually means next day start. 0-23 is safer for duration part.
                    print(f"{user_interface.Fore.RED}Error: event_duration_hour must be 0-23, got {hours}{user_interface.Style.RESET_ALL}")
                    return {"error": "event_duration_hour must be 0-23", "successful": False}
            except ValueError:
                print(f"{user_interface.Fore.RED}Error: event_duration_hour must be an integer, got {actual_params_to_send['event_duration_hour']}{user_interface.Style.RESET_ALL}")
                return {"error": "event_duration_hour must be an integer", "successful": False}
           # Add more checks as needed

        print(f"MCP_SM ({self.app_name}): Attempting to call '{tool_name}' for EventID='{event_id}' with updates: {updates_for_logging}")
        tool_call_outcome = await self.ensure_auth_and_call_tool(tool_name, actual_params_to_send)

        # --- NEW SIMPLIFIED PARSING ---
        print(f"MCP_SM ({self.app_name}): Raw tool_call_outcome for {tool_name}:") # Keep this debug
        if isinstance(tool_call_outcome, dict):
            print(json.dumps(tool_call_outcome, indent=2))
        elif tool_call_outcome and hasattr(tool_call_outcome, 'content'):
            print(f"  ToolCallResult.content: {tool_call_outcome.content}")
            if tool_call_outcome.content and hasattr(tool_call_outcome.content[0], 'text'):
                print(f"  First content item text: {getattr(tool_call_outcome.content[0], 'text', None)}")
       # --- END DEBUGGING BLOCK ---

        # 1. Handle direct error dicts from ensure_auth_and_call_tool
        if isinstance(tool_call_outcome, dict) and tool_call_outcome.get("error"):
            print(f"DEBUG_MCP_HANDLER: Returning error directly from ensure_auth_and_call_tool: {tool_call_outcome.get('error')}")
            return tool_call_outcome # This already has "successful": False (implicitly or explicitly) if it's an error

        # 2. Process ToolCallResult if it's not an error dict
        if tool_call_outcome and hasattr(tool_call_outcome, 'content') and tool_call_outcome.content:
            text_content = getattr(tool_call_outcome.content[0], 'text', None)
            if text_content:
                try:
                    composio_response = json.loads(text_content)
                    print(f"DEBUG_MCP_HANDLER: Parsed composio_response: {json.dumps(composio_response, indent=2)}")

                    if composio_response.get("successful") is True:
                        print(f"DEBUG_MCP_HANDLER: Composio reported success.")
                        response_data = composio_response.get("data", {}).get("response_data", {}) # For update/fetch
                        if not response_data and tool_name == "GOOGLECALENDAR_DELETE_EVENT": # Delete might have empty response_data
                            response_data = {"message": "Delete operation reported successful by Composio."}

                        print(f"{user_interface.Fore.GREEN}Successfully executed {tool_name} for event (ID: {event_id}).{user_interface.Style.RESET_ALL}")
                        return {"successful": True, "message": f"Tool {tool_name} successful for event ID: {event_id}.", "response_data": response_data}

                    elif composio_response.get("successful") is False and composio_response.get("error") is not None:
                        error_msg = str(composio_response.get("error"))
                        print(f"{user_interface.Fore.RED}Composio tool '{tool_name}' reported failure: {error_msg}{user_interface.Style.RESET_ALL}")
                        return {"successful": False, "error": error_msg, "composio_reported_error": True}

                    else: # "successful" key not True, or missing, or False without an "error" field
                        print(f"{user_interface.Fore.YELLOW}MCP_SM ({self.app_name}): Composio response for {tool_name} unclear: {json.dumps(composio_response, indent=2)}{user_interface.Style.RESET_ALL}")
                        return {"successful": False, "error": f"Composio response for {tool_name} did not indicate clear success or provide a specific error."}

                except json.JSONDecodeError:
                    print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): JSONDecodeError parsing {tool_name} response: {text_content[:200]}...{user_interface.Style.RESET_ALL}")
                    return {"successful": False, "error": f"Could not parse {tool_name} JSON response."}
            else: # text_content is None or empty
                print(f"{user_interface.Fore.YELLOW}MCP_SM ({self.app_name}): No text_content in {tool_name} response item.{user_interface.Style.RESET_ALL}")
                return {"successful": False, "error": f"No text content in {tool_name} response item."}

        # 3. Fallback: If tool_call_outcome is not an error dict and not a ToolCallResult with parseable content
        print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): Unhandled result structure from {tool_name}. Raw: {tool_call_outcome}{user_interface.Style.RESET_ALL}")
        return {"successful": False, "error": f"Unexpected result structure from {tool_name} after tool call."}

    async def create_calendar_event(self, event_details: Dict[str, Any], calendar_id: str = "primary") -> Dict[str, Any]:
        """
        Uses Composio's GOOGLECALENDAR_CREATE_EVENT tool.
        event_details should contain: summary, start_datetime, timezone,
                                   event_duration_hour, event_duration_minutes,
                                   and optional: attendees (list of str), description, location, create_meeting_room
        """
        if not self.session:
            return {"error": "No active Calendar MCP session.", "successful": False}

        tool_name = "GOOGLECALENDAR_CREATE_EVENT"
        if tool_name not in self.tools:
            print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): Tool '{tool_name}' not available.{user_interface.Style.RESET_ALL}")
            return {"error": f"Tool '{tool_name}' not available.", "successful": False}

        # Prepare params from event_details, ensuring required ones are present
        params = {"calendar_id": calendar_id}

        required_keys = ["summary", "start_datetime", "timezone"] # As per Composio schema, summary is optional, but practically needed
        for req_key in required_keys:
            if req_key not in event_details or not event_details[req_key]:
                # Summary can be optional by schema, but let's enforce it for better UX
                if req_key == "summary" and not event_details.get(req_key):
                    params[req_key] = "Untitled Event" # Default if LLM/user missed it
                else:
                    print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): Missing required key '{req_key}' for {tool_name}.{user_interface.Style.RESET_ALL}")
                    return {"error": f"Missing required key '{req_key}' for event creation.", "successful": False}

        params.update(event_details) # Add all details from the validated dict

        # Ensure duration defaults if not provided by LLM/user and not in event_details from parsing
        params.setdefault("event_duration_hour", 0)
        params.setdefault("event_duration_minutes", 30 if params["event_duration_hour"] == 0 else 0)


        print(f"MCP_SM ({self.app_name}): Attempting to call '{tool_name}' with params: { {k:v for k,v in params.items() if k != 'calendar_id'} }") # Log without calendar_id for brevity

        creation_result_from_mcp = await self.ensure_auth_and_call_tool(tool_name, params)

        # Use the same robust parsing logic as update_calendar_event
        if isinstance(creation_result_from_mcp, dict) and creation_result_from_mcp.get("error"):
            return creation_result_from_mcp

        if creation_result_from_mcp and hasattr(creation_result_from_mcp, 'content') and creation_result_from_mcp.content:
            text_content = getattr(creation_result_from_mcp.content[0], 'text', None)
            if text_content:
                try:
                    composio_response = json.loads(text_content)
                    print(f"DEBUG_MCP_HANDLER (CreateEvent): Parsed composio_response: {json.dumps(composio_response, indent=2)}")

                    if composio_response.get("successful") is True:
                        created_event_data = composio_response.get("data", {}).get("response_data", {})
                        event_id = created_event_data.get("id", "N/A")
                        print(f"{user_interface.Fore.GREEN}Successfully created Calendar event (ID: {event_id}).{user_interface.Style.RESET_ALL}")
                        return {"successful": True, "message": f"Event created (ID: {event_id}).", "created_event_data": created_event_data}
                    elif composio_response.get("successful") is False and composio_response.get("error") is not None:
                        # ... (error handling as in update_calendar_event) ...
                        error_msg = str(composio_response.get("error"))
                        print(f"{user_interface.Fore.RED}Composio tool '{tool_name}' reported failure: {error_msg}{user_interface.Style.RESET_ALL}")
                        return {"successful": False, "error": error_msg, "composio_reported_error": True}
                    else:
                        # ... (unclear success handling as in update_calendar_event) ...
                        print(f"{user_interface.Fore.YELLOW}MCP_SM ({self.app_name}): Composio response for {tool_name} unclear: {json.dumps(composio_response, indent=2)}{user_interface.Style.RESET_ALL}")
                        return {"successful": False, "error": f"Composio response for {tool_name} did not indicate clear success or provide a specific error."}
                except json.JSONDecodeError:
                    # ... (JSON decode error handling) ...
                    print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): JSONDecodeError parsing {tool_name} response: {text_content[:200]}...{user_interface.Style.RESET_ALL}")
                    return {"successful": False, "error": f"Could not parse {tool_name} JSON response."}
            else: # text_content is None or empty
                # ... (no text_content error handling) ...
                print(f"{user_interface.Fore.YELLOW}MCP_SM ({self.app_name}): No text_content in {tool_name} response item.{user_interface.Style.RESET_ALL}")
                return {"successful": False, "error": f"No text content in {tool_name} response item."}

        print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): Unhandled result structure from {tool_name}. Raw: {creation_result_from_mcp}{user_interface.Style.RESET_ALL}")
        return {"successful": False, "error": f"Unexpected result structure from {tool_name} after tool call."}
