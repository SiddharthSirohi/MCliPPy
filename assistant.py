import os
import sys
import json
import asyncio
import traceback
import platform
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from google import genai
from google.genai import types as genai_types # For types like GenerateContentConfig, Part, Content

# Import our modules
import config_manager
from mcp_handler import McpSessionManager
import llm_processor
import notifier
import user_interface
import calendar_utils

from mcp import ClientSession
from mcp.types import Tool, CallToolResult

MODEL_NAME = "gemini-2.5-flash-preview-05-20"

# --- Helper for User Input & Signup Flow (from user_interface.py now) ---
# These are called from user_interface.py, so no need to redefine here.

# assistant.py
def generate_launchd_plist_content(
    script_path: str,
    working_directory: str,
    label_prefix: str,
    frequency_minutes: int,
    log_dir: str
) -> str:
    # print(f"DEBUG_PLIST_GEN (Start of function): Received working_directory: {working_directory}")
    project_root = Path(working_directory).resolve()
    # print(f"DEBUG_PLIST_GEN: Resolved project_root: {project_root}")

    venv_python_path = project_root / ".venv" / "bin" / "python"
    # print(f"DEBUG_PLIST_GEN: Expected venv_python_path: {venv_python_path}")

    path_exists_check = venv_python_path.exists()
    # print(f"DEBUG_PLIST_GEN: Result of venv_python_path.exists(): {path_exists_check}")

    if path_exists_check:
        # print(f"{user_interface.Fore.GREEN}DEBUG_PLIST_GEN: venv_python_path EXISTS branch taken.{user_interface.Style.RESET_ALL}")
        python_exec_to_use_candidate = str(venv_python_path)
        # print(f"DEBUG_PLIST_GEN: Candidate from venv: {python_exec_to_use_candidate}")
        python_exec_to_use = python_exec_to_use_candidate
    else:
        # print(f"{user_interface.Fore.RED}DEBUG_PLIST_GEN: venv_python_path DOES NOT EXIST branch taken.{user_interface.Style.RESET_ALL}")
        current_sys_executable = str(Path(sys.executable).resolve())
        print(f"{user_interface.Fore.YELLOW}WARNING: Virtual environment Python not found at {venv_python_path}. Falling back to sys.executable: {current_sys_executable}{user_interface.Style.RESET_ALL}")
        python_exec_to_use = current_sys_executable

    # print(f"DEBUG_PLIST_GEN: VALUE OF 'python_exec_to_use' AFTER IF/ELSE: {python_exec_to_use}")

    script_path_resolved = str(Path(script_path).resolve())
    log_dir_resolved = str(Path(log_dir).resolve())
    Path(log_dir_resolved).mkdir(parents=True, exist_ok=True)
    label = f"{label_prefix}.proactiveassistant"
    interval_seconds = int(frequency_minutes * 60)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_exec_to_use}</string>
        <string>{script_path_resolved}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{str(project_root)}</string>
    <key>StartInterval</key>
    <integer>{interval_seconds}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir_resolved}/assistant_out.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir_resolved}/assistant_err.log</string>
</dict>
</plist>
"""
    return plist_content

def run_signup_flow():
    print(f"{user_interface.Fore.CYAN}Welcome to your Proactive AI Assistant!{user_interface.Style.RESET_ALL}")
    print("Let's get you set up.")
    user_interface.print_header("Initial Setup")

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

    user_interface.print_header("Scheduling Preferences")
    while True:
        try:
            freq_str = user_interface.get_user_input("How often should the assistant check for updates (e.g., 15m, 30m, 1h)?", default="30m")
            if 'h' in freq_str:
                user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY] = int(freq_str.replace('h', '')) * 60
            elif 'm' in freq_str:
                user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY] = int(freq_str.replace('m', ''))
            else:
                user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY] = int(freq_str)
            if user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY] > 0:
                break
            else:
                print(f"{user_interface.Fore.RED}Frequency must be positive.{user_interface.Style.RESET_ALL}")
        except ValueError:
            print(f"{user_interface.Fore.RED}Invalid frequency format. Use numbers optionally followed by 'm' or 'h'.{user_interface.Style.RESET_ALL}")

    days_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    active_days_input_str = user_interface.get_user_input(
        "On which days should the assistant be active? (e.g., mon,tue,wed,thu,fri or 'all' or 'weekdays')",
        default="weekdays"
    ).lower()
    selected_days = []
    if "all" in active_days_input_str:
        selected_days = list(range(7))
    elif "weekdays" in active_days_input_str:
        selected_days = [0, 1, 2, 3, 4]
    else:
        for day_abbr in active_days_input_str.split(','):
            if day_abbr.strip() in days_map:
                selected_days.append(days_map[day_abbr.strip()])
    user_config[config_manager.SCHED_ACTIVE_DAYS_KEY] = sorted(list(set(selected_days)))

    while True:
        try:
            start_h_str = user_interface.get_user_input("What hour should checks START (0-23, e.g., 9 for 9 AM)?", default="9")
            start_h = int(start_h_str)
            if 0 <= start_h <= 23:
                user_config[config_manager.SCHED_ACTIVE_START_HOUR_KEY] = start_h
                user_config[config_manager.WORK_START_HOUR_KEY] = start_h
                break
            else:
                print(f"{user_interface.Fore.RED}Hour must be 0-23.{user_interface.Style.RESET_ALL}")
        except ValueError:
            print(f"{user_interface.Fore.RED}Invalid hour.{user_interface.Style.RESET_ALL}")

    while True:
        try:
            end_h_str = user_interface.get_user_input(f"What hour should checks END (0-23, e.g., 18 for up to 6 PM, must be after start hour {user_config[config_manager.SCHED_ACTIVE_START_HOUR_KEY]})?", default="18")
            end_h = int(end_h_str)
            if user_config[config_manager.SCHED_ACTIVE_START_HOUR_KEY] < end_h <= 23:
                user_config[config_manager.SCHED_ACTIVE_END_HOUR_KEY] = end_h
                user_config[config_manager.WORK_END_HOUR_KEY] = end_h
                break
            else:
                print(f"{user_interface.Fore.RED}End hour must be after start hour and <= 23.{user_interface.Style.RESET_ALL}")
        except ValueError:
            print(f"{user_interface.Fore.RED}Invalid hour.{user_interface.Style.RESET_ALL}")

    if user_interface.get_yes_no_input("Are your typical working hours for free-slot calculation different from these active check hours?", default_yes=False):
        while True:
            try:
                work_start_h_str = user_interface.get_user_input("Your typical workday START hour (0-23)?", default=str(user_config[config_manager.SCHED_ACTIVE_START_HOUR_KEY]))
                work_start_h = int(work_start_h_str)
                if 0 <= work_start_h <= 23:
                    user_config[config_manager.WORK_START_HOUR_KEY] = work_start_h
                    break
            except ValueError: print(f"{user_interface.Fore.RED}Invalid hour.{user_interface.Style.RESET_ALL}")
        while True:
            try:
                work_end_h_str = user_interface.get_user_input(f"Your typical workday END hour (0-23, after {user_config[config_manager.WORK_START_HOUR_KEY]})?", default=str(user_config[config_manager.SCHED_ACTIVE_END_HOUR_KEY]))
                work_end_h = int(work_end_h_str)
                if user_config[config_manager.WORK_START_HOUR_KEY] < work_end_h <= 23:
                    user_config[config_manager.WORK_END_HOUR_KEY] = work_end_h
                    break
            except ValueError: print(f"{user_interface.Fore.RED}Invalid hour.{user_interface.Style.RESET_ALL}")

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
        user_interface.print_header("Setup Complete & launchd Agent Configuration")
        print(f"{user_interface.Fore.GREEN}Your preferences have been saved.{user_interface.Style.RESET_ALL}")

        if platform.system() == "Darwin":
            try:
                script_file_path = Path(__file__).resolve()
                work_dir_for_plist = script_file_path.parent

                log_storage_dir = config_manager.CONFIG_DIR_PATH
                user_name = os.getenv("USER", "defaultuser")
                label_prefix_str = f"com.{user_name}"

                plist_content_str = generate_launchd_plist_content(
                    script_path=str(script_file_path),
                    working_directory=str(work_dir_for_plist),
                    label_prefix=label_prefix_str,
                    frequency_minutes=user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY],
                    log_dir=str(log_storage_dir)
                )

                launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
                launch_agents_dir.mkdir(parents=True, exist_ok=True)

                plist_filename = f"{label_prefix_str}.proactiveassistant.plist"
                plist_file_path = launch_agents_dir / plist_filename

                with open(plist_file_path, "w") as f:
                    f.write(plist_content_str)

                print(f"\n{user_interface.Fore.GREEN}A launchd agent file has been created at:{user_interface.Style.RESET_ALL}")
                print(f"  {plist_file_path}")
                print(f"\n{user_interface.Fore.YELLOW}To enable automatic background checks, open Terminal and run:{user_interface.Style.RESET_ALL}")
                print(f"  launchctl load {plist_file_path}")
                print(f"\n{user_interface.Fore.CYAN}The assistant will then run every {user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY]} minutes during your active hours/days.{user_interface.Style.RESET_ALL}")
                print(f"Logs will be written to: {log_storage_dir}/assistant_out.log and assistant_err.log")
                print(f"To stop automatic checks, run:")
                print(f"  launchctl unload {plist_file_path}")

            except Exception as e:
                print(f"\n{user_interface.Fore.RED}Error creating launchd agent file: {e}{user_interface.Style.RESET_ALL}")
                print(f"{user_interface.Fore.YELLOW}You will need to set up scheduling manually if desired.{user_interface.Style.RESET_ALL}")
    else:
        print(f"{user_interface.Fore.RED}Error: Could not save your configuration.{user_interface.Style.RESET_ALL}")
        sys.exit("Failed to save user configuration.")
    return user_config


async def perform_proactive_checks(user_config: Dict[str, Any], gemini_client: genai.Client, MODEL_NAME: str) -> tuple[bool, List[Dict[str, Any]], List[Dict[str, Any]]]:
    print(f"\n{user_interface.Style.DIM}Performing proactive checks at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...{user_interface.Style.RESET_ALL}")

    user_id = user_config.get(config_manager.USER_EMAIL_KEY)
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a busy professional")
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY, "important tasks and communications")

    gmail_base_url = user_config.get(config_manager.GMAIL_MCP_URL_KEY)
    calendar_base_url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY)

    all_fetched_raw_messages = []
    auth_action_required_overall = False

    if gmail_base_url and user_id and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off":
        user_interface.print_header("Checking Gmail")
        email_cycle_successful_for_timestamp_update = False
        auth_action_required_for_gmail = False
        try:
            async with McpSessionManager(gmail_base_url, user_id, "gmail") as gmail_manager:
                if not gmail_manager.session:
                    print(f"{user_interface.Fore.RED}Failed to establish Gmail MCP session.{user_interface.Style.RESET_ALL}")
                else:
                    twenty_four_hours_ago_utc = datetime.now(timezone.utc) - timedelta(hours=24)
                    query_since_timestamp = int(twenty_four_hours_ago_utc.timestamp())
                    gmail_query = f"is:unread after:{query_since_timestamp}"
                    base_fetch_params = {"query": gmail_query, "max_results": 10, "include_payload": True}
                    current_page_token = None
                    max_pages_to_fetch = 3; pages_fetched = 0
                    while pages_fetched < max_pages_to_fetch:
                        pages_fetched += 1
                        current_fetch_params = base_fetch_params.copy()
                        if current_page_token: current_fetch_params["page_token"] = current_page_token
                        email_result_page = await gmail_manager.ensure_auth_and_call_tool("GMAIL_FETCH_EMAILS", current_fetch_params)
                        if isinstance(email_result_page, dict) and email_result_page.get("needs_user_action"):
                            auth_action_required_for_gmail = True; auth_action_required_overall = True; break
                        elif isinstance(email_result_page, dict) and email_result_page.get("error"):
                            print(f"{user_interface.Fore.RED}Error fetching Gmail page {pages_fetched}: {email_result_page.get('error')}{user_interface.Style.RESET_ALL}")
                            current_page_token = None; break
                        elif email_result_page and hasattr(email_result_page, 'content') and email_result_page.content:
                            text_content = getattr(email_result_page.content[0], 'text', None)
                            if text_content:
                                try:
                                    email_data_json_page = json.loads(text_content)
                                    if email_data_json_page.get("successful"):
                                        messages = email_data_json_page.get("data", {}).get("messages", [])
                                        if messages: all_fetched_raw_messages.extend(messages)
                                        current_page_token = email_data_json_page.get("data", {}).get("nextPageToken")
                                        if not current_page_token: break
                                    else:
                                        print(f"{user_interface.Fore.RED}GMAIL_FETCH_EMAILS not successful: {email_data_json_page.get('error')}{user_interface.Style.RESET_ALL}")
                                        current_page_token = None; break
                                except json.JSONDecodeError:
                                    print(f"{user_interface.Fore.RED}JSONDecodeError parsing Gmail page {pages_fetched}.{user_interface.Style.RESET_ALL}")
                                    current_page_token = None; break
                            else: current_page_token = None; break
                        else: current_page_token = None; break
                        if not current_page_token: break
                    if not auth_action_required_for_gmail: email_cycle_successful_for_timestamp_update = True
            if auth_action_required_for_gmail: return False, [], []
            if email_cycle_successful_for_timestamp_update:
                config_manager.set_last_email_check_timestamp()
                print(f"{user_interface.Fore.GREEN}Gmail check complete. {len(all_fetched_raw_messages)} unread email(s) in last 24h fetched.{user_interface.Style.RESET_ALL}")
        except Exception as e:
            print(f"{user_interface.Fore.RED}Outer error during Gmail processing: {e}{user_interface.Style.RESET_ALL}")
    else:
        if user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off":
             print(f"{user_interface.Fore.YELLOW}Gmail MCP URL or User ID not configured. Skipping Gmail checks.{user_interface.Style.RESET_ALL}")

    important_emails_llm_data = []
    if all_fetched_raw_messages and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") == "important":
        processed_emails_from_llm = await llm_processor.process_emails_with_llm(
            gemini_client, MODEL_NAME, all_fetched_raw_messages, user_persona, user_priorities
        )
        if processed_emails_from_llm:
            for pe_data in processed_emails_from_llm:
                if pe_data.get('is_important'): important_emails_llm_data.append(pe_data)
    elif user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") == "all":
        for raw_email in all_fetched_raw_messages:
             important_emails_llm_data.append({
                 "original_email_data": raw_email, "is_important": True,
                 "summary": raw_email.get("snippet", "No summary."), "suggested_actions": ["View", "Mark read", "Delete"]
             })

    raw_calendar_events = []
    auth_action_required_for_calendar = False
    if calendar_base_url and user_id and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
        user_interface.print_header("Checking Calendar")
        try:
            async with McpSessionManager(calendar_base_url, user_id, "googlecalendar") as calendar_manager:
                if not calendar_manager.session:
                    print(f"{user_interface.Fore.RED}Failed to establish Calendar MCP session.{user_interface.Style.RESET_ALL}")
                else:
                    now_utc = datetime.now(timezone.utc)
                    time_min_str = now_utc.isoformat().replace("+00:00", "Z")
                    time_max_str = (now_utc + timedelta(days=1)).isoformat().replace("+00:00", "Z")
                    params = {"calendarId": "primary", "timeMin": time_min_str, "timeMax": time_max_str, "max_results": 10, "singleEvents": True, "order_by": "startTime"}
                    event_result = await calendar_manager.ensure_auth_and_call_tool("GOOGLECALENDAR_FIND_EVENT", params)
                    if isinstance(event_result, dict) and event_result.get("needs_user_action"):
                        auth_action_required_for_calendar = True; auth_action_required_overall = True
                    elif isinstance(event_result, dict) and event_result.get("error"):
                        print(f"{user_interface.Fore.RED}Error fetching Calendar events: {event_result.get('error')}{user_interface.Style.RESET_ALL}")
                    elif event_result and hasattr(event_result, 'content') and event_result.content:
                        text_content = getattr(event_result.content[0], 'text', None)
                        if text_content:
                            try:
                                data = json.loads(text_content)
                                if data.get("successful"): raw_calendar_events.extend(data.get("data",{}).get("event_data",{}).get("event_data",[]))
                                else: print(f"{user_interface.Fore.RED}GOOGLECALENDAR_FIND_EVENT not successful: {data.get('error')}{user_interface.Style.RESET_ALL}")
                            except json.JSONDecodeError: print(f"{user_interface.Fore.RED}JSONDecodeError parsing Calendar events.{user_interface.Style.RESET_ALL}")
                        print(f"{user_interface.Fore.GREEN}Calendar check complete. {len(raw_calendar_events)} event(s) in next 24h fetched.{user_interface.Style.RESET_ALL}")
            if auth_action_required_for_calendar: return False, important_emails_llm_data, []
        except Exception as e:
            print(f"{user_interface.Fore.RED}Error during Calendar processing: {e}{user_interface.Style.RESET_ALL}")
    else:
        if user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
            print(f"{user_interface.Fore.YELLOW}Calendar MCP URL or User ID not configured. Skipping Calendar checks.{user_interface.Style.RESET_ALL}")

    if auth_action_required_overall: return False, important_emails_llm_data, raw_calendar_events

    actionable_events_llm_data = []
    if raw_calendar_events and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
        processed_events_from_llm_temp = await llm_processor.process_calendar_events_with_llm(
            gemini_client, MODEL_NAME, raw_calendar_events, user_config, user_persona, user_priorities
        )
        if processed_events_from_llm_temp:
            for pe_data in processed_events_from_llm_temp:
                if pe_data.get('suggested_actions'): actionable_events_llm_data.append(pe_data)

    notification_sent_this_cycle = False
    num_imp_emails = len(important_emails_llm_data)
    num_act_events = len(actionable_events_llm_data)
    if user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off" or \
       user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
        if num_imp_emails > 0 or num_act_events > 0 :
            notif_title = "Proactive Assistant Update"
            parts = []
            if num_imp_emails > 0 and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off": parts.append(f"{num_imp_emails} important email(s)")
            if num_act_events > 0 and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off": parts.append(f"{num_act_events} event(s) with suggestions")
            elif len(raw_calendar_events) > 0 and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off": parts.append(f"{len(raw_calendar_events)} upcoming event(s)")
            if parts:
                python_executable = sys.executable
                script_path_obj = Path(__file__).resolve()
                work_dir_obj = script_path_obj.parent
                notifier.send_macos_notification(
                    notif_title, ", ".join(parts) + " requiring attention.",
                    python_executable_for_action=str(python_executable),
                    script_to_run_on_action=str(script_path_obj),
                    working_dir_for_action=str(work_dir_obj)
                )
                notification_sent_this_cycle = True
        else:
            if sys.stdin.isatty(): print(f"{user_interface.Style.DIM}No new important items for notification.{user_interface.Style.RESET_ALL}")
            else: print(f"NOTIF_LOG: No new items for notification at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if notification_sent_this_cycle: config_manager.save_actionable_data(important_emails_llm_data, actionable_events_llm_data)
    else: config_manager.clear_actionable_data()
    print(f"{user_interface.Style.DIM}Proactive checks cycle complete.{user_interface.Style.RESET_ALL}")
    return True, important_emails_llm_data, actionable_events_llm_data


async def handle_draft_email_reply(
    gemini_client: genai.Client, MODEL_NAME: str,
    chosen_email_data: Dict[str, Any],
    initial_llm_action_text: str,
    user_config: Dict[str, Any]
) -> bool:
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a professional")
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY, "key tasks")
    gmail_mcp_url = user_config.get(config_manager.GMAIL_MCP_URL_KEY)
    calendar_mcp_url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY)
    user_id = user_config.get(config_manager.USER_EMAIL_KEY)
    current_draft_info = None
    available_slots_for_llm: Optional[List[Dict[str, str]]] = None

    if calendar_mcp_url and user_id and any(kw in initial_llm_action_text.lower() for kw in ["availability", "times", "slots", "propose", "slot", "time", "free", "suggest time"]):
        if user_interface.get_yes_no_input("Check calendar for free slots to suggest?", default_yes=True):
            date_str = user_interface.get_user_input("Date for slots (YYYY-MM-DD, 'today', 'tomorrow')", default="today")
            duration_str = user_interface.get_user_input("Meeting duration (e.g., 30m, 1h)", default="30m")
            meeting_duration_minutes = 30
            try:
                if 'h' in duration_str: parts = duration_str.split('h'); hours = int(parts[0]); minutes_part = parts[1].replace('m',''); minutes = int(minutes_part) if minutes_part else 0; meeting_duration_minutes = (hours * 60) + minutes
                elif 'm' in duration_str: meeting_duration_minutes = int(duration_str.replace('m',''))
            except ValueError: meeting_duration_minutes = 30
            target_date = None; now_ist = datetime.now(calendar_utils.IST)
            if date_str.lower() == "today": target_date = now_ist.date()
            elif date_str.lower() == "tomorrow": target_date = (now_ist + timedelta(days=1)).date()
            else:
                try: target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError: print(f"{user_interface.Fore.RED}Invalid date format.{user_interface.Style.RESET_ALL}")
            if target_date:
                work_start_h = user_config.get(config_manager.WORK_START_HOUR_KEY, 9)
                work_end_h = user_config.get(config_manager.WORK_END_HOUR_KEY, 18)
                time_min_dt = datetime(target_date.year, target_date.month, target_date.day, work_start_h, 0, 0, tzinfo=calendar_utils.IST)
                time_max_dt = datetime(target_date.year, target_date.month, target_date.day, work_end_h, 0, 0, tzinfo=calendar_utils.IST)
                time_min_iso = calendar_utils.format_datetime_to_iso_ist(time_min_dt)
                time_max_iso = calendar_utils.format_datetime_to_iso_ist(time_max_dt)
                parsed_target_date_str = target_date.strftime("%Y-%m-%d")
                async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-findslots-for-email") as slot_finder_manager:
                    if slot_finder_manager.session:
                        free_slots_result = await slot_finder_manager.get_calendar_free_slots(
                            time_min_iso_ist=time_min_iso, time_max_iso_ist=time_max_iso,
                            meeting_duration_minutes=meeting_duration_minutes,
                            user_work_start_hour=work_start_h, user_work_end_hour=work_end_h
                        )
                        if free_slots_result.get("successful"):
                            available_slots_for_llm = free_slots_result.get("free_slots", [])
                            if available_slots_for_llm: user_interface.display_free_slots(available_slots_for_llm, parsed_target_date_str)
                            else: print(f"{user_interface.Fore.YELLOW}No free slots found.{user_interface.Style.RESET_ALL}")
                        else: print(f"{user_interface.Fore.RED}Error finding slots: {free_slots_result.get('error')}{user_interface.Style.RESET_ALL}")
                    else: print(f"{user_interface.Fore.RED}Could not connect to find slots.{user_interface.Style.RESET_ALL}")
    print(f"\n{user_interface.Style.DIM}Drafting reply for '{chosen_email_data['original_email_data'].get('subject', 'N/A')}'...{user_interface.Style.RESET_ALL}")
    current_draft_info = await llm_processor.draft_email_reply_with_llm(
        gemini_client, MODEL_NAME, chosen_email_data['original_email_data'], initial_llm_action_text,
        user_persona, user_priorities, available_slots=available_slots_for_llm
    )
    while True:
        if not current_draft_info or current_draft_info.get("error"):
            error_msg = current_draft_info.get("error", "Failed to generate draft.") if current_draft_info else "Failed to generate draft."
            print(f"{user_interface.Fore.RED}Error: {error_msg}{user_interface.Style.RESET_ALL}"); return False
        draft_body = current_draft_info.get("body"); draft_subject = current_draft_info.get("subject")
        confirmation_choice = user_interface.get_send_edit_cancel_confirmation(f"Subject: {draft_subject}\n\n{draft_body}", "Email Reply")
        if confirmation_choice == "send_reply":
            recipient = current_draft_info.get("recipient_email_for_reply"); thread_id = current_draft_info.get("original_thread_id")
            if not recipient or not thread_id or not draft_body or not gmail_mcp_url or not user_id:
                print(f"{user_interface.Fore.RED}Critical info missing for send. Cannot proceed.{user_interface.Style.RESET_ALL}"); return False
            async with McpSessionManager(gmail_mcp_url, user_id, "gmail-action-send-reply") as exec_manager:
                if not exec_manager.session: print(f"{user_interface.Fore.RED}Failed Gmail session for send.{user_interface.Style.RESET_ALL}"); return False
                outcome = await exec_manager.reply_to_gmail_thread(thread_id, recipient, draft_body)
                if outcome.get("successful"):
                    print(f"{user_interface.Fore.GREEN}Success! {outcome.get('message', 'Reply sent.')}{user_interface.Style.RESET_ALL}")
                    thread_id_mark = chosen_email_data.get("original_email_data", {}).get("threadId")
                    if thread_id_mark:
                        mark_outcome = await exec_manager.mark_thread_as_read(thread_id_mark)
                        if mark_outcome.get("successful"): print(f"{user_interface.Fore.GREEN}Original thread marked read.{user_interface.Style.RESET_ALL}")
                        else: print(f"{user_interface.Fore.YELLOW}Could not mark thread read: {mark_outcome.get('error')}{user_interface.Style.RESET_ALL}")
                    return True
                else: print(f"{user_interface.Fore.RED}MCP Error sending reply: {outcome.get('error', 'Failed.')}{user_interface.Style.RESET_ALL}"); return False
        elif confirmation_choice == "edit":
            instructions = user_interface.get_user_input("Your edit instructions:")
            current_draft_info = await llm_processor.draft_email_reply_with_llm(
                gemini_client, MODEL_NAME, chosen_email_data['original_email_data'], initial_llm_action_text,
                user_persona, user_priorities, user_edit_instructions=instructions, available_slots=available_slots_for_llm
            )
        elif confirmation_choice == "cancel": print(f"{user_interface.Fore.YELLOW}Cancelled.{user_interface.Style.RESET_ALL}"); return True
        else: print(f"{user_interface.Fore.RED}Unknown choice.{user_interface.Style.RESET_ALL}"); return False

async def handle_delete_calendar_event(chosen_event_data: Dict[str, Any], user_config: Dict[str, Any]) -> bool:
    event_id = chosen_event_data.get("original_event_data", {}).get("id")
    title = chosen_event_data.get("original_event_data", {}).get("summary", "Unknown Event")
    if not event_id: print(f"{user_interface.Fore.RED}No Event ID for '{title}'. Cannot delete.{user_interface.Style.RESET_ALL}"); return False
    if user_interface.get_confirmation(f"Delete '{title}' (ID: {event_id})?", destructive=True):
        url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY); user = user_config.get(config_manager.USER_EMAIL_KEY)
        if not url or not user: print(f"{user_interface.Fore.RED}Calendar config missing.{user_interface.Style.RESET_ALL}"); return False
        async with McpSessionManager(url, user, "googlecalendar-action-delete") as manager:
            if not manager.session: print(f"{user_interface.Fore.RED}Failed session for delete.{user_interface.Style.RESET_ALL}"); return False
            outcome = await manager.delete_calendar_event(event_id)
        if outcome.get("successful"): print(f"{user_interface.Fore.GREEN}Success! {outcome.get('message', 'Event deleted.')}{user_interface.Style.RESET_ALL}"); return True
        else: print(f"{user_interface.Fore.RED}MCP Error deleting: {outcome.get('error', 'Failed.')}{user_interface.Style.RESET_ALL}"); return False
    else: print(f"{user_interface.Fore.YELLOW}Deletion cancelled.{user_interface.Style.RESET_ALL}"); return True

async def handle_update_calendar_event(chosen_event_data: Dict[str, Any], user_config: Dict[str, Any]) -> bool:
    original_details = chosen_event_data.get("original_event_data", {})
    event_id = original_details.get("id"); title = original_details.get("summary", "Unknown Event")
    if not event_id: print(f"{user_interface.Fore.RED}No Event ID for '{title}'. Cannot update.{user_interface.Style.RESET_ALL}"); return False
    url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY); user = user_config.get(config_manager.USER_EMAIL_KEY)
    if not url or not user: print(f"{user_interface.Fore.RED}Calendar config missing.{user_interface.Style.RESET_ALL}"); return False
    while True:
        ui_outcome = user_interface.get_event_update_choices(title, original_details)
        if not ui_outcome: print(f"{user_interface.Fore.YELLOW}Update cancelled.{user_interface.Style.RESET_ALL}"); return True
        if isinstance(ui_outcome, dict) and ui_outcome.get("trigger_action") == "find_free_slots":
            date_str = user_interface.get_user_input("Date (YYYY-MM-DD, 'today', 'tomorrow')", "today")
            duration_str = user_interface.get_user_input("Duration (e.g., 30m, 1h)", "30m")
            meeting_duration_minutes = 30
            try:
                if 'h' in duration_str: h,m_str = duration_str.split('h'); m_str=m_str.replace('m',''); meeting_duration_minutes = (int(h)*60) + (int(m_str) if m_str else 0)
                elif 'm' in duration_str: meeting_duration_minutes = int(duration_str.replace('m',''))
            except ValueError: meeting_duration_minutes = 30
            target_date = None; now_ist = datetime.now(calendar_utils.IST)
            if date_str.lower() == "today": target_date = now_ist.date()
            elif date_str.lower() == "tomorrow": target_date = (now_ist + timedelta(days=1)).date()
            else:
                try: target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError: print(f"{user_interface.Fore.RED}Invalid date format.{user_interface.Style.RESET_ALL}"); continue
            if target_date:
                work_start_h=user_config.get(config_manager.WORK_START_HOUR_KEY,9); work_end_h=user_config.get(config_manager.WORK_END_HOUR_KEY,18)
                min_dt=datetime(target_date.year,target_date.month,target_date.day,work_start_h,0,0,tzinfo=calendar_utils.IST)
                max_dt=datetime(target_date.year,target_date.month,target_date.day,work_end_h,0,0,tzinfo=calendar_utils.IST)
                min_iso=calendar_utils.format_datetime_to_iso_ist(min_dt); max_iso=calendar_utils.format_datetime_to_iso_ist(max_dt)
                parsed_date_str = target_date.strftime("%Y-%m-%d")
                async with McpSessionManager(url, user, "googlecalendar-findslots-in-update") as manager:
                    if manager.session:
                        slots_res = await manager.get_calendar_free_slots(min_iso,max_iso,meeting_duration_minutes,work_start_h,work_end_h)
                        if slots_res.get("successful"): user_interface.display_free_slots(slots_res.get("free_slots",[]), parsed_date_str)
                        else: print(f"{user_interface.Fore.RED}Error finding slots: {slots_res.get('error')}{user_interface.Style.RESET_ALL}")
                    else: print(f"{user_interface.Fore.RED}Could not connect for slots.{user_interface.Style.RESET_ALL}")
            continue
        final_updates = ui_outcome
        async with McpSessionManager(url, user, "googlecalendar-action-update") as manager:
            if not manager.session: print(f"{user_interface.Fore.RED}Failed session for update.{user_interface.Style.RESET_ALL}"); return False
            outcome = await manager.update_calendar_event(event_id, updates=final_updates)
        if outcome.get("successful"): print(f"{user_interface.Fore.GREEN}Success! {outcome.get('message', 'Event updated.')}{user_interface.Style.RESET_ALL}"); return True
        else: print(f"{user_interface.Fore.RED}MCP Error updating: {outcome.get('error', 'Failed.')}{user_interface.Style.RESET_ALL}"); return False

async def handle_create_calendar_event(gemini_client: genai.Client, MODEL_NAME: str, llm_suggestion_text: str, original_context_text: Optional[str], user_config: Dict[str, Any]) -> bool:
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a professional")
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY, "key tasks")
    current_time_iso = datetime.now(timezone.utc).isoformat()
    url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY); user_id = user_config.get(config_manager.USER_EMAIL_KEY)
    if not url or not user_id: print(f"{user_interface.Fore.RED}Calendar config missing.{user_interface.Style.RESET_ALL}"); return False
    current_details = await llm_processor.parse_event_creation_details_from_suggestion(
        gemini_client, MODEL_NAME, llm_suggestion_text, original_context_text, user_persona, user_priorities, current_time_iso
    )
    if not current_details or current_details.get("error"):
        print(f"{user_interface.Fore.RED}Could not parse event: {current_details.get('error', 'Failed.') if current_details else 'None.'}{user_interface.Style.RESET_ALL}"); return False
    while True:
        final_details_trigger = user_interface.get_event_creation_confirmation_and_edits(current_details)
        if not final_details_trigger: print(f"{user_interface.Fore.YELLOW}Creation cancelled.{user_interface.Style.RESET_ALL}"); return True
        if isinstance(final_details_trigger, dict) and final_details_trigger.get("trigger_action") == "find_free_slots":
            date_str = user_interface.get_user_input("Date (YYYY-MM-DD, 'today', 'tomorrow')", "today")
            duration_str = user_interface.get_user_input("Duration (e.g., 30m, 1h)", f"{current_details.get('event_duration_hour',0)}h{current_details.get('event_duration_minutes',30)}m")
            meeting_duration_minutes = 30
            try:
                if 'h' in duration_str: h,m_str = duration_str.split('h'); m_str=m_str.replace('m',''); meeting_duration_minutes = (int(h)*60) + (int(m_str) if m_str else 0)
                elif 'm' in duration_str: meeting_duration_minutes = int(duration_str.replace('m',''))
            except ValueError: meeting_duration_minutes = 30
            target_date = None; now_ist = datetime.now(calendar_utils.IST)
            if date_str.lower() == "today": target_date = now_ist.date()
            elif date_str.lower() == "tomorrow": target_date = (now_ist + timedelta(days=1)).date()
            else:
                try: target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError: print(f"{user_interface.Fore.RED}Invalid date format.{user_interface.Style.RESET_ALL}"); continue
            if target_date:
                work_start_h=user_config.get(config_manager.WORK_START_HOUR_KEY,9); work_end_h=user_config.get(config_manager.WORK_END_HOUR_KEY,18)
                min_dt=datetime(target_date.year,target_date.month,target_date.day,work_start_h,0,0,tzinfo=calendar_utils.IST)
                max_dt=datetime(target_date.year,target_date.month,target_date.day,work_end_h,0,0,tzinfo=calendar_utils.IST)
                min_iso=calendar_utils.format_datetime_to_iso_ist(min_dt); max_iso=calendar_utils.format_datetime_to_iso_ist(max_dt)
                parsed_date_str = target_date.strftime("%Y-%m-%d")
                async with McpSessionManager(url, user_id, "googlecalendar-findslots-for-create") as manager:
                    if manager.session:
                        slots_res = await manager.get_calendar_free_slots(min_iso,max_iso,meeting_duration_minutes,work_start_h,work_end_h)
                        if slots_res.get("successful"):
                            slots = slots_res.get("free_slots",[]); user_interface.display_free_slots(slots, parsed_date_str)
                            if slots and user_interface.get_yes_no_input("Use one of these slots?", False):
                                choice = user_interface.get_user_input(f"Slot number (1-{len(slots)}) or 'n'")
                                if choice.isdigit():
                                    idx = int(choice) - 1
                                    if 0 <= idx < len(slots):
                                        dt_obj = calendar_utils.parse_iso_to_ist(slots[idx]['start'])
                                        if dt_obj:
                                            current_details["start_datetime"] = dt_obj.strftime("%Y-%m-%dT%H:%M:%S")
                                            current_details["timezone"] = "Asia/Kolkata" # Assuming IST for now
                                            current_details["event_duration_hour"] = meeting_duration_minutes // 60
                                            current_details["event_duration_minutes"] = meeting_duration_minutes % 60
                        else: print(f"{user_interface.Fore.RED}Error finding slots: {slots_res.get('error')}{user_interface.Style.RESET_ALL}")
                    else: print(f"{user_interface.Fore.RED}Could not connect for slots.{user_interface.Style.RESET_ALL}")
            continue
        final_event_details = final_details_trigger
        async with McpSessionManager(url, user_id, "googlecalendar-action-create") as manager:
            if not manager.session: print(f"{user_interface.Fore.RED}Failed session for create.{user_interface.Style.RESET_ALL}"); return False
            outcome = await manager.create_calendar_event(final_event_details)
        if outcome.get("successful"): print(f"{user_interface.Fore.GREEN}Success! {outcome.get('message', 'Event created.')}{user_interface.Style.RESET_ALL}"); return True
        else: print(f"{user_interface.Fore.RED}MCP Error creating: {outcome.get('error', 'Failed.')}{user_interface.Style.RESET_ALL}"); return False

def display_welcome_art():
    try:
        script_dir = Path(__file__).resolve().parent
        ascii_file_path = script_dir / "ascii.txt"
        if ascii_file_path.exists():
            with open(ascii_file_path, 'r', encoding='utf-8') as f: art = f.read()
            print(f"{user_interface.Fore.BLUE}{art}{user_interface.Style.RESET_ALL}")
        print(f"{user_interface.Style.BRIGHT}MCliPPy - Your Proactive MCP Assistant{user_interface.Style.RESET_ALL}")
        print("-" * 60)
    except Exception as e:
        print(f"Could not display art: {e}")
        print(f"{user_interface.Style.BRIGHT}MCliPPy - Proactive MCP Assistant{user_interface.Style.RESET_ALL}")
        print("-" * 60)

import json
from typing import Dict, List, Optional, Any, Union
from mcp import ClientSession
from mcp.types import Tool, CallToolResult
import asyncio
import weakref


class ConversationHistory:
    """Manages conversation history for the assistant"""

    def __init__(self):
        self.history: List[genai_types.Content] = []

    def add_user_message(self, message: str):
        """Add a user message to history"""
        self.history.append(genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=message)]
        ))

    def add_assistant_message(self, message: str):
        """Add an assistant message to history"""
        self.history.append(genai_types.Content(
            role="model",
            parts=[genai_types.Part.from_text(text=message)]
        ))

    def add_function_call(self, function_call, tool_name: str, tool_result: Dict[str, Any]):
        """Add function call and result to history"""
        # Add the function call
        self.history.append(genai_types.Content(
            role="model",
            parts=[genai_types.Part.from_function_call(
                name=function_call.name,
                args=dict(function_call.args)
            )]
        ))

        # Add the function response
        self.history.append(genai_types.Content(
            role="tool",
            parts=[genai_types.Part.from_function_response(
                name=tool_name,
                response=tool_result
            )]
        ))

    def get_history(self) -> List[genai_types.Content]:
        """Get the conversation history"""
        return self.history.copy()

    def clear_history(self):
        """Clear conversation history"""
        self.history.clear()

    def get_recent_history(self, max_exchanges: int = 10) -> List[genai_types.Content]:
        """Get recent conversation history limited by exchanges"""
        # Limit to prevent context window overflow
        if len(self.history) <= max_exchanges * 2:  # Rough estimate
            return self.history.copy()
        else:
            return self.history[-(max_exchanges * 2):]

# Create global conversation history
conversation_history = ConversationHistory()

async def handle_nli_command(
    user_query: str,
    gemini_client: genai.Client,
    MODEL_NAME: str,
    user_config: Dict[str, Any],
    gmail_mcp_manager: Optional[McpSessionManager],
    calendar_mcp_manager: Optional[McpSessionManager]
):
    print(f"{user_interface.Style.DIM}Processing your request: '{user_query}'...{user_interface.Style.RESET_ALL}")

    # Add user message to conversation history
    conversation_history.add_user_message(user_query)
    try:
        # Connect managers and choose primary session based on query intent
        available_managers = {}
        primary_session = None
        primary_manager = None

        # Smart session selection based on query content
        query_lower = user_query.lower()
        calendar_keywords = ["calendar", "event", "schedule", "meeting", "free", "time", "slot", "book", "create event"]
        gmail_keywords = ["email", "gmail", "mail", "message", "reply", "send", "inbox", "fetch"]

        prefers_calendar = any(keyword in query_lower for keyword in calendar_keywords)
        prefers_gmail = any(keyword in query_lower for keyword in gmail_keywords)

        # Connect both managers
        if gmail_mcp_manager:
            await gmail_mcp_manager.ensure_connected()
            if gmail_mcp_manager.session:
                available_managers['gmail'] = gmail_mcp_manager
                print(f"   Gmail session ready with {len(gmail_mcp_manager.tools)} tools")

        if calendar_mcp_manager:
            await calendar_mcp_manager.ensure_connected()
            if calendar_mcp_manager.session:
                available_managers['calendar'] = calendar_mcp_manager
                print(f"   Calendar session ready with {len(calendar_mcp_manager.tools)} tools")

        # Choose primary session based on query intent
        if prefers_calendar and 'calendar' in available_managers:
            primary_session = available_managers['calendar'].session
            primary_manager = available_managers['calendar']
            primary_type = 'calendar'
        elif prefers_gmail and 'gmail' in available_managers:
            primary_session = available_managers['gmail'].session
            primary_manager = available_managers['gmail']
            primary_type = 'gmail'
        elif 'gmail' in available_managers:  # Default to gmail
            primary_session = available_managers['gmail'].session
            primary_manager = available_managers['gmail']
            primary_type = 'gmail'
        elif 'calendar' in available_managers:  # Fallback to calendar
            primary_session = available_managers['calendar'].session
            primary_manager = available_managers['calendar']
            primary_type = 'calendar'

        if not primary_session:
            response_text = "I can't access Gmail or Calendar tools right now. Please ensure they are connected."
            conversation_history.add_assistant_message(response_text)
            user_interface.display_nli_llm_response(response_text)
            return

        print(f"   Using {primary_type} as primary session for this query")

        # Store routing information for tool execution
        user_config['_available_managers'] = available_managers
        user_config['_conversation_history'] = conversation_history
        user_config['_primary_manager'] = primary_manager

        # Pass ONLY the primary session to avoid conflicts
        llm_response_or_fc = await llm_processor.get_llm_tool_call_from_natural_language(
            gemini_client, MODEL_NAME, user_query, [primary_session], user_config
        )

        if isinstance(llm_response_or_fc, str):
            conversation_history.add_assistant_message(llm_response_or_fc)
            user_interface.display_nli_llm_response(llm_response_or_fc)
            return

        if isinstance(llm_response_or_fc, dict) and "function_call" in llm_response_or_fc:
            function_call_obj = llm_response_or_fc["function_call"]
            gemini_content_parts_for_next_turn = llm_response_or_fc["model_response_parts"]

            tool_name = function_call_obj.name
            tool_args = dict(function_call_obj.args)

            if user_interface.display_tool_confirmation_prompt(tool_name, tool_args):
                # Find the manager that has this tool
                target_manager: Optional[McpSessionManager] = None

                # First check primary manager
                if tool_name in primary_manager.tools:
                    target_manager = primary_manager
                    print(f"   Routing {tool_name} to primary {primary_type} manager")
                else:
                    # Check other managers
                    for manager_name, manager in available_managers.items():
                        if tool_name in manager.tools:
                            target_manager = manager
                            print(f"   Routing {tool_name} to {manager_name} manager")
                            break

                if target_manager:
                    # Ensure connection is still active
                    await target_manager.ensure_connected()

                    # Execute the tool
                    raw_mcp_tool_outcome = await target_manager.ensure_auth_and_call_tool(tool_name, tool_args)

                    # Process the result
                    processed_tool_result_for_llm: Dict[str, Any]

                    if isinstance(raw_mcp_tool_outcome, dict):
                        processed_tool_result_for_llm = raw_mcp_tool_outcome
                    elif hasattr(raw_mcp_tool_outcome, 'content') and raw_mcp_tool_outcome.content and \
                        hasattr(raw_mcp_tool_outcome.content[0], 'text') and \
                        isinstance(raw_mcp_tool_outcome.content[0].text, str):
                        try:
                            composio_json_response_str = raw_mcp_tool_outcome.content[0].text
                            parsed_composio_response = json.loads(composio_json_response_str)
                            processed_tool_result_for_llm = parsed_composio_response
                        except json.JSONDecodeError:
                            err_msg = "Failed to parse JSON response from MCP tool."
                            print(f"{user_interface.Fore.RED}ASSISTANT_NLI_ERROR: {err_msg} Raw text: {raw_mcp_tool_outcome.content[0].text[:200]}{user_interface.Style.RESET_ALL}")
                            processed_tool_result_for_llm = {"successful": False, "error": err_msg}
                        except (IndexError, Exception) as e:
                            err_msg = f"Error processing MCP tool result: {str(e)}"
                            print(f"{user_interface.Fore.RED}ASSISTANT_NLI_ERROR: {err_msg}{user_interface.Style.RESET_ALL}")
                            processed_tool_result_for_llm = {"successful": False, "error": err_msg}
                    else:
                        processed_tool_result_for_llm = {"successful": False, "error": "Unexpected tool response format"}

                    # Add function call and result to conversation history
                    conversation_history.add_function_call(function_call_obj, tool_name, processed_tool_result_for_llm)

                    # Get final response
                    final_llm_response_text = await llm_processor.get_final_response_after_tool_execution(
                        gemini_client, MODEL_NAME,
                        user_query,
                        gemini_content_parts_for_next_turn,
                        tool_name,
                        processed_tool_result_for_llm,
                        [primary_session],  # Pass primary session
                        user_config
                    )

                    conversation_history.add_assistant_message(final_llm_response_text)
                    user_interface.display_nli_llm_response(final_llm_response_text)
                else:
                    error_msg = f"Sorry, I don't know how to execute the tool '{tool_name}'. It might not be available in the current session."
                    conversation_history.add_assistant_message(error_msg)
                    user_interface.display_nli_llm_response(error_msg)
            else:
                cancel_msg = "Action cancelled by user."
                conversation_history.add_assistant_message(cancel_msg)
                user_interface.display_nli_llm_response(cancel_msg)

    except Exception as e:
        error_msg = f"Sorry, I encountered an error: {str(e)}"
        print(f"{user_interface.Fore.RED}Error in handle_nli_command: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()
        conversation_history.add_assistant_message(error_msg)
        user_interface.display_nli_llm_response(error_msg)



async def main_assistant_entry(run_mode: str = "normal"):
    if sys.stdin.isatty() and run_mode == "normal":
        display_welcome_art()

    if not config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY):
        print(f"ASSISTANT_ERROR: {config_manager.ENV_GOOGLE_API_KEY} not set. Exiting.")
        return 1

    user_configuration = config_manager.load_user_config()
    if not user_configuration or not user_configuration.get(config_manager.USER_EMAIL_KEY):
        if sys.stdin.isatty():
            user_configuration = run_signup_flow()
            if not (user_configuration and user_configuration.get(config_manager.USER_EMAIL_KEY)):
                print(f"{user_interface.Fore.RED}Signup incomplete. Exiting.{user_interface.Style.RESET_ALL}"); return 1
            user_configuration = config_manager.load_user_config()
        else:
            print("ASSISTANT_ERROR: Not configured and not interactive. Run manually once."); return 1

    if sys.stdin.isatty():
        print(f"\n{user_interface.Fore.GREEN}Welcome back, {user_configuration.get(config_manager.USER_EMAIL_KEY)}!{user_interface.Style.RESET_ALL}")

    google_api_key = config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY)
    gemini_client = None
    try:
        gemini_client = genai.Client(api_key=google_api_key)
        if sys.stdin.isatty():
            print(f"{user_interface.Fore.GREEN}Gemini client initialized successfully for model {MODEL_NAME}.{user_interface.Style.RESET_ALL}")
    except Exception as e:
        print(f"{user_interface.Fore.RED}Failed to initialize Gemini client: {e}{user_interface.Style.RESET_ALL}"); traceback.print_exc(); return 1

    # --- MCP Session Managers (initialized once per app, if configured) ---
    # These will be passed around. They manage their own connection state.
    gmail_mcp_url = user_configuration.get(config_manager.GMAIL_MCP_URL_KEY)
    calendar_mcp_url = user_configuration.get(config_manager.CALENDAR_MCP_URL_KEY)
    user_id = user_configuration.get(config_manager.USER_EMAIL_KEY)

    # Initialize managers but don't connect yet. Connection happens within `async with`.
    # We need them for NLI mode even if proactive checks are skipped.
    gmail_manager: Optional[McpSessionManager] = None
    if gmail_mcp_url and user_id:
        gmail_manager = McpSessionManager(gmail_mcp_url, user_id, "gmail")

    calendar_manager: Optional[McpSessionManager] = None
    if calendar_mcp_url and user_id:
        calendar_manager = McpSessionManager(calendar_mcp_url, user_id, "googlecalendar")

    # --- Main Interaction Loop ---
    current_mode = run_mode # "normal" (NLI first) or "from_notification" (Actionables first)

    actionable_emails_list = []
    actionable_events_list = []
    # For "from_notification", data is loaded once. For "normal", it's loaded if user switches to actionables.
    data_loaded_for_actionables_view = False

    while True:
        if current_mode == "normal": # NLI-first
            nli_command_type, user_text_input = user_interface.get_nli_chat_input(run_mode="normal")

            if nli_command_type == "quit_assistant":
                break

            elif nli_command_type == "clear_history":
                if user_interface.confirm_clear_history():
                    conversation_history.clear_history()
                    user_interface.display_history_cleared()
                else:
                    print(f"{user_interface.Fore.YELLOW}History clearing cancelled.{user_interface.Style.RESET_ALL}")
                continue

            elif nli_command_type == "show_history":
                user_interface.display_conversation_history(conversation_history)
                continue

            elif nli_command_type == "show_actionables":
                current_mode = "actionables_from_nli" # Switch mode
                data_loaded_for_actionables_view = False # Force reload/check
                continue

            elif nli_command_type == "nli_query":
                if user_text_input: # Ensure there's a query
                    # Connect managers if not already connected (idempotent)
                    if gmail_manager: await gmail_manager.ensure_connected()
                    if calendar_manager: await calendar_manager.ensure_connected()
                    await handle_nli_command(user_text_input, gemini_client, MODEL_NAME, user_configuration, gmail_manager, calendar_manager)
                else:
                    print(f"{user_interface.Fore.YELLOW}Empty NLI query, please type a command.{user_interface.Style.RESET_ALL}")
                continue # Stay in NLI mode

            else:
                print(f"{user_interface.Fore.YELLOW}Unknown command. Type a question or use the available commands.{user_interface.Style.RESET_ALL}")
                continue


        elif current_mode in ["from_notification", "actionables_from_nli"]:
            if not data_loaded_for_actionables_view:
                 # Active day/hour check for non-notification triggered runs (e.g. launchd direct call or switching from NLI)
                active_days = user_configuration.get(config_manager.SCHED_ACTIVE_DAYS_KEY, [])
                active_start_hour = user_configuration.get(config_manager.SCHED_ACTIVE_START_HOUR_KEY, 0)
                active_end_hour = user_configuration.get(config_manager.SCHED_ACTIVE_END_HOUR_KEY, 23)
                now_ist = datetime.now(calendar_utils.IST)

                if current_mode == "actionables_from_nli" and not (now_ist.weekday() in active_days and active_start_hour <= now_ist.hour < active_end_hour):
                    print(f"{user_interface.Style.DIM}Current time {now_ist.strftime('%A %H:%M')} is outside active schedule for proactive checks. Displaying potentially stale data or NLI only.{user_interface.Style.RESET_ALL}")
                    # Allow viewing stale data if any, or just go back to NLI if user came from there
                    if not config_manager.load_actionable_data(max_age_seconds=30*60): # Check if any reasonably recent data
                        print(f"{user_interface.Fore.YELLOW}No recent actionable data found, and outside schedule for fresh checks.{user_interface.Style.RESET_ALL}")
                        if current_mode == "actionables_from_nli": # If user explicitly asked from NLI
                            current_mode = "normal" # Switch back to NLI
                            continue
                        # If from_notification and outside schedule, something is odd, but proceed with attempt to load

                if current_mode == "from_notification" and not data_loaded_for_actionables_view:
                    print(f"{user_interface.Style.DIM}Loading data from last notification check...{user_interface.Style.RESET_ALL}")
                    loaded_data = config_manager.load_actionable_data()
                    if loaded_data:
                        actionable_emails_list = loaded_data.get("emails", [])
                        actionable_events_list = loaded_data.get("events", [])
                        config_manager.clear_actionable_data()
                        data_loaded_for_actionables_view = True
                    else:
                        print(f"{user_interface.Fore.YELLOW}Could not load recent actionable data. Performing a fresh check...{user_interface.Style.RESET_ALL}")
                        # Fall through to fresh check

                if not data_loaded_for_actionables_view: # Fresh check needed
                    # Connect managers if not already connected (idempotent)
                    if gmail_manager: await gmail_manager.ensure_connected()
                    if calendar_manager: await calendar_manager.ensure_connected()

                    can_proceed, emails, events = await perform_proactive_checks(user_configuration, gemini_client, MODEL_NAME)
                    if not can_proceed: # Auth needed
                        print(f"{user_interface.Fore.YELLOW}Cycle paused: user action (e.g., auth) required. Exiting this run.{user_interface.Style.RESET_ALL}")
                        break # Exit main loop
                    actionable_emails_list = emails
                    actionable_events_list = events
                    data_loaded_for_actionables_view = True

            if not actionable_emails_list and not actionable_events_list:
                print(f"\n{user_interface.Fore.GREEN}No actionable items found to interact with.{user_interface.Style.RESET_ALL}")
                if current_mode == "actionables_from_nli": # If user came here from NLI
                    current_mode = "normal" # Go back to NLI as there's nothing to do here
                    continue
                else: # from_notification and no items, just quit
                    break


            action_choice_data = user_interface.display_processed_data_and_get_action(
                actionable_emails_list, actionable_events_list,
                first_time_display=True, # Always treat as first display when entering this mode
                run_mode=current_mode
            )

            if not action_choice_data: # Invalid input from user in action_choice
                if not actionable_emails_list and not actionable_events_list: break # No items, user likely hit enter
                else: print(f"{user_interface.Fore.YELLOW}Try selection again or a command.{user_interface.Style.RESET_ALL}"); continue

            action_type, item_idx, action_idx_in_llm, raw_choice = action_choice_data

            if action_type == "quit_assistant": break
            if action_type == "done": # User is done with actionables for this round
                if current_mode == "actionables_from_nli": # If came from NLI, go back to NLI
                    current_mode = "normal"
                    continue
                else: # If was from_notification, then done means quit.
                    break
            if action_type == "redisplay": data_loaded_for_actionables_view = False; continue # Force refresh
            if action_type == "chat_nli": current_mode = "normal"; continue # Switch to NLI mode
            if action_type == "no_actionables_chat_nli": # Special case from UI if no items
                current_mode = "normal"; continue

            # If user typed an NLI command directly while in actionable menu (normal mode only)
            if action_type == "nli_command_from_actionable_menu":
                if raw_choice: # Ensure there's a query
                    # Connect managers if not already connected (idempotent)
                    if gmail_manager: await gmail_manager.ensure_connected()
                    if calendar_manager: await calendar_manager.ensure_connected()
                    await handle_nli_command(raw_choice, gemini_client, MODEL_NAME, user_configuration, gmail_manager, calendar_manager)
                current_mode = "normal" # Assume they want to stay in NLI after this
                continue


            action_succeeded = False
            # --- Quick Action Dispatch Logic ---
            if action_type == "email":
                chosen_email = actionable_emails_list[item_idx]
                action_text = chosen_email['suggested_actions'][action_idx_in_llm]
                # ... (call handle_draft_email_reply or handle_create_calendar_event from email context)
                if "draft" in action_text.lower() or "reply" in action_text.lower(): # Simplified
                     action_succeeded = await handle_draft_email_reply(gemini_client, MODEL_NAME, chosen_email, action_text, user_configuration)
                elif "create calendar event" in action_text.lower() or "schedule" in action_text.lower():
                    ctx_text = chosen_email.get("original_email_data", {}).get("messageText") or chosen_email.get("original_email_data", {}).get("snippet","")
                    action_succeeded = await handle_create_calendar_event(gemini_client, MODEL_NAME, action_text, ctx_text, user_configuration)
                else: print(f"Action '{action_text}' for email not implemented.")
                if action_succeeded: actionable_emails_list.pop(item_idx) # Remove if handled

            elif action_type == "event":
                chosen_event = actionable_events_list[item_idx]
                action_text = chosen_event['suggested_actions'][action_idx_in_llm]
                # ... (call handle_update_calendar_event, handle_delete_calendar_event, or handle_create_calendar_event from event context)
                if "update" in action_text.lower():
                    action_succeeded = await handle_update_calendar_event(chosen_event, user_configuration)
                elif "delete" in action_text.lower() or "cancel" in action_text.lower():
                    action_succeeded = await handle_delete_calendar_event(chosen_event, user_configuration)
                elif "create" in action_text.lower() or "schedule" in action_text.lower():
                    ctx_text = chosen_event.get("original_event_data", {}).get("summary", "related event")
                    action_succeeded = await handle_create_calendar_event(gemini_client, MODEL_NAME, action_text, ctx_text, user_configuration)
                else: print(f"Action '{action_text}' for event not implemented.")
                if action_succeeded: actionable_events_list.pop(item_idx) # Remove if handled

            if action_succeeded: data_loaded_for_actionables_view = False # Force re-display if an item was removed/changed

    # Cleanup MCP sessions if they were used
    if gmail_manager: await gmail_manager.disconnect_if_connected()
    if calendar_manager: await calendar_manager.disconnect_if_connected()

    print(f"\n{user_interface.Style.DIM}Proactive Assistant Session Ended at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.{user_interface.Style.RESET_ALL}")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proactive Assistant")
    parser.add_argument(
        "--from-notification",
        action="store_true",
        help="Indicates the script is run from a notification action."
    )
    args = parser.parse_args()

    current_run_mode = "from_notification" if args.from_notification else "normal"

    try:
        exit_code = asyncio.run(main_assistant_entry(run_mode=current_run_mode))
        sys.exit(exit_code if isinstance(exit_code, int) else 0)
    except KeyboardInterrupt:
        print(f"\n{user_interface.Fore.YELLOW}Assistant stopped by user. Goodbye!{user_interface.Style.RESET_ALL}")
        sys.exit(0)
    except SystemExit as e:
        sys.exit(e.code if isinstance(e.code, int) else 0)
    except Exception as e:
        print(f"{user_interface.Fore.RED}An unexpected error occurred in the main execution: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()
        sys.exit(1)
