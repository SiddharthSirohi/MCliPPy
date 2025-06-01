# assistant.py
import os
import sys
import json
import asyncio
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any

import google.generativeai as genai # For Gemini

# Import our modules
import config_manager
from mcp_handler import McpSessionManager
import llm_processor
import notifier # Added
import user_interface # Added

# --- Helper for User Input & Signup Flow (from user_interface.py now) ---
# These are called from user_interface.py, so no need to redefine here.

def run_signup_flow(): # Stays in assistant.py as it uses config_manager directly
    print(f"{user_interface.Fore.CYAN}Welcome to your Proactive AI Assistant!{user_interface.Style.RESET_ALL}")
    print("Let's get you set up.")
    user_interface.print_header("Initial Setup") # Using new UI helper

    user_config = {}
    while True:
        email = user_interface.get_user_input("Please enter your primary email address (this will be your user ID for service connections)")
        if email and "@" in email and "." in email.split("@")[-1]:
            user_config[config_manager.USER_EMAIL_KEY] = email
            break
        else:
            print(f"{user_interface.Fore.RED}Invalid email format. Please try again.{user_interface.Style.RESET_ALL}")

    user_config[config_manager.USER_PERSONA_KEY] = user_interface.get_user_input("Describe your role and main work focus")
    user_config[config_manager.USER_PRIORITIES_KEY] = user_interface.get_user_input("What are your key work priorities?")

    email_notifs_on = user_interface.get_yes_no_input("Enable notifications for important emails?", default_yes=True)
    calendar_notifs_on = user_interface.get_yes_no_input("Enable notifications for upcoming calendar events?", default_yes=True)

    user_config[config_manager.NOTIFICATION_PREFS_KEY] = {
        "email": "important" if email_notifs_on else "off",
        "calendar": "on" if calendar_notifs_on else "off"
    }

    gmail_server_uuid = config_manager.DEV_CONFIG.get(config_manager.ENV_GMAIL_MCP_SERVER_UUID)
    calendar_server_uuid = config_manager.DEV_CONFIG.get(config_manager.ENV_CALENDAR_MCP_SERVER_UUID)

    if not gmail_server_uuid or not calendar_server_uuid:
        print(f"\n{user_interface.Fore.RED}Error: GMAIL_MCP_SERVER_UUID or CALENDAR_MCP_SERVER_UUID not found in .env file.{user_interface.Style.RESET_ALL}")
        sys.exit("Critical configuration missing: MCP Server UUIDs.")

    user_config[config_manager.GMAIL_MCP_URL_KEY] = f"https://mcp.composio.dev/composio/server/{gmail_server_uuid}?transport=sse&include_composio_helper_actions=true"
    user_config[config_manager.CALENDAR_MCP_URL_KEY] = f"https://mcp.composio.dev/composio/server/{calendar_server_uuid}?transport=sse&include_composio_helper_actions=true"

    print(f"\nUsing Gmail MCP Server UUID: {user_interface.Fore.YELLOW}{gmail_server_uuid}{user_interface.Style.RESET_ALL}")
    print(f"Using Calendar MCP Server UUID: {user_interface.Fore.YELLOW}{calendar_server_uuid}{user_interface.Style.RESET_ALL}")

    user_config[config_manager.LAST_EMAIL_CHECK_KEY] = datetime.now(timezone.utc).isoformat()

    if config_manager.save_user_config(user_config):
        user_interface.print_header("Setup Complete")
        print(f"{user_interface.Fore.GREEN}Your preferences have been saved.{user_interface.Style.RESET_ALL}")
    else:
        print(f"{user_interface.Fore.RED}Error: Could not save your configuration.{user_interface.Style.RESET_ALL}")
        sys.exit("Failed to save user configuration.")
    return user_config

# --- Main Application Logic (Async now) ---
async def perform_proactive_checks(user_config, gemini_llm_model) -> tuple[bool, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Performs one cycle of proactive checks for Gmail and Calendar.
    Processes fetched data with LLM.
    Returns (can_continue_without_auth, important_emails_llm_data, processed_events_llm_data)
    """
    print(f"\n{user_interface.Style.DIM}Performing proactive checks at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...{user_interface.Style.RESET_ALL}")

    user_id = user_config.get(config_manager.USER_EMAIL_KEY)
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a busy professional")
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY, "important tasks and communications")

    gmail_base_url = user_config.get(config_manager.GMAIL_MCP_URL_KEY)
    calendar_base_url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY)

    all_fetched_raw_messages = []
    auth_action_required_overall = False # Flag if any service needs auth

    # --- Gmail Check ---
    if gmail_base_url and user_id and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off":
        user_interface.print_header("Checking Gmail")
        email_cycle_successful_for_timestamp_update = False
        auth_action_required_for_gmail = False
        try:
            async with McpSessionManager(gmail_base_url, user_id, "gmail") as gmail_manager:
                if not gmail_manager.session:
                    print(f"{user_interface.Fore.RED}Failed to establish Gmail MCP session.{user_interface.Style.RESET_ALL}")
                else:
                    # print(f"Gmail tools available (first 5): {list(gmail_manager.tools.keys())[:5]}...")

                    twenty_four_hours_ago_utc = datetime.now(timezone.utc) - timedelta(hours=24)
                    # last_check_ts_str = user_config.get(config_manager.LAST_EMAIL_CHECK_KEY)
                    # query_start_dt = twenty_four_hours_ago_utc # Default to 24h
                    # if last_check_ts_str:
                    #     last_check_dt = datetime.fromisoformat(last_check_ts_str)
                    #     # Query for emails newer than last check, but not older than 24h
                    #     query_start_dt = max(last_check_dt, twenty_four_hours_ago_utc)

                    query_since_timestamp = int(twenty_four_hours_ago_utc.timestamp()) # Sticking to 24h for now
                    gmail_query = f"is:unread after:{query_since_timestamp}"

                    base_fetch_params = {
                        "query": gmail_query, "max_results": 10, "include_payload": True
                    }
                    current_page_token = None
                    max_pages_to_fetch = 3
                    pages_fetched = 0

                    while pages_fetched < max_pages_to_fetch:
                        pages_fetched += 1
                        current_fetch_params = base_fetch_params.copy()
                        if current_page_token:
                            current_fetch_params["page_token"] = current_page_token

                        # print(f"Attempting GMAIL_FETCH_EMAILS (Page {pages_fetched}) with params: {current_fetch_params}")
                        email_result_page = await gmail_manager.ensure_auth_and_call_tool("GMAIL_FETCH_EMAILS", current_fetch_params)

                        if isinstance(email_result_page, dict) and email_result_page.get("needs_user_action"):
                            print(f"{user_interface.Fore.YELLOW}Gmail requires authentication. Please follow instructions and re-run.{user_interface.Style.RESET_ALL}")
                            auth_action_required_for_gmail = True
                            auth_action_required_overall = True
                            break
                        elif isinstance(email_result_page, dict) and email_result_page.get("error"):
                            print(f"{user_interface.Fore.RED}Error fetching Gmail emails page {pages_fetched}: {email_result_page.get('error')}{user_interface.Style.RESET_ALL}")
                            current_page_token = None
                            break
                        elif email_result_page and hasattr(email_result_page, 'content'):
                            if email_result_page.content:
                                for item in email_result_page.content:
                                    text_content = getattr(item, 'text', None)
                                    if text_content:
                                        try:
                                            email_data_json_page = json.loads(text_content)
                                            if email_data_json_page.get("successful") is True:
                                                messages_on_page = email_data_json_page.get("data", {}).get("messages", [])
                                                if messages_on_page:
                                                    # print(f"Found {len(messages_on_page)} email(s) on page {pages_fetched}.")
                                                    all_fetched_raw_messages.extend(messages_on_page)
                                                current_page_token = email_data_json_page.get("data", {}).get("nextPageToken")
                                                if not current_page_token: break
                                            else:
                                                error_from_tool = email_data_json_page.get('error', 'Unknown error from GMAIL_FETCH_EMAILS tool.')
                                                print(f"{user_interface.Fore.RED}Composio GMAIL_FETCH_EMAILS reported not successful for page {pages_fetched}: {error_from_tool}{user_interface.Style.RESET_ALL}")
                                                current_page_token = None; break
                                        except json.JSONDecodeError:
                                            print(f"{user_interface.Fore.RED}Could not parse email page {pages_fetched} as JSON.{user_interface.Style.RESET_ALL}")
                                            current_page_token = None; break
                                    else: current_page_token = None; break
                            else: current_page_token = None; break
                        else: current_page_token = None; break
                        if not current_page_token: break

                    if not auth_action_required_for_gmail:
                        email_cycle_successful_for_timestamp_update = True

            if auth_action_required_for_gmail:
                return False, [], [] # Signal main to exit for auth

            if email_cycle_successful_for_timestamp_update:
                config_manager.set_last_email_check_timestamp()
                print(f"{user_interface.Fore.GREEN}Gmail check complete. {len(all_fetched_raw_messages)} unread email(s) in last 24h fetched.{user_interface.Style.RESET_ALL}")


        except Exception as e:
            print(f"{user_interface.Fore.RED}Outer error during Gmail processing: {e}{user_interface.Style.RESET_ALL}")
            # traceback.print_exc()
    else:
        if user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off":
             print(f"{user_interface.Fore.YELLOW}Gmail MCP URL or User ID not configured. Skipping Gmail checks.{user_interface.Style.RESET_ALL}")

    # --- Process Gmail with LLM ---
    important_emails_llm_data = []
    if all_fetched_raw_messages and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") == "important":
        # user_interface.print_header(f"Processing {len(all_fetched_raw_messages)} Gmail messages with LLM")
        processed_emails_from_llm = await llm_processor.process_emails_with_llm(
            gemini_llm_model, all_fetched_raw_messages, user_persona, user_priorities
        )
        if processed_emails_from_llm:
            for pe_data in processed_emails_from_llm:
                if pe_data.get('is_important'):
                    important_emails_llm_data.append(pe_data)
            # print(f"LLM identified {len(important_emails_llm_data)} important email(s).")
    elif user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") == "all":
        # If "all", treat all fetched as "important" for display, but LLM might not have summarized
        for raw_email in all_fetched_raw_messages:
             important_emails_llm_data.append({
                 "original_email_data": raw_email,
                 "is_important": True, # For display purposes
                 "summary": raw_email.get("snippet", "No summary available."), # Use snippet if no LLM summary
                 "suggested_actions": ["View full email", "Mark as read", "Delete"] # Generic actions
             })
        # print(f"Displaying all {len(important_emails_llm_data)} fetched emails (preference: all).")


    # --- Calendar Check ---
    raw_calendar_events = []
    auth_action_required_for_calendar = False
    if calendar_base_url and user_id and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
        user_interface.print_header("Checking Calendar")
        try:
            async with McpSessionManager(calendar_base_url, user_id, "googlecalendar") as calendar_manager:
                if not calendar_manager.session:
                    print(f"{user_interface.Fore.RED}Failed to establish Calendar MCP session.{user_interface.Style.RESET_ALL}")
                else:
                    # print(f"Calendar tools available (first 5): {list(calendar_manager.tools.keys())[:5]}...")
                    now_utc = datetime.now(timezone.utc)
                    time_min_str = now_utc.isoformat().replace("+00:00", "Z")
                    time_max_str = (now_utc + timedelta(days=1)).isoformat().replace("+00:00", "Z")
                    calendar_fetch_params = {
                        "calendarId": "primary", "timeMin": time_min_str,
                        "timeMax": time_max_str, "max_results": 10,
                        "singleEvents": True, "order_by": "startTime"
                    }
                    # print(f"Attempting GOOGLECALENDAR_FIND_EVENT with params: {calendar_fetch_params}")
                    event_result = await calendar_manager.ensure_auth_and_call_tool("GOOGLECALENDAR_FIND_EVENT", calendar_fetch_params)

                    if isinstance(event_result, dict) and event_result.get("needs_user_action"):
                        print(f"{user_interface.Fore.YELLOW}Google Calendar requires authentication. Please follow instructions and re-run.{user_interface.Style.RESET_ALL}")
                        auth_action_required_for_calendar = True
                        auth_action_required_overall = True
                    elif isinstance(event_result, dict) and event_result.get("error"):
                        print(f"{user_interface.Fore.RED}Error fetching Calendar events: {event_result.get('error')}{user_interface.Style.RESET_ALL}")
                    elif event_result and hasattr(event_result, 'content'):
                        if event_result.content:
                            for item in event_result.content:
                                text_content = getattr(item, 'text', None)
                                if text_content:
                                    try:
                                        event_data_json = json.loads(text_content)
                                        actual_events = event_data_json.get("data",{}).get("event_data",{}).get("event_data",[])
                                        if actual_events:
                                            raw_calendar_events.extend(actual_events)
                                    except json.JSONDecodeError:
                                        print(f"{user_interface.Fore.RED}Could not parse calendar item text as JSON.{user_interface.Style.RESET_ALL}")
                        print(f"{user_interface.Fore.GREEN}Calendar check complete. {len(raw_calendar_events)} event(s) in next 24h fetched.{user_interface.Style.RESET_ALL}")

            if auth_action_required_for_calendar:
                return False, important_emails_llm_data, [] # Signal exit for auth

        except Exception as e:
            print(f"{user_interface.Fore.RED}Error during Calendar processing: {e}{user_interface.Style.RESET_ALL}")
            # traceback.print_exc()
    else:
        if user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
            print(f"{user_interface.Fore.YELLOW}Calendar MCP URL or User ID not configured. Skipping Calendar checks.{user_interface.Style.RESET_ALL}")

    if auth_action_required_overall: # If any service triggered auth, exit now
        return False, important_emails_llm_data, raw_calendar_events

    # --- Process Calendar with LLM ---
    processed_events_llm_data = []
    if raw_calendar_events and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
        # user_interface.print_header(f"Processing {len(raw_calendar_events)} Calendar events with LLM")
        processed_events_llm_data = await llm_processor.process_calendar_events_with_llm(
            gemini_llm_model, raw_calendar_events, user_persona, user_priorities, user_config
        )
        # print(f"LLM processed {len(processed_events_llm_data)} calendar event(s).")

    # --- Send Notifications ---
    num_imp_emails = len(important_emails_llm_data)
    num_act_events = len([e for e in processed_events_llm_data if e.get('suggested_actions')]) # Count events that LLM gave actions for

    if user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off" or \
       user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
        if num_imp_emails > 0 or num_act_events > 0 : # Or use len(raw_calendar_events) if just notifying about any event
            notif_title = "Proactive Assistant Update"
            notif_message_parts = []
            if num_imp_emails > 0 and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off":
                notif_message_parts.append(f"{num_imp_emails} important email(s)")
            if num_act_events > 0 and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off": # Check pref again
                notif_message_parts.append(f"{num_act_events} upcoming event(s) with suggestions")
            elif len(raw_calendar_events) > 0 and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
                 notif_message_parts.append(f"{len(raw_calendar_events)} upcoming event(s)")


            if notif_message_parts: # Only send if there's something to say based on prefs
                notifier.send_macos_notification(notif_title, ", ".join(notif_message_parts) + " requiring attention.")
        else:
            print(f"{user_interface.Style.DIM}No new important items for notification this cycle.{user_interface.Style.RESET_ALL}")


    print(f"{user_interface.Style.DIM}Proactive checks cycle complete.{user_interface.Style.RESET_ALL}")
    return True, important_emails_llm_data, processed_events_llm_data

# assistant.py
# ...

async def handle_draft_email_reply(
    gemini_llm_model,
    chosen_email_data: Dict[str, Any],
    initial_llm_action_text: str,
    user_config: Dict[str, Any],
    gmail_mcp_manager: McpSessionManager
):
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY)
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY)

    current_draft_info = None
    user_edit_instructions = None # Store edit instructions for LLM

    print(f"\n{user_interface.Style.DIM}Drafting reply for '{chosen_email_data['original_email_data'].get('subject', 'N/A')}' based on action: '{initial_llm_action_text}'...{user_interface.Style.RESET_ALL}")
    current_draft_info = await llm_processor.draft_email_reply_with_llm(
        gemini_llm_model,
        chosen_email_data['original_email_data'],
        initial_llm_action_text,
        user_persona,
        user_priorities
    )

    while True:
        if not current_draft_info or current_draft_info.get("error"):
            error_msg = current_draft_info.get("error", "Failed to generate draft.") if current_draft_info else "Failed to generate draft."
            print(f"{user_interface.Fore.RED}Error: {error_msg}{user_interface.Style.RESET_ALL}")
            return False

        draft_body = current_draft_info.get("body")
        draft_subject = current_draft_info.get("subject") # Still useful for context and save_draft

        # Use the new confirmation function
        confirmation_choice = user_interface.get_send_edit_cancel_confirmation(
            f"Subject: {draft_subject}\n\n{draft_body}",
            service_name="Email Reply"
        )

        if confirmation_choice == "send_reply":
            print(f"{user_interface.Style.DIM}Preparing to send reply via Gmail...{user_interface.Style.RESET_ALL}")
            recipient_for_reply = current_draft_info.get("recipient_email_for_reply")
            original_thread_id = current_draft_info.get("original_thread_id")
            draft_body = current_draft_info.get("body") # Make sure body is also fetched

            # CRITICAL CHECK FOR RECIPIENT
            if not recipient_for_reply or recipient_for_reply == "Unknown Sender" or "@" not in recipient_for_reply:
                 print(f"{user_interface.Fore.RED}Cannot send reply: Valid recipient email not found. Found: '{recipient_for_reply}'{user_interface.Style.RESET_ALL}")
                 return False # Critical info missing

            if not original_thread_id or not draft_body:
                 print(f"{user_interface.Fore.RED}Critical reply information missing (thread ID or body). Cannot send.{user_interface.Style.RESET_ALL}")
                 return False

            if not gmail_mcp_manager or not gmail_mcp_manager.session:
                print(f"{user_interface.Fore.RED}Gmail MCP session not available. Cannot send reply.{user_interface.Style.RESET_ALL}")
                return False

            send_reply_outcome = await gmail_mcp_manager.reply_to_gmail_thread(
                thread_id=original_thread_id,
                recipient_email=recipient_for_reply, # Original sender
                message_body=draft_body
            )
            if send_reply_outcome.get("successful"):
                print(f"{user_interface.Fore.GREEN}Success! {send_reply_outcome.get('message', 'Reply sent.')}{user_interface.Style.RESET_ALL}")
                return True
            else:
                error_msg = send_reply_outcome.get('error', 'Failed to send reply via MCP.')
                print(f"{user_interface.Fore.RED}MCP Error: {error_msg}{user_interface.Style.RESET_ALL}")
                # if send_reply_outcome.get("needs_user_action"): # Handled by ensure_auth_and_call_tool
                #     print(f"{user_interface.Fore.YELLOW}Gmail authentication might be required again.{user_interface.Style.RESET_ALL}")
                return False

        elif confirmation_choice == "edit":
            user_edit_instructions = user_interface.get_user_input(f"{user_interface.Fore.CYAN}Your edit instructions (or type your full new draft):{user_interface.Style.RESET_ALL}")
            print(f"{user_interface.Style.DIM}Re-drafting with your instructions...{user_interface.Style.RESET_ALL}")
            current_draft_info = await llm_processor.draft_email_reply_with_llm(
                gemini_llm_model,
                chosen_email_data['original_email_data'],
                initial_llm_action_text,
                user_persona,
                user_priorities,
                user_edit_instructions=user_edit_instructions
            )
            # Loop continues to show new draft

        elif confirmation_choice == "cancel":
            print(f"{user_interface.Fore.YELLOW}Drafting and replying cancelled.{user_interface.Style.RESET_ALL}")
            return True

        else:
            print(f"{user_interface.Fore.RED}Unknown confirmation choice.{user_interface.Style.RESET_ALL}")
            return False

# ... (rest of assistant.py, especially main_assistant_entry, remains the same for now) ...
# The logic in main_assistant_entry that calls handle_draft_email_reply is fine.

async def handle_delete_calendar_event(
    chosen_event_data: Dict[str, Any],
    user_config: Dict[str, Any],
    calendar_mcp_manager: McpSessionManager # Pass the manager
):
    original_event_details = chosen_event_data.get("original_event_data", {})
    event_id_to_delete = original_event_details.get("id")
    event_title = original_event_details.get("summary", "Unknown Event")
    # calendar_id = original_event_details.get("calendarId", "primary") # From sample, calendarId isn't directly on event object

    if not event_id_to_delete:
        print(f"{user_interface.Fore.RED}Error: Could not find Event ID for '{event_title}'. Cannot delete.{user_interface.Style.RESET_ALL}")
        return False

    confirm_delete = user_interface.get_confirmation(
        f"Are you sure you want to delete the event: '{event_title}' (ID: {event_id_to_delete})?",
        destructive=True
    )

    if confirm_delete:
        print(f"{user_interface.Style.DIM}Attempting to delete calendar event '{event_title}'...{user_interface.Style.RESET_ALL}")
        if not calendar_mcp_manager or not calendar_mcp_manager.session:
            print(f"{user_interface.Fore.RED}Calendar MCP session not available. Cannot delete event.{user_interface.Style.RESET_ALL}")
            return False

        delete_outcome = await calendar_mcp_manager.delete_calendar_event(event_id=event_id_to_delete) # Defaults to primary calendar

        if delete_outcome.get("successful"):
            print(f"{user_interface.Fore.GREEN}Success! {delete_outcome.get('message', f'Event {event_title} deleted.')}{user_interface.Style.RESET_ALL}")
            # PM: Consider re-fetching calendar events here to update the UI immediately, or mark as deleted locally.
            # For now, the next cycle of perform_proactive_checks will show the updated calendar.
            return True
        else:
            error_msg = delete_outcome.get('error', 'Failed to delete event via MCP.')
            print(f"{user_interface.Fore.RED}MCP Error: {error_msg}{user_interface.Style.RESET_ALL}")
            # if delete_outcome.get("needs_user_action"): (handled by ensure_auth_and_call_tool)
            return False
    else:
        print(f"{user_interface.Fore.YELLOW}Event deletion cancelled by user.{user_interface.Style.RESET_ALL}")
        return True # User cancelled, not a failure of the action itself

async def handle_update_calendar_event(
    chosen_event_data: Dict[str, Any],
    user_config: Dict[str, Any], # For user_id, calendar_mcp_url
    calendar_mcp_manager: McpSessionManager,
    # llm_parsed_updates: Optional[Dict[str, Any]] = None # For when LLM suggests specific changes
):
    original_event_details = chosen_event_data.get("original_event_data", {})
    event_id_to_update = original_event_details.get("id")
    event_title = original_event_details.get("summary", "Unknown Event")

    if not event_id_to_update:
        print(f"{user_interface.Fore.RED}Error: Could not find Event ID for '{event_title}'. Cannot update.{user_interface.Style.RESET_ALL}")
        return False

    # For now, always go to manual update menu.
    # Later, if llm_parsed_updates is rich, we can confirm those first.
    updates_from_user = user_interface.get_event_update_choices(
        event_title,
        original_event_details # Pass the original event details
    )

    if not updates_from_user: # User cancelled or made no changes
        print(f"{user_interface.Fore.YELLOW}Event update cancelled or no changes specified.{user_interface.Style.RESET_ALL}")
        return True # User cancelled, not a failure

    print(f"{user_interface.Style.DIM}Attempting to update calendar event '{event_title}'...{user_interface.Style.RESET_ALL}")

    update_outcome = await calendar_mcp_manager.update_calendar_event(
        event_id=event_id_to_update,
        updates=updates_from_user
    )

    if update_outcome.get("successful"):
        print(f"{user_interface.Fore.GREEN}Success! {update_outcome.get('message', f'Event {event_title} updated.')}{user_interface.Style.RESET_ALL}")
        # updated_event_display = update_outcome.get("updated_event", {}) # If Composio returns full updated event
        # print(f"Updated event details: {json.dumps(updated_event_display, indent=2)}")
        return True
    else:
        error_msg = update_outcome.get('error', 'Failed to update event via MCP.')
        print(f"{user_interface.Fore.RED}MCP Error: {error_msg}{user_interface.Style.RESET_ALL}")
        return False

async def handle_create_calendar_event(
    gemini_llm_model, # The initialized Gemini model instance
    llm_suggestion_text: str, # e.g., "Create event: 'Team Sync' Tuesday 3pm with john@example.com"
                              # or "Schedule a 30-min follow-up for Project Alpha"
    original_context_text: Optional[str], # e.g., email body or original event summary
    user_config: Dict[str, Any],
    calendar_mcp_manager: McpSessionManager # The active McpSessionManager for Calendar
) -> bool: # Returns True if action was processed (even if cancelled by user), False on critical error
    """
    Handles the workflow for creating a new calendar event based on an LLM suggestion.
    """
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a professional")
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY, "key tasks")

    # Get current time to help LLM resolve relative dates/times in its parsing
    # The LLM needs current time in UTC preferably, or with explicit timezone
    # Google API event times are often handled with specific timezones or UTC.
    # Let's provide UTC to the LLM for parsing, and then handle timezone in UI/MCP call.
    current_time_for_llm_context = datetime.now(timezone.utc).isoformat()


    print(f"\n{user_interface.Style.DIM}Assistant is parsing details for new event based on: '{llm_suggestion_text}'...{user_interface.Style.RESET_ALL}")

    parsed_event_details_from_llm = await llm_processor.parse_event_creation_details_from_suggestion(
        gemini_llm_model=gemini_llm_model,
        llm_suggestion_text=llm_suggestion_text,
        original_context_text=original_context_text,
        user_persona=user_persona,
        user_priorities=user_priorities,
        current_datetime_iso=current_time_for_llm_context
    )

    if not parsed_event_details_from_llm or parsed_event_details_from_llm.get("error"):
        error_msg = parsed_event_details_from_llm.get("error", "Failed to parse event creation details from LLM suggestion.") if parsed_event_details_from_llm else "LLM parsing for event creation returned None."
        print(f"{user_interface.Fore.RED}Could not create event: {error_msg}{user_interface.Style.RESET_ALL}")
        return False # Indicate failure to process this action

    # Now, let the user confirm and potentially edit these parsed details
    final_event_details_for_api = user_interface.get_event_creation_confirmation_and_edits(
        parsed_event_details_from_llm # Pass the dictionary parsed by the LLM
    )

    if not final_event_details_for_api: # User cancelled in the UI
        print(f"{user_interface.Fore.YELLOW}Event creation cancelled by user.{user_interface.Style.RESET_ALL}")
        return True # Action was "handled" by user cancelling, not a script error

    # Proceed to call the MCP tool to create the event
    print(f"{user_interface.Style.DIM}Attempting to create calendar event: '{final_event_details_for_api.get('summary', 'Untitled Event')}'...{user_interface.Style.RESET_ALL}")

    if not calendar_mcp_manager or not calendar_mcp_manager.session:
        print(f"{user_interface.Fore.RED}Calendar MCP session not available. Cannot create event.{user_interface.Style.RESET_ALL}")
        return False

    create_outcome = await calendar_mcp_manager.create_calendar_event(
        event_details=final_event_details_for_api
        # calendar_id defaults to "primary" in create_calendar_event method
    )

    if create_outcome.get("successful"):
        created_event_response_data = create_outcome.get("created_event_data", {})
        event_summary_created = created_event_response_data.get('summary', final_event_details_for_api.get('summary', 'Untitled Event'))
        event_id_created = created_event_response_data.get('id', 'N/A')
        print(f"{user_interface.Fore.GREEN}Success! {create_outcome.get('message', 'Event created.')} "
              f"Title: '{event_summary_created}', ID: {event_id_created}{user_interface.Style.RESET_ALL}")
        # PM: Consider if assistant should offer to add it to a local "just created" list for the current cycle display
        return True
    else:
        error_msg = create_outcome.get('error', 'Failed to create event via MCP tool.')
        print(f"{user_interface.Fore.RED}MCP Error creating event: {error_msg}{user_interface.Style.RESET_ALL}")
        return False


async def main_assistant_entry():
    """Entry point for the assistant logic."""
    if not config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY):
        print(f"{user_interface.Fore.RED}Error: {config_manager.ENV_GOOGLE_API_KEY} is not set in .env file.{user_interface.Style.RESET_ALL}")
        sys.exit(f"Critical configuration missing: {config_manager.ENV_GOOGLE_API_KEY}")

    user_configuration = config_manager.load_user_config()

    if not user_configuration or not user_configuration.get(config_manager.USER_EMAIL_KEY):
        user_configuration = run_signup_flow()
        if not (user_configuration and user_configuration.get(config_manager.USER_EMAIL_KEY)):
            print(f"{user_interface.Fore.RED}Signup was not completed successfully. Exiting.{user_interface.Style.RESET_ALL}")
            return
        print(f"\n{user_interface.Fore.GREEN}Initial setup complete. The assistant will now perform its first check.{user_interface.Style.RESET_ALL}")
        user_configuration = config_manager.load_user_config()

    print(f"\n{user_interface.Fore.GREEN}Welcome back, {user_interface.Fore.YELLOW}{user_configuration.get(config_manager.USER_EMAIL_KEY)}{user_interface.Style.RESET_ALL}!")
    print(f"{user_interface.Style.DIM}Proactive Assistant starting...{user_interface.Style.RESET_ALL}")

    google_api_key = config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY)
    try:
        genai.configure(api_key=google_api_key)
        gemini_llm_model = genai.GenerativeModel('gemini-2.5-flash-preview-05-20')
        print(f"{user_interface.Fore.GREEN}Gemini model initialized successfully.{user_interface.Style.RESET_ALL}")
    except Exception as e:
        print(f"{user_interface.Fore.RED}Failed to initialize Gemini model: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()
        sys.exit("Could not initialize LLM. Exiting.")

    # --- Outer loop for potentially multiple proactive check cycles ---
    while True:
        can_continue, emails_to_display, events_to_display = await perform_proactive_checks(
            user_configuration, gemini_llm_model
        )

        if not can_continue:
            print(f"{user_interface.Fore.YELLOW}Exiting assistant due to required user action (e.g., authentication). Please re-run after completing the action.{user_interface.Style.RESET_ALL}")
            break # Break outer assistant loop

        if not emails_to_display and not any(e.get('suggested_actions') for e in events_to_display if e): # Check if events_to_display has actionable items
            print(f"\n{user_interface.Fore.GREEN}No actionable items found in this cycle.{user_interface.Style.RESET_ALL}")
            if not user_interface.get_yes_no_input("Perform another full proactive check (y/N)?", default_yes=False):
                print(f"{user_interface.Fore.YELLOW}Quitting assistant.{user_interface.Style.RESET_ALL}")
                break # Quit outer assistant loop
            else:
                continue # Go to next iteration of outer while True loop (re-check)

        # --- Inner loop for multiple actions on the CURRENTLY FETCHED items ---
        first_display_of_items = True
        while True:
            action_choice_data = user_interface.display_processed_data_and_get_action(
                emails_to_display,
                events_to_display,
                first_time_display=first_display_of_items
            )
            first_display_of_items = False

            if not action_choice_data:
                if not emails_to_display and not any(e.get('suggested_actions') for e in events_to_display if e):
                    break
                else:
                    print(f"{user_interface.Fore.YELLOW}Please try your selection again or choose 'd' (done), 'r' (redisplay), or 'q' (quit).{user_interface.Style.RESET_ALL}")
                    continue

            action_type, item_idx, action_idx_in_llm_suggestions, raw_choice = action_choice_data

            if action_type == "done":
                print(f"{user_interface.Fore.YELLOW}Done with actions for this set of items.{user_interface.Style.RESET_ALL}")
                break
            elif action_type == "quit_assistant":
                print(f"{user_interface.Fore.YELLOW}Quitting assistant.{user_interface.Style.RESET_ALL}")
                return
            elif action_type == "redisplay":
                first_display_of_items = True
                continue

            action_succeeded_this_turn = False
            gmail_mcp_url = user_configuration.get(config_manager.GMAIL_MCP_URL_KEY)
            calendar_mcp_url = user_configuration.get(config_manager.CALENDAR_MCP_URL_KEY)
            user_id = user_configuration.get(config_manager.USER_EMAIL_KEY)

            if action_type == "email":
                chosen_email_data = emails_to_display[item_idx]
                chosen_llm_action_text = chosen_email_data['suggested_actions'][action_idx_in_llm_suggestions]
                user_interface.print_header(f"Action on Email: {chosen_email_data['original_email_data'].get('subject', 'N/A')[:40]}...")
                print(f"{user_interface.Style.BRIGHT}Chosen LLM Suggested Action: {user_interface.Fore.GREEN}{chosen_llm_action_text}{user_interface.Style.RESET_ALL}")

                if "draft" in chosen_llm_action_text.lower() or \
                   "reply" in chosen_llm_action_text.lower() or \
                   "confirm availability" in chosen_llm_action_text.lower() or \
                   "suggest an alternative time" in chosen_llm_action_text.lower():
                    if gmail_mcp_url and user_id:
                        print(f"{user_interface.Style.DIM}Establishing Gmail session for action...{user_interface.Style.RESET_ALL}")
                        async with McpSessionManager(gmail_mcp_url, user_id, "gmail-action") as action_gmail_manager: # Unique name for action manager
                            if action_gmail_manager.session:
                                action_succeeded_this_turn = await handle_draft_email_reply(
                                    gemini_llm_model, chosen_email_data, chosen_llm_action_text,
                                    user_configuration, action_gmail_manager
                                )
                            else:
                                print(f"{user_interface.Fore.RED}Could not establish Gmail session for the action.{user_interface.Style.RESET_ALL}")
                    else:
                        print(f"{user_interface.Fore.RED}Gmail configuration (URL or User ID) missing.{user_interface.Style.RESET_ALL}")

                elif "create calendar event" in chosen_llm_action_text.lower() or \
                    "schedule a meeting" in chosen_llm_action_text.lower() or \
                    "add to calendar" in chosen_llm_action_text.lower(): # Keywords for creating event

                    original_email_body_for_context = chosen_email_data.get("original_email_data", {}).get("messageText") or \
                                                    chosen_email_data.get("original_email_data", {}).get("snippet","No original email body available for context.")

                    if calendar_mcp_url and user_id: # Ensure we have calendar config
                        print(f"{user_interface.Style.DIM}Establishing Calendar session for create action (from email)...{user_interface.Style.RESET_ALL}")
                        async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-action-create-from-email") as action_calendar_manager:
                            if action_calendar_manager.session:
                                action_succeeded_this_turn = await handle_create_calendar_event(
                                    gemini_llm_model,
                                    chosen_llm_action_text, # The LLM's suggestion string
                                    original_email_body_for_context,
                                    user_configuration,
                                    action_calendar_manager
                                )
                            else:
                                print(f"{user_interface.Fore.RED}Could not establish Calendar session for create action.{user_interface.Style.RESET_ALL}")
                    else:
                        print(f"{user_interface.Fore.RED}Calendar configuration missing. Cannot create event from email.{user_interface.Style.RESET_ALL}")

                else:
                    print(f"{user_interface.Fore.MAGENTA}Action '{chosen_llm_action_text}' for email is not yet specifically implemented beyond drafting/replying.{user_interface.Style.RESET_ALL}")

            elif action_type == "event":
                # Ensure events_to_display has valid items before indexing
                displayable_events = [e for e in events_to_display if e and e.get('suggested_actions')]
                if 0 <= item_idx < len(displayable_events): # item_idx should be index for displayable_events list
                    # This mapping was complex. Let's simplify by getting the actual event object
                    # The item_idx from display_processed_data_and_get_action IS ALREADY the correct
                    # index into the original processed_events_llm IF we ensure the numbering is based on it.
                    # The user_interface function's event indexing logic needs to be robust.
                    # For now, assuming item_idx directly maps to processed_events_llm (after emails).

                    # Find the true chosen event data from the original list passed to display_processed_data
                    true_event_index_in_original_list = item_idx # if item_idx from UI is already 0-based for events_to_display

                    if 0 <= true_event_index_in_original_list < len(events_to_display):
                        chosen_event_data = events_to_display[true_event_index_in_original_list]
                        chosen_llm_action_text = chosen_event_data['suggested_actions'][action_idx_in_llm_suggestions]
                        user_interface.print_header(f"Action on Event: {chosen_event_data['original_event_data'].get('summary', 'N/A')[:40]}...")
                        print(f"{user_interface.Style.BRIGHT}Chosen LLM Suggested Action: {user_interface.Fore.GREEN}{chosen_llm_action_text}{user_interface.Style.RESET_ALL}")

                        if "update this event's details" in chosen_llm_action_text.lower() or \
                                           "reschedule" in chosen_llm_action_text.lower() or \
                                           "change title" in chosen_llm_action_text.lower() or \
                                           "add attendee" in chosen_llm_action_text.lower() or \
                                           "add google meet" in chosen_llm_action_text.lower(): # Add more keywords

                                            if calendar_mcp_url and user_id:
                                                print(f"{user_interface.Style.DIM}Establishing Calendar session for update action...{user_interface.Style.RESET_ALL}")
                                                async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-action-update") as action_calendar_manager:
                                                    if action_calendar_manager.session:
                                                        action_succeeded_this_turn = await handle_update_calendar_event(
                                                            chosen_event_data,
                                                            user_configuration,
                                                            action_calendar_manager
                                                        )

                        elif "delete" in chosen_llm_action_text.lower() or "cancel this meeting" in chosen_llm_action_text.lower():
                            if calendar_mcp_url and user_id:
                                print(f"{user_interface.Style.DIM}Establishing Calendar session for delete action...{user_interface.Style.RESET_ALL}")
                                async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-action-delete") as action_calendar_manager: # Unique name
                                    if action_calendar_manager.session:
                                        action_succeeded_this_turn = await handle_delete_calendar_event(
                                            chosen_event_data, user_configuration, action_calendar_manager
                                        )
                                    else:
                                        print(f"{user_interface.Fore.RED}Could not establish Calendar session for delete action.{user_interface.Style.RESET_ALL}")
                            else:
                                print(f"{user_interface.Fore.RED}Calendar config missing. Cannot delete event.{user_interface.Style.RESET_ALL}")
                        elif "create a new event" in chosen_llm_action_text.lower() or \
                             "schedule a follow-up" in chosen_llm_action_text.lower() or \
                             "schedule a prep session" in chosen_llm_action_text.lower() or \
                             "block additional time" in chosen_llm_action_text.lower():

                            # Define original_event_summary_for_context HERE
                            original_event_summary_for_context = chosen_event_data.get("original_event_data", {}).get("summary", "related event") # Get summary of the CURRENT event

                            if calendar_mcp_url and user_id:
                                print(f"{user_interface.Style.DIM}Establishing Calendar session for create action (from event context)...{user_interface.Style.RESET_ALL}")
                                async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-action-create-from-event") as action_calendar_manager:
                                    if action_calendar_manager.session:
                                        action_succeeded_this_turn = await handle_create_calendar_event(
                                            gemini_llm_model,
                                            chosen_llm_action_text,
                                            original_event_summary_for_context, # Now it's defined
                                            user_configuration,
                                            action_calendar_manager
                                        )
                                    else:
                                        print(f"{user_interface.Fore.RED}Could not establish Calendar session for create action.{user_interface.Style.RESET_ALL}")
                            else:
                                 print(f"{user_interface.Fore.RED}Calendar configuration missing. Cannot create new event.{user_interface.Style.RESET_ALL}")
                        else:
                            print(f"{user_interface.Fore.MAGENTA}Action '{chosen_llm_action_text}' for event is not yet implemented.{user_interface.Style.RESET_ALL}")
                    else:
                        print(f"{user_interface.Fore.RED}Internal error: Invalid event index after selection.{user_interface.Style.RESET_ALL}")
                else:
                    print(f"{user_interface.Fore.RED}Internal error: Invalid event index from UI choice.{user_interface.Style.RESET_ALL}")


            if action_succeeded_this_turn:
                print(f"{user_interface.Fore.GREEN}Action '{raw_choice}' processed successfully.{user_interface.Style.RESET_ALL}")
                # PM: After a successful action, data might be stale.
                # Forcing a redisplay of current (potentially stale) data. User can use 'r' for full refresh.
                print(f"{user_interface.Style.DIM}Displaying current items. Use 'r' to fetch fresh data if needed.{user_interface.Style.RESET_ALL}")
                # We don't set first_display_of_items = True here, so it just re-prompts for action on current data.
            elif action_type in ["email", "event"]: # If it was an email/event action but it failed or wasn't handled
                print(f"{user_interface.Fore.YELLOW}Action '{raw_choice}' was not fully completed or not applicable.{user_interface.Style.RESET_ALL}")

            # Loop back to display_processed_data_and_get_action for another action on the same set of items

        # This is the end of the inner "action loop" for the current set of fetched items
        print(f"\n{user_interface.Style.DIM}Finished interacting with current set of proactive items.{user_interface.Style.RESET_ALL}")

        if not user_interface.get_yes_no_input("Perform another full proactive check (y/N)?", default_yes=False):
            print(f"{user_interface.Fore.YELLOW}Quitting assistant.{user_interface.Style.RESET_ALL}")
            break # Break outer assistant loop (for multiple checks)
        # else: continue to the next proactive check (next iteration of outer while True)

    print(f"\n{user_interface.Style.DIM}Proactive Assistant session finished.{user_interface.Style.RESET_ALL}")

if __name__ == "__main__":
    try:
        asyncio.run(main_assistant_entry())
    except KeyboardInterrupt:
        print(f"\n{user_interface.Fore.YELLOW}Assistant stopped by user. Goodbye!{user_interface.Style.RESET_ALL}")
    except SystemExit as e:
        pass
    except Exception as e:
        print(f"{user_interface.Fore.RED}An unexpected error occurred in the main execution: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()
