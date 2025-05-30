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
async def perform_proactive_checks(user_config, gemini_model) -> tuple[bool, List[Dict[str, Any]], List[Dict[str, Any]]]:
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
            gemini_model, all_fetched_raw_messages, user_persona, user_priorities
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
                        "timeMax": time_max_str, "maxResults": 10,
                        "singleEvents": True, "orderBy": "startTime"
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
            gemini_model, raw_calendar_events, user_persona, user_priorities
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


async def main_assistant_entry():
    """Entry point for the assistant logic."""
    if not config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY):
        print(f"{user_interface.Fore.RED}Error: {config_manager.ENV_GOOGLE_API_KEY} is not set in .env file.{user_interface.Style.RESET_ALL}")
        sys.exit(f"Critical configuration missing: {config_manager.ENV_GOOGLE_API_KEY}")

    user_configuration = config_manager.load_user_config()

    if not user_configuration or not user_configuration.get(config_manager.USER_EMAIL_KEY):
        user_configuration = run_signup_flow() # This now uses user_interface for prompts
        if not (user_configuration and user_configuration.get(config_manager.USER_EMAIL_KEY)):
            print(f"{user_interface.Fore.RED}Signup was not completed successfully. Exiting.{user_interface.Style.RESET_ALL}")
            return
        print(f"\n{user_interface.Fore.GREEN}Initial setup complete. The assistant will now perform its first check.{user_interface.Style.RESET_ALL}")
        user_configuration = config_manager.load_user_config() # Reload to be sure

    print(f"\n{user_interface.Fore.GREEN}Welcome back, {user_interface.Fore.YELLOW}{user_configuration.get(config_manager.USER_EMAIL_KEY)}{user_interface.Style.RESET_ALL}!")
    print(f"{user_interface.Style.DIM}Proactive Assistant starting...{user_interface.Style.RESET_ALL}")

    google_api_key = config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY)
    try:
        genai.configure(api_key=google_api_key)
        gemini_llm_model = genai.GenerativeModel('gemini-1.5-flash-latest') # Updated model
        print(f"{user_interface.Fore.GREEN}Gemini model initialized successfully.{user_interface.Style.RESET_ALL}")
    except Exception as e:
        print(f"{user_interface.Fore.RED}Failed to initialize Gemini model: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()
        sys.exit("Could not initialize LLM. Exiting.")

    # --- Main Interaction Loop (simplified for now) ---
    # For now, we'll just run one check cycle and then allow one round of actions.
    # Phase 7 will wrap this in a schedule.

    can_continue, emails_to_display, events_to_display = await perform_proactive_checks(user_configuration, gemini_llm_model)

    if not can_continue:
        print(f"{user_interface.Fore.YELLOW}Exiting assistant due to required user action (e.g., authentication). Please re-run after completing the action.{user_interface.Style.RESET_ALL}")
        return

    if not emails_to_display and not events_to_display:
        print(f"\n{user_interface.Fore.GREEN}No actionable items found in this cycle.{user_interface.Style.RESET_ALL}")
        # In a scheduled version, we'd just wait for the next cycle.
        return

    action_choice_data = user_interface.display_processed_data_and_get_action(emails_to_display, events_to_display)

    if action_choice_data:
        action_type, item_idx, action_idx_in_llm_suggestions = action_choice_data

        if action_type == "skip":
            print(f"{user_interface.Fore.YELLOW}Skipping actions for this cycle.{user_interface.Style.RESET_ALL}")
        elif action_type == "quit":
            print(f"{user_interface.Fore.YELLOW}Quitting interaction for this cycle.{user_interface.Style.RESET_ALL}")
        elif action_type == "email":
            chosen_email_data = emails_to_display[item_idx]
            chosen_llm_action_text = chosen_email_data['suggested_actions'][action_idx_in_llm_suggestions]
            print(f"\n{user_interface.Style.BRIGHT}You chose to act on Email:{user_interface.Style.RESET_ALL}")
            user_interface.display_email_summary(item_idx + 1, chosen_email_data) # Display it again for context
            print(f"{user_interface.Style.BRIGHT}Chosen LLM Suggested Action: {user_interface.Fore.GREEN}{chosen_llm_action_text}{user_interface.Style.RESET_ALL}")
            # TODO: Implement Phase 6 - actual action dispatching logic here
            # e.g., if "Draft" in chosen_llm_action_text: await handle_draft_email_reply(...)
            print(f"{user_interface.Fore.MAGENTA}Action execution for '{chosen_llm_action_text}' on email (ID: {chosen_email_data['original_email_data'].get('messageId')}) is not yet implemented.{user_interface.Style.RESET_ALL}")

        elif action_type == "event":
            chosen_event_data = events_to_display[item_idx]
            chosen_llm_action_text = chosen_event_data['suggested_actions'][action_idx_in_llm_suggestions]
            print(f"\n{user_interface.Style.BRIGHT}You chose to act on Event:{user_interface.Style.RESET_ALL}")
            user_interface.display_calendar_event_summary(len(emails_to_display) + item_idx + 1, chosen_event_data)
            print(f"{user_interface.Style.BRIGHT}Chosen LLM Suggested Action: {user_interface.Fore.GREEN}{chosen_llm_action_text}{user_interface.Style.RESET_ALL}")
            # TODO: Implement Phase 6 - actual action dispatching logic here
            print(f"{user_interface.Fore.MAGENTA}Action execution for '{chosen_llm_action_text}' on event (ID: {chosen_event_data['original_event_data'].get('id')}) is not yet implemented.{user_interface.Style.RESET_ALL}")
    else:
        if emails_to_display or events_to_display: # Only print if there were items but no valid action chosen
             print(f"{user_interface.Fore.YELLOW}No valid action selected or input was empty.{user_interface.Style.RESET_ALL}")


    print(f"\n{user_interface.Style.DIM}Assistant interaction finished for this cycle.{user_interface.Style.RESET_ALL}")
    # In a scheduled version, this is where it would loop back to wait for `schedule.run_pending()`

if __name__ == "__main__":
    try:
        asyncio.run(main_assistant_entry())
    except KeyboardInterrupt:
        print(f"\n{user_interface.Fore.YELLOW}Assistant stopped by user. Goodbye!{user_interface.Style.RESET_ALL}")
    except SystemExit as e:
        # print(f"Assistant is exiting: {e}") # SystemExit often has no message or just the exit code
        pass # Already handled by messages in code typically
    except Exception as e:
        print(f"{user_interface.Fore.RED}An unexpected error occurred in the main execution: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()
