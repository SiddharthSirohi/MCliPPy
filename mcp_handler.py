# mcp_handler.py
import asyncio
import json
import traceback
import sys
import time
from mcp import ClientSession
from mcp.client.sse import sse_client
from typing import Dict, List, Optional, Any, Tuple

import config_manager
import user_interface
import calendar_utils

COMPOSIO_AUTH_INIT_TOOL = "COMPOSIO_INITIATE_CONNECTION"

async def call_composio_initiate_connection(session: ClientSession, app_name: str, user_id_for_logging: str):
    print(f"MCP_HANDLER: Calling {COMPOSIO_AUTH_INIT_TOOL} for app '{app_name}' and user '{user_id_for_logging}'.")
    init_conn_params = {"tool": app_name}
    print(f"  Parameters for {COMPOSIO_AUTH_INIT_TOOL}: {init_conn_params}")
    redirect_url_from_tool = None
    try:
        auth_tool_result = await session.call_tool(COMPOSIO_AUTH_INIT_TOOL, init_conn_params)
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
                             start_index = text_content.find("https://backend.composio.dev/api/v3/s/")
                             if start_index != -1:
                                 end_index = text_content.find("\"", start_index)
                                 if end_index == -1: end_index = text_content.find(" ", start_index)
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
        self.session: Optional[ClientSession] = None
        self.tools: Dict[str, Any] = {}
        self._is_connecting = False
        self._is_connected = False
        self._last_activity = None
        self._connection_timeout = 300  # 5 minutes as per MCP SDK
        self._max_reconnect_attempts = 3


    async def _connect_session(self):
        """Internal method to establish the MCP session."""
        if self._is_connected or self._is_connecting:
            return

        self._is_connecting = True
        print(f"MCP_SM ({self.app_name}): Connecting to {self.full_mcp_url}")
        try:
            self._sse_client_cm = sse_client(self.full_mcp_url)
            self._transport_streams = await self._sse_client_cm.__aenter__()
            self.session = ClientSession(self._transport_streams[0], self._transport_streams[1])
            await self.session.__aenter__()
            print(f"MCP_SM ({self.app_name}): Initializing session...")
            await self.session.initialize()
            print(f"MCP_SM ({self.app_name}): Session initialized.")
            await self._list_and_cache_tools()
            self._is_connected = True
            self._last_activity = time.time()  # Set initial activity time
        except Exception as e:
            print(f"MCP_SM ({self.app_name}): Error during _connect_session: {e}")
            traceback.print_exc()
            exc_info = sys.exc_info()
            # Clean up partially established resources
            if self.session and hasattr(self.session, '__aexit__'):
                try:
                    await self.session.__aexit__(*exc_info)
                except Exception as e_exit_session:
                    print(f"MCP_SM ({self.app_name}): Error during session cleanup: {e_exit_session}")
            if self._sse_client_cm and hasattr(self._sse_client_cm, '__aexit__'):
                try:
                    await self._sse_client_cm.__aexit__(*exc_info)
                except Exception as e_exit_sse:
                    print(f"MCP_SM ({self.app_name}): Error during sse_client cleanup: {e_exit_sse}")

            self.session = None
            self._sse_client_cm = None
            self._transport_streams = None
            self._is_connected = False
        finally:
            self._is_connecting = False

    async def ensure_connected(self):
        """Ensures the MCP session is connected with automatic reconnection."""
        current_time = time.time()

        # Check if connection is stale (more than 4 minutes old to be safe)
        if (self._is_connected and self._last_activity and
            current_time - self._last_activity > 240):  # 4 minutes
            print(f"MCP_SM ({self.app_name}): Connection appears stale, proactively reconnecting...")
            await self.disconnect_if_connected()

        if not self._is_connected and not self._is_connecting:
            await self._connect_session()
        elif self._is_connecting:
            # Wait for existing connection attempt
            wait_count = 0
            while self._is_connecting and wait_count < 30:  # Max 3 seconds wait
                await asyncio.sleep(0.1)
                wait_count += 1

        # Update last activity timestamp
        if self._is_connected:
            self._last_activity = current_time

    async def disconnect_if_connected(self):
        """Disconnects the MCP session if it's currently active."""
        if self._is_connected and self.session:
            print(f"MCP_SM ({self.app_name}): Disconnecting session...")
            try:
                # Proper cleanup sequence
                if self.session and hasattr(self.session, '__aexit__'):
                    await self.session.__aexit__(None, None, None)
            except Exception as e:
                print(f"MCP_SM ({self.app_name}): Warning during session cleanup: {e}")

            try:
                if self._sse_client_cm and hasattr(self._sse_client_cm, '__aexit__'):
                    await self._sse_client_cm.__aexit__(None, None, None)
            except Exception as e:
                print(f"MCP_SM ({self.app_name}): Warning during SSE client cleanup: {e}")
            finally:
                # Always reset state
                self.session = None
                self._sse_client_cm = None
                self._transport_streams = None
                self._is_connected = False
                self._last_activity = None
                print(f"MCP_SM ({self.app_name}): Session disconnected.")
        elif self._is_connecting:
            print(f"MCP_SM ({self.app_name}): Warning: disconnect_if_connected called while still connecting.")

    async def __aenter__(self):
        await self.ensure_connected()
        if not self.session:
            raise ConnectionError(f"MCP_SM ({self.app_name}): Failed to establish session in __aenter__.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect_if_connected()

    async def _list_and_cache_tools(self):
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
        """Enhanced tool calling with automatic reconnection on failure."""
        for attempt in range(self._max_reconnect_attempts):
            try:
                await self.ensure_connected()
                if not self.session:
                    print(f"MCP_SM ({self.app_name}): No active session for tool '{tool_name}'. Cannot proceed (connection failed).")
                    return {"error": f"No active MCP session for {self.app_name} (connection failed).", "needs_reconnect": True, "successful": False}

                print(f"MCP_SM ({self.app_name}): Attempting to call tool '{tool_name}' (attempt {attempt + 1}/{self._max_reconnect_attempts})...")

                # Update activity timestamp before making the call
                self._last_activity = time.time()

                tool_result = await self.session.call_tool(tool_name, params)

                # Process successful result (your existing logic)
                if hasattr(tool_result, 'content') and tool_result.content:
                    first_content_item_text = getattr(tool_result.content[0], 'text', None)
                    data = None
                    if first_content_item_text:
                        try:
                            data = json.loads(first_content_item_text)
                        except json.JSONDecodeError:
                            print(f"MCP_SM ({self.app_name}): Content text is not valid JSON: {first_content_item_text[:100]}...")
                            return {"error": "Tool response content is not valid JSON.", "successful": False}

                    if isinstance(data, dict):
                        error_message = data.get("error")
                        is_successful_false = data.get("successful") is False
                        connection_not_found_err = f"Could not find a connection with app='{self.app_name}' and entity='{self.user_id}'"

                        # Fixed None handling
                        if error_message and (
                            error_message == connection_not_found_err or
                            "credentials do not contain the necessary fields" in str(error_message) or
                            (is_successful_false and "401 Client Error: Unauthorized" in str(error_message))
                        ):
                            print(f"MCP_SM ({self.app_name}): Auth needed for '{tool_name}'. Initiating connection process.")
                            redirect_url = await call_composio_initiate_connection(self.session, self.app_name, self.user_id)
                            if redirect_url:
                                return {"error": f"Authentication required for {self.app_name}.", "redirect_url": redirect_url, "needs_user_action": True, "successful": False}
                            else:
                                return {"error": f"Auth initiation for {self.app_name} called, but no redirect URL obtained.", "needs_user_action": False, "auth_initiation_failed": True, "successful": False}
                        elif is_successful_false and error_message:
                            print(f"MCP_SM ({self.app_name}): Composio error during '{tool_name}' call: {error_message}")
                            return {"error": f"Composio error for {self.app_name}: {error_message}", "composio_error": True, "successful": False}

                    return tool_result

                print(f"MCP_SM ({self.app_name}): Tool '{tool_name}' call returned no content or unexpected structure.")
                return {"error": f"Tool '{tool_name}' call returned no content or unexpected structure.", "successful": False}

            except Exception as e:
                error_msg = str(e)
                print(f"MCP_SM ({self.app_name}): Exception calling tool '{tool_name}' (attempt {attempt + 1}): {error_msg}")

                # Check if it's a connection error that we can retry
                if "Connection closed" in error_msg or "Connection lost" in error_msg:
                    if attempt < self._max_reconnect_attempts - 1:
                        print(f"MCP_SM ({self.app_name}): Connection error, attempting reconnection...")
                        await self.disconnect_if_connected()
                        await asyncio.sleep(1)  # Brief delay before retry
                        continue
                    else:
                        print(f"MCP_SM ({self.app_name}): Max reconnection attempts reached for '{tool_name}'")
                        return {"error": f"Connection failed after {self._max_reconnect_attempts} attempts: {error_msg}", "connection_failed": True, "successful": False}
                else:
                    # Non-connection error, don't retry
                    traceback.print_exc()
                    return {"error": str(e), "exception": True, "successful": False}

        return {"error": f"Tool execution failed after {self._max_reconnect_attempts} attempts", "max_attempts_reached": True, "successful": False}
    # --- get_calendar_free_slots ---
    async def get_calendar_free_slots(
        self,
        time_min_iso_ist: str,
        time_max_iso_ist: str,
        meeting_duration_minutes: int,
        user_work_start_hour: int,
        user_work_end_hour: int,
        calendar_id: str = "primary"
    ) -> Dict[str, Any]:
        await self.ensure_connected()
        if not self.session:
            return {"error": "No active Calendar MCP session (connection failed).", "successful": False}

        tool_name = "GOOGLECALENDAR_FIND_FREE_SLOTS"
        if tool_name not in self.tools:
            msg = f"Tool '{tool_name}' not available. Check Composio allowed_tools."
            print(f"{user_interface.Fore.RED}MCP_SM ({self.app_name}): {msg}{user_interface.Style.RESET_ALL}")
            return {"error": msg, "successful": False}

        params = {
            "time_min": time_min_iso_ist, "time_max": time_max_iso_ist,
            "timezone": "Asia/Kolkata", "items": [calendar_id],
            "calendar_expansion_max": 1, "group_expansion_max": 0
        }

        find_slots_outcome = await self.ensure_auth_and_call_tool(tool_name, params)

        if isinstance(find_slots_outcome, dict) and find_slots_outcome.get("error"):
            return find_slots_outcome

        if find_slots_outcome and hasattr(find_slots_outcome, 'content') and find_slots_outcome.content:
            text_content = getattr(find_slots_outcome.content[0], 'text', None)
            if text_content:
                try:
                    composio_response = json.loads(text_content)
                    if composio_response.get("successful"):
                        response_data = composio_response.get("data", {}).get("response_data", {})
                        busy_slots = response_data.get("calendars", {}).get(calendar_id, {}).get("busy", [])
                        query_start_dt = calendar_utils.parse_iso_to_ist(time_min_iso_ist)
                        query_end_dt = calendar_utils.parse_iso_to_ist(time_max_iso_ist)
                        if not query_start_dt or not query_end_dt:
                            return {"successful": False, "error": "Invalid time_min/max for free slot calc."}

                        free_slots = calendar_utils.calculate_free_slots(
                            query_start_dt, query_end_dt, busy_slots,
                            meeting_duration_minutes, user_work_start_hour, user_work_end_hour
                        )
                        return {"successful": True, "free_slots": free_slots}
                    else:
                        err = composio_response.get("error", "Composio tool reported not successful.")
                        return {"successful": False, "error": str(err), "composio_reported_error": True}
                except json.JSONDecodeError:
                    return {"successful": False, "error": f"Could not parse {tool_name} JSON response."}
            else:
                return {"successful": False, "error": f"No text content in {tool_name} response."}

        return {"successful": False, "error": f"Unexpected result from {tool_name} tool call."}

    # --- reply_to_gmail_thread ---
    async def reply_to_gmail_thread(self, thread_id: str, recipient_email: str, message_body: str) -> Dict[str, Any]:
        await self.ensure_connected()
        if not self.session:
            return {"error": "No active Gmail MCP session (connection failed).", "successful": False}

        tool_name = "GMAIL_REPLY_TO_THREAD"
        if tool_name not in self.tools:
            return {"error": f"Tool '{tool_name}' not available.", "successful": False}

        params = {"thread_id": thread_id, "recipient_email": recipient_email, "message_body": message_body, "is_html": False}
        outcome = await self.ensure_auth_and_call_tool(tool_name, params)

        if isinstance(outcome, dict) and outcome.get("error"):
            return outcome
        if outcome and hasattr(outcome, 'content') and outcome.content:
            text_content = getattr(outcome.content[0], 'text', None)
            if text_content:
                try:
                    res = json.loads(text_content)
                    if res.get("successful"):
                        return {"successful": True, "message": f"Replied to thread {thread_id}."}
                    else:
                        return {"successful": False, "error": res.get("error", "Gmail reply failed.")}
                except json.JSONDecodeError:
                    return {"successful": False, "error": "Could not parse reply response."}
        return {"successful": False, "error": f"Unexpected response from {tool_name}."}

    # --- mark_thread_as_read ---
    async def mark_thread_as_read(self, thread_id: str) -> Dict[str, Any]:
        await self.ensure_connected()
        if not self.session:
            return {"error": "No active Gmail MCP session (connection failed).", "successful": False}

        tool_name = "GMAIL_MODIFY_THREAD_LABELS"
        if tool_name not in self.tools:
            return {"error": f"Tool '{tool_name}' not available.", "successful": False}

        params = {"thread_id": thread_id, "remove_label_ids": ["UNREAD"]}
        outcome = await self.ensure_auth_and_call_tool(tool_name, params)

        if isinstance(outcome, dict) and outcome.get("error"):
            return outcome
        if outcome and hasattr(outcome, 'content') and outcome.content:
            text_content = getattr(outcome.content[0], 'text', None)
            if text_content:
                try:
                    res = json.loads(text_content)
                    if res.get("successful"):
                        return {"successful": True, "message": f"Thread {thread_id} marked as read."}
                    else:
                        return {"successful": False, "error": res.get("error", "Mark read failed.")}
                except json.JSONDecodeError:
                    return {"successful": False, "error": "Could not parse mark read response."}
        return {"successful": False, "error": f"Unexpected response from {tool_name}."}

    # --- create_calendar_event ---
    async def create_calendar_event(self, event_details: Dict[str, Any]) -> Dict[str, Any]:
        await self.ensure_connected()
        if not self.session:
            return {"error": "No active Calendar MCP session (connection failed).", "successful": False}

        tool_name = "GOOGLECALENDAR_CREATE_EVENT"
        if tool_name not in self.tools:
            return {"error": f"Tool '{tool_name}' not available.", "successful": False}

        # Convert event_details to the format expected by Composio
        params = {
            "calendar_id": "primary",
            "summary": event_details.get("summary", "New Event"),
            "start_datetime": event_details.get("start_datetime"),
            "timezone": event_details.get("timezone", "Asia/Kolkata"),
            "description": event_details.get("description", ""),
            "location": event_details.get("location", "")
        }

        # Calculate end time
        duration_hours = event_details.get("event_duration_hour", 0)
        duration_minutes = event_details.get("event_duration_minutes", 30)
        total_minutes = (duration_hours * 60) + duration_minutes

        # Add end time calculation
        from datetime import datetime, timedelta
        start_dt = datetime.fromisoformat(event_details.get("start_datetime"))
        end_dt = start_dt + timedelta(minutes=total_minutes)
        params["end_datetime"] = end_dt.strftime("%Y-%m-%dT%H:%M:%S")

        # Add attendees if provided
        if event_details.get("attendees"):
            params["attendees"] = event_details["attendees"]

        # Add meeting room if requested
        if event_details.get("create_meeting_room", False):
            params["create_meeting_room"] = True

        outcome = await self.ensure_auth_and_call_tool(tool_name, params)

        if isinstance(outcome, dict) and outcome.get("error"):
            return outcome
        if outcome and hasattr(outcome, 'content') and outcome.content:
            text_content = getattr(outcome.content[0], 'text', None)
            if text_content:
                try:
                    res = json.loads(text_content)
                    if res.get("successful"):
                        return {"successful": True, "message": "Calendar event created successfully.", "created_event_data": res.get("data")}
                    else:
                        return {"successful": False, "error": res.get("error", "Event creation failed.")}
                except json.JSONDecodeError:
                    return {"successful": False, "error": "Could not parse create event response."}
        return {"successful": False, "error": f"Unexpected response from {tool_name}."}

    # --- update_calendar_event ---
    async def update_calendar_event(self, event_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        await self.ensure_connected()
        if not self.session:
            return {"error": "No active Calendar MCP session (connection failed).", "successful": False}

        tool_name = "GOOGLECALENDAR_UPDATE_EVENT"
        if tool_name not in self.tools:
            return {"error": f"Tool '{tool_name}' not available.", "successful": False}

        params = {"calendar_id": "primary", "event_id": event_id}
        params.update(updates)  # Add the updates to params

        outcome = await self.ensure_auth_and_call_tool(tool_name, params)

        if isinstance(outcome, dict) and outcome.get("error"):
            return outcome
        if outcome and hasattr(outcome, 'content') and outcome.content:
            text_content = getattr(outcome.content[0], 'text', None)
            if text_content:
                try:
                    res = json.loads(text_content)
                    if res.get("successful"):
                        return {"successful": True, "message": f"Event {event_id} updated successfully."}
                    else:
                        return {"successful": False, "error": res.get("error", "Event update failed.")}
                except json.JSONDecodeError:
                    return {"successful": False, "error": "Could not parse update event response."}
        return {"successful": False, "error": f"Unexpected response from {tool_name}."}

    # --- delete_calendar_event ---
    async def delete_calendar_event(self, event_id: str) -> Dict[str, Any]:
        await self.ensure_connected()
        if not self.session:
            return {"error": "No active Calendar MCP session (connection failed).", "successful": False}

        tool_name = "GOOGLECALENDAR_DELETE_EVENT"
        if tool_name not in self.tools:
            return {"error": f"Tool '{tool_name}' not available.", "successful": False}

        params = {"calendar_id": "primary", "event_id": event_id}
        outcome = await self.ensure_auth_and_call_tool(tool_name, params)

        if isinstance(outcome, dict) and outcome.get("error"):
            return outcome
        if outcome and hasattr(outcome, 'content') and outcome.content:
            text_content = getattr(outcome.content[0], 'text', None)
            if text_content:
                try:
                    res = json.loads(text_content)
                    if res.get("successful"):
                        return {"successful": True, "message": f"Event {event_id} deleted successfully."}
                    else:
                        return {"successful": False, "error": res.get("error", "Event deletion failed.")}
                except json.JSONDecodeError:
                    return {"successful": False, "error": "Could not parse delete event response."}
        return {"successful": False, "error": f"Unexpected response from {tool_name}."}
