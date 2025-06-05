# llm_processor.py
import json
import traceback
import asyncio
from typing import Dict, List, Optional, Any, Tuple, Union

from google import genai # Main SDK
from google.genai import types as genai_types # For types like GenerateContentConfig, Part, Content
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import config_manager
import calendar_utils # For parsing slots if needed in future prompts
import user_interface # For colored output (though ideally not directly from here)
from mcp_handler import McpSessionManager

# --- Email Processing ---
async def process_emails_with_llm(
    gemini_client: genai.Client,
    model_name: str,
    emails_data: list,
    user_persona: str,
    user_priorities: str
):
    if not emails_data:
        return []

    prompt_email_parts = []
    for i, email in enumerate(emails_data):
        subject = "No Subject"
        sender = "Unknown Sender"
        snippet = email.get("snippet", "No snippet available.")
        message_text_preview = email.get("messageText", snippet)[:500] # Limiting preview length

        if email.get("payload") and isinstance(email["payload"].get("headers"), list):
            for header in email["payload"]["headers"]:
                if header.get("name", "").lower() == "subject":
                    subject = header.get("value", "No Subject")
                if header.get("name", "").lower() == "from":
                    sender = header.get("value", "Unknown Sender")

        prompt_email_parts.append(
            f"Email {i+1}:\n"
            f"ID: {email.get('messageId', email.get('id', 'N/A'))}\n" # Use 'id' as fallback from Gmail API
            f"From: {sender}\n"
            f"Subject: {subject}\n"
            f"Snippet/Preview: {message_text_preview}\n---\n"
        )

    if not prompt_email_parts:
        print("LLM_PROCESSOR (Emails): No email content to process.")
        return []
    email_details_str = "\n".join(prompt_email_parts)

    system_prompt = f"""
You are a highly efficient AI assistant for a user whose role is: '{user_persona}'.
Their key priorities are: '{user_priorities}'.

You will be given a list of recent unread emails. Your tasks are:

Determine if each email is "important".
For important emails, provide a concise 1-2 sentence summary.
For important emails, suggest 1-3 brief, actionable next steps.
The assistant has tools for:
1. Replying to emails (e.g., "Draft a reply to confirm availability")
2. Creating new calendar events (e.g., "Create calendar event: Meeting with X about Y")
3. Updating existing calendar events (e.g., "Update event 'Team Sync' to add Google Meet", "Update event 'Project Briefing' to new time [YYYY-MM-DDTHH:MM:SS] based on this email")
4. Deleting calendar events
5. Finding free slots in the calendar
If an email discusses changes to an existing meeting (e.g., rescheduling, changing attendees, location, adding a meeting link),
try to identify the original meeting (by its title or time if mentioned) and suggest an "Update event..." action.
Clearly state what part of the event should be updated and with what new information, if discernible from the email.

If an email discusses changes to an existing meeting (e.g., rescheduling, changing attendees, location, adding a meeting link):
    1. Try to identify the original meeting by its title or time if mentioned in the email.
    2. Extract the proposed changes (e.g., new time, new attendees, request for a Meet link).
    3. Suggest an action like: "Update event '[Original Event Title Guessed]' with changes: [Details of changes, e.g., start_time to YYYY-MM-DDTHH:MM:SS, add_attendee: x@y.com, create_google_meet: true]".
    OR if the original event is unclear, suggest: "Follow up on email to clarify which event needs update for [details of changes]".


Analyze the following emails:
{email_details_str}

Please format your response as a single JSON array, where each object in the array corresponds to an email you analyzed (important or not).
Each object should have the following keys:
- "email_id": (string) The ID of the email (e.g., from "ID: ...").
- "is_important": (boolean) True if you deem it important, False otherwise.
- "summary": (string) Your 1-2 sentence summary IF is_important is true, otherwise an empty string or null.
- "suggested_actions": (array of strings) A list of 1-3 suggested actions IF is_important is true, otherwise an empty array or null.

Example of a single object in the JSON array:
{{
"email_id": "xyz789",
"is_important": true,
"summary": "John wants to move the 'Project Alpha Sync' from 2 PM to 4 PM today.",
"suggested_actions": ["Draft reply to John acknowledging request", "Update event 'Project Alpha Sync' start_datetime to today 4 PM", "Check calendar for conflicts at 4 PM"]
}}

Only include emails in your response that you have analyzed. If an email is not important, still include its object with "is_important": false.
Ensure the entire response is a valid JSON array.
"""
    processed_emails = []
    response_text_for_debugging = "Gemini call did not occur or failed before response was received."
    try:
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json"  # Direct parameter, not nested
        )
        response = await gemini_client.aio.models.generate_content(
            model=model_name,
            contents=system_prompt,
            config=config
        )
        response_text_for_debugging = response.text

        cleaned_response_text = response.text.strip()
        if cleaned_response_text.startswith("```json"):
            cleaned_response_text = cleaned_response_text[7:]
        if cleaned_response_text.endswith("```"):
            cleaned_response_text = cleaned_response_text[:-3]

        llm_output = json.loads(cleaned_response_text)

        if isinstance(llm_output, list):
            llm_output_map = {item.get("email_id"): item for item in llm_output if isinstance(item, dict)}
            for original_email_data in emails_data:
                original_id = original_email_data.get('messageId', original_email_data.get('id'))
                processed_item = llm_output_map.get(original_id)
                if processed_item:
                    processed_emails.append({
                        "original_email_data": original_email_data,
                        "is_important": processed_item.get("is_important", False),
                        "summary": processed_item.get("summary"),
                        "suggested_actions": processed_item.get("suggested_actions", [])
                    })
                else:
                     processed_emails.append({
                        "original_email_data": original_email_data,
                        "is_important": False,
                        "summary": f"LLM did not provide specific analysis for email ID: {original_id}.",
                        "suggested_actions": []
                    })
        else:
            print(f"LLM_PROCESSOR (Emails): Gemini response was not a list as expected: {type(llm_output)}")
            for original_email_data in emails_data: # Populate with error for each original email
                processed_emails.append({"original_email_data": original_email_data, "is_important": False, "summary": "LLM response format error.", "suggested_actions": []})

    except json.JSONDecodeError as e:
        print(f"LLM_PROCESSOR (Emails): Failed to decode Gemini JSON response: {e}")
        print(f"LLM_PROCESSOR (Emails): Raw response that failed parsing:\n{response_text_for_debugging}")
        for original_email_data in emails_data: # Populate with error
             processed_emails.append({"original_email_data": original_email_data, "is_important": False, "summary": "LLM JSON parsing error.", "suggested_actions": []})
    except Exception as e:
        print(f"LLM_PROCESSOR (Emails): Error during Gemini API call: {e}")
        print(f"LLM_PROCESSOR (Emails): Raw response that might have caused error (if available):\n{response_text_for_debugging}")
        traceback.print_exc()
        for original_email_data in emails_data: # Populate with error
             processed_emails.append({"original_email_data": original_email_data, "is_important": False, "summary": "LLM API call error.", "suggested_actions": []})
    return processed_emails


# --- Calendar Event Processing ---
async def process_calendar_events_with_llm(
    gemini_client: genai.Client,
    model_name: str,
    events_data: list,
    user_config: Dict[str, Any],
    user_persona: str,
    user_priorities: str
):
    if not events_data:
        return []

    prompt_event_parts = []
    for i, event in enumerate(events_data):
        summary = event.get("summary", "No Title")
        event_id = event.get("id", "N/A")
        start_time_obj = calendar_utils.parse_iso_to_ist(event.get("start", {}).get("dateTime", ""))
        end_time_obj = calendar_utils.parse_iso_to_ist(event.get("end", {}).get("dateTime", ""))

        start_time_display = start_time_obj.strftime("%Y-%m-%d %I:%M %p %Z") if start_time_obj else "No Start Time"
        end_time_display = end_time_obj.strftime("%Y-%m-%d %I:%M %p %Z") if end_time_obj else "No End Time"

        description_snippet = event.get("description", "No description")[:150] # Limit snippet

        prompt_event_parts.append(
            f"Event {i+1} (ID: {event_id}):\n"
            f"  Title: {summary}\n"
            f"  Start: {start_time_display}\n" # Use formatted time
            f"  End: {end_time_display}\n"     # Use formatted time
            f"  Description Snippet: {description_snippet}\n---\n"
        )

    if not prompt_event_parts:
        print("LLM_PROCESSOR (Calendar): No event content to process for LLM.")
        return []
    event_details_str = "\n".join(prompt_event_parts)

    system_prompt = f"""
You are a highly efficient AI assistant for a user whose role is: '{user_persona}'.
Their key priorities are: '{user_priorities}'.

You will be given a list of their upcoming calendar events from Google Calendar.
Your tasks are:
1. For EACH event, provide a very brief highlight or summary.
2. For EACH event, suggest 1-3 brief, actionable next steps using calendar tools.
   The assistant has tools to:
     - Delete an event (e.g., "Cancel this meeting")
     - Update an event's details (e.g., title, time, description, attendees, add Google Meet) -> Suggest as "Update this event's details"
     - Create a new event
     - Find free time slots

   Focus on concrete actions related to managing the calendar event itself or follow-ups.
   Example suggestions: "Delete this event", "Update this event's details", "Schedule a 30-min follow-up".

Analyze the following events:
{event_details_str}

Please format your response as a single JSON array.
Each object in the array MUST correspond to an event you analyzed and MUST have the following keys:
- "event_id": (string) The ID of the event (e.g., from "Event 1 (ID: ...)").
- "summary_llm": (string) Your brief highlight/summary for this event.
- "suggested_actions": (array of strings) A list of 1-3 suggested actions for this event. If no specific actions are obvious, provide an empty array or a generic suggestion like "Review event details".

Ensure the entire response is a valid JSON array.
"""

    processed_events = []
    response_text_for_debugging = "Gemini call did not occur or failed before response was received."
    try:
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json"
        )

        response = await gemini_client.aio.models.generate_content(
            model=model_name,
            contents=[system_prompt],
            config=config
        )
        response_text_for_debugging = response.text

        cleaned_response_text = response.text.strip()
        if cleaned_response_text.startswith("```json"):
            cleaned_response_text = cleaned_response_text[7:]
        if cleaned_response_text.endswith("```"):
            cleaned_response_text = cleaned_response_text[:-3]
        llm_output = json.loads(cleaned_response_text)

        if isinstance(llm_output, list):
            llm_output_map = {item.get("event_id"): item for item in llm_output if isinstance(item, dict)}
            for original_event_data in events_data:
                original_id = original_event_data.get('id')
                processed_item = llm_output_map.get(original_id)
                if processed_item:
                    processed_events.append({
                        "original_event_data": original_event_data,
                        "summary_llm": processed_item.get("summary_llm"),
                        "suggested_actions": processed_item.get("suggested_actions", [])
                    })
                else:
                     processed_events.append({
                        "original_event_data": original_event_data,
                        "summary_llm": f"LLM did not provide specific analysis for event ID: {original_id}.",
                        "suggested_actions": []
                    })
        else:
            print(f"LLM_PROCESSOR (Calendar): Gemini response was not a list as expected: {type(llm_output)}")
            for original_event_data in events_data:
                processed_events.append({"original_event_data": original_event_data, "summary_llm": "LLM response format error.", "suggested_actions": []})
    except json.JSONDecodeError as e:
        print(f"LLM_PROCESSOR (Calendar): Failed to decode Gemini JSON response: {e}")
        print(f"LLM_PROCESSOR (Calendar): Raw response that failed parsing:\n{response_text_for_debugging}")
        for original_event_data in events_data:
             processed_events.append({"original_event_data": original_event_data, "summary_llm": "LLM JSON parsing error.", "suggested_actions": []})
    except Exception as e:
        print(f"LLM_PROCESSOR (Calendar): Error during Gemini API call: {e}")
        print(f"LLM_PROCESSOR (Calendar): Raw response that might have caused error (if available):\n{response_text_for_debugging}")
        traceback.print_exc()
        for original_event_data in events_data:
             processed_events.append({"original_event_data": original_event_data, "summary_llm": "LLM API call error.", "suggested_actions": []})
    return processed_events


# --- Email Reply Drafting ---
async def draft_email_reply_with_llm(
    gemini_client: genai.Client,
    model_name: str,
    original_email_data: Dict[str, Any],
    action_sentiment: str,
    user_persona: str,
    user_priorities: str,
    user_edit_instructions: Optional[str] = None,
    available_slots: Optional[List[Dict[str, str]]] = None
) -> Dict[str, Any]:
    if not original_email_data:
        return {"error": "Original email data not provided."}

    original_subject = "No Subject"
    original_sender_email_only = None
    original_sender_full_header = "Unknown Sender"
    original_thread_id = original_email_data.get("threadId", "N/A") # Get threadId for reply

    payload = original_email_data.get("payload")
    if payload and isinstance(payload.get("headers"), list):
        for header in payload["headers"]:
            header_name_lower = header.get("name", "").lower()
            if header_name_lower == "from":
                original_sender_full_header = header.get("value", "Unknown Sender")
                # Robust email parsing from 'From' header
                if "<" in original_sender_full_header and ">" in original_sender_full_header:
                    start_index = original_sender_full_header.find("<") + 1
                    end_index = original_sender_full_header.find(">")
                    if start_index < end_index:
                        original_sender_email_only = original_sender_full_header[start_index:end_index].strip()
                elif "@" in original_sender_full_header: # Fallback if no < >
                    parts = original_sender_full_header.split()
                    for part in parts:
                        if "@" in part and "." in part:
                            original_sender_email_only = part.strip().strip('<>')
                            break
                    if not original_sender_email_only:
                         original_sender_email_only = original_sender_full_header.strip()


            elif header_name_lower == "subject":
                original_subject = header.get("value", "No Subject")

    if not original_sender_email_only: # Fallback to top-level sender if header parsing failed
        composio_top_level_sender = original_email_data.get("sender")
        if composio_top_level_sender:
            original_sender_full_header = composio_top_level_sender
            if "<" in composio_top_level_sender and ">" in composio_top_level_sender:
                start_index = composio_top_level_sender.find("<") + 1
                end_index = composio_top_level_sender.find(">")
                if start_index < end_index:
                    original_sender_email_only = composio_top_level_sender[start_index:end_index].strip()
            elif "@" in composio_top_level_sender : # Fallback
                 parts = composio_top_level_sender.split()
                 for part in parts:
                     if "@" in part and "." in part:
                         original_sender_email_only = part.strip().strip('<>')
                         break
                 if not original_sender_email_only:
                     original_sender_email_only = composio_top_level_sender.strip()


    if not original_sender_email_only:
        print(f"LLM_PROCESSOR (Draft Reply): CRITICAL - Could not determine a valid recipient email for the reply from header or sender field.")
        return {"error": "Could not determine a valid recipient email for the reply."}

    original_body_for_llm = original_email_data.get("messageText", original_email_data.get("snippet", "No content available."))
    if not original_body_for_llm or original_body_for_llm == "No content available.": # Attempt to decode payload if messageText is poor
        if payload and isinstance(payload.get("parts"), list):
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                    try:
                        import base64
                        decoded_body = base64.urlsafe_b64decode(part["body"]["data"].encode('ASCII')).decode('utf-8')
                        original_body_for_llm = decoded_body
                        break
                    except Exception: pass # Keep snippet if decoding fails

    max_body_length_for_prompt = 1500
    original_body_for_llm_preview = (original_body_for_llm[:max_body_length_for_prompt] + "\n[...message truncated...]") if len(original_body_for_llm) > max_body_length_for_prompt else original_body_for_llm

    prompt_construction_parts = [
        f"You are an AI assistant helping a user draft an email reply. You have access to the user's persona and priorities. However, these are for your general information for steering the direction of the draft. Overall, the draft should be written focused more on the email that it is in response to.",
        f"User's Role: '{user_persona}'",
        f"User's Priorities: '{user_priorities}'",
        "\nOriginal Email Details:",
        f"From: {original_sender_full_header}",
        f"Subject: {original_subject}",
        f"Thread ID: {original_thread_id}",
        f"Body Preview of Original Email:\n{original_body_for_llm_preview}",
        f"\nThe user wants to: \"{action_sentiment}\"."
    ]
    if user_edit_instructions:
        prompt_construction_parts.append(f"\nUser's specific instructions for this draft: \"{user_edit_instructions}\"")
    if available_slots:
        prompt_construction_parts.append("\nThe user has indicated they are free during the following time slots. If relevant to the reply sentiment (e.g., proposing meeting times), please pick 1-3 suitable options from this list and incorporate them naturally into the email body. Format them readably (e.g., 'June 3rd from 2:00 PM to 3:00 PM IST').")
        prompt_construction_parts.append("Available Slots:")
        for slot in available_slots[:5]:
            start_dt = calendar_utils.parse_iso_to_ist(slot["start"])
            end_dt = calendar_utils.parse_iso_to_ist(slot["end"])
            if start_dt and end_dt:
                slot_text = f"- {start_dt.strftime('%A, %B %d, %Y, from %I:%M %p')} to {end_dt.strftime('%I:%M %p %Z')}"
                prompt_construction_parts.append(slot_text)
    prompt_construction_parts.append(
        "\nBased on this, please generate:"
        "\n1. A suitable reply \"subject\" line (usually \"Re: [original subject]\")."
        "\n2. A professional and concise email \"body\" for the reply."
        "\n\nFormat your response as a single JSON object with the keys \"subject\" and \"body\"."
        "\nExample:"
        "\n{"
        "\n  \"subject\": \"Re: Meeting Request\","
        "\n  \"body\": \"Hi [Sender Name],\\n\\nThanks for reaching out. I'm available on Tuesday afternoon.\\n\\nBest,\\n[User's Name (or generic sign-off)]\""
        "\n}"
        "\nEnsure the body is plain text. Do not include any other explanatory text outside the JSON object."
    )
    system_prompt_for_llm = "\n".join(prompt_construction_parts)
    response_text_for_debugging = "Gemini call for draft did not occur or failed."
    try:
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json"
        )

        response = await gemini_client.aio.models.generate_content(
            model=model_name,
            contents=[system_prompt_for_llm],
            config=config
        )
        response_text_for_debugging = response.text
        cleaned_response_text = response.text.strip()
        if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text[7:]
        if cleaned_response_text.endswith("```"): cleaned_response_text = cleaned_response_text[:-3]
        draft_data = json.loads(cleaned_response_text)
        if isinstance(draft_data, dict) and "subject" in draft_data and "body" in draft_data:
            return {
                "subject": draft_data["subject"], "body": draft_data["body"],
                "recipient_email_for_reply": original_sender_email_only,
                "original_thread_id": original_thread_id, "error": None
            }
        else:
            return {"error": "LLM did not return draft in expected subject/body format."}
    except json.JSONDecodeError:
        return {"error": "LLM JSON parsing error for draft."}
    except Exception as e:
        return {"error": f"LLM API call error for draft: {str(e)}"}

# --- Event Creation Parsing ---
async def parse_event_creation_details_from_suggestion(
    gemini_client: genai.Client, model_name: str, llm_suggestion_text: str,
    original_context_text: Optional[str], user_persona: str, user_priorities: str,
    current_datetime_iso: str
) -> Dict[str, Any]:
    prompt_parts = [
        f"You are an AI assistant helping a user create a new Google Calendar event.",
        f"User's Role: '{user_persona}'. User's Priorities: '{user_priorities}'.",
        f"The current date and time is: {current_datetime_iso}.",
        f"The user's primary timezone is likely Asia/Kolkata, but confirm if the input specifies another.",
        f"\nThe user's intention or the text suggesting event creation is: '{llm_suggestion_text}'."
    ]
    if original_context_text:
        prompt_parts.append(f"\nThis suggestion was made in the context of the following text (e.g., an email):"
                            f"\n\"\"\"\n{original_context_text[:1000]}\n\"\"\"")
    prompt_parts.extend([
        f"\nBased on this, extract the following details for the new event. If a detail is not mentioned, use a sensible default or leave it null/empty where appropriate:",
        f"- summary (string, event title, make it concise)",
        f"- start_datetime (string, REQUIRED, in YYYY-MM-DDTHH:MM:SS naive local time based on context. Resolve 'tomorrow', 'next Tuesday at 3pm' etc., relative to current time)",
        f"- timezone (string, REQUIRED, IANA timezone like 'Asia/Kolkata' or 'America/New_York' for the start_datetime)",
        f"- event_duration_hour (integer, default 0 or 1 if it's a meeting)",
        f"- event_duration_minutes (integer, default 30 or 0 if hour is set)",
        f"- attendees (array of email strings, optional, extract from context if mentioned)",
        f"- description (string, optional, can be generated from context)",
        f"- location (string, optional)",
        f"- create_meeting_room (boolean, default to true if it seems like a meeting that would need one)",
        f"\nFormat your response as a single JSON object with these keys.",
        f"Example JSON output:",
        f"{{",
        f"  \"summary\": \"Meeting with Marketing Team\",",
        f"  \"start_datetime\": \"2025-06-04T14:00:00\",",
        f"  \"timezone\": \"Asia/Kolkata\",",
        f"  \"event_duration_hour\": 1,",
        f"  \"event_duration_minutes\": 0,",
        f"  \"attendees\": [\"jane@example.com\", \"marketing_lead@example.com\"],",
        f"  \"description\": \"Discuss Q3 marketing strategy based on email from Jane.\",",
        f"  \"location\": \"Online / Google Meet\",",
        f"  \"create_meeting_room\": true",
        f"}}"
    ])
    system_prompt = "\n".join(prompt_parts)
    response_text_for_debugging = "Gemini call for parsing event creation details did not occur or failed."
    try:
        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json"
        )

        response = await gemini_client.aio.models.generate_content(
            model=model_name,
            contents=[system_prompt],
            config=config
        )
        response_text_for_debugging = response.text
        cleaned_response_text = response.text.strip()
        if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text[7:]
        if cleaned_response_text.endswith("```"): cleaned_response_text = cleaned_response_text[:-3]
        parsed_details = json.loads(cleaned_response_text)
        if not all(k in parsed_details for k in ["summary", "start_datetime", "timezone"]):
            return {"error": "LLM failed to extract all required event details (summary, start_datetime, timezone)."}
        parsed_details.setdefault("event_duration_hour", 0)
        parsed_details.setdefault("event_duration_minutes", 30 if parsed_details["event_duration_hour"] == 0 else 0)
        parsed_details.setdefault("attendees", [])
        parsed_details.setdefault("create_meeting_room", True)
        return parsed_details
    except json.JSONDecodeError:
        return {"error": "LLM JSON parsing error for event creation details."}
    except Exception as e:
        return {"error": f"LLM API call error for event creation details: {str(e)}"}

async def get_llm_tool_call_from_natural_language(
    gemini_client: genai.Client,
    model_name: str,
    user_query: str,
    active_mcp_sessions_for_llm: List,  # Now can contain MergedMCPSession
    user_config: Dict[str, Any]
) -> Union[str, Dict[str, Any]]:
    print(f"LLM_PROCESSOR (NLI_ToolCall): Getting tool call for query: '{user_query}'")

    if not active_mcp_sessions_for_llm:
        return "Sorry, I don't have any tools (like Gmail or Calendar access) currently available to help with that."

    print(f"LLM_PROCESSOR (NLI_ToolCall): Sending to Gemini with {len(active_mcp_sessions_for_llm)} MCP session(s) as tools.")

    # Enhanced system prompt with tool information
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a professional")
    user_email = user_config.get(config_manager.USER_EMAIL_KEY, "user")

    # Get tool count from merged session if available
    tool_count_info = ""
    if '_merged_session' in user_config:
        merged_session = user_config['_merged_session']
        total_tools = len(merged_session.merged_tools)

        # Fixed: Access session correctly from managers
        available_managers = user_config.get('_available_managers', {})
        gmail_session = available_managers.get('gmail').session if available_managers.get('gmail') else None
        calendar_session = available_managers.get('calendar').session if available_managers.get('calendar') else None

        gmail_tools = sum(1 for tool in merged_session.tool_routing.keys()
                         if merged_session.tool_routing[tool] == gmail_session)
        calendar_tools = sum(1 for tool in merged_session.tool_routing.keys()
                            if merged_session.tool_routing[tool] == calendar_session)

        tool_count_info = f"\n- Total tools available: {total_tools} (Gmail: {gmail_tools}, Calendar: {calendar_tools})"

    system_prompt = f"""You are MCliPPy, a proactive AI assistant designed to help manage emails and calendar events through Gmail and Google Calendar tools.

Your user is {user_email}, whose role is: {user_persona}

You have access to tools for:
- Gmail: Reading emails, sending replies, managing threads, searching messages
- Google Calendar: Creating events, finding free slots, updating/deleting events, managing schedules
{tool_count_info}

You can help with tasks like:
- Checking emails and calendar events
- Drafting email replies
- Scheduling meetings
- Finding free time slots
- Managing calendar events

Always identify yourself as MCliPpy when asked who you are. Be helpful and proactive in suggesting actions the user might want to take.

User query: {user_query}"""

    try:
        config = genai_types.GenerateContentConfig(
            temperature=0.2,
            tools=active_mcp_sessions_for_llm,  # Contains merged session
            automatic_function_calling=genai_types.AutomaticFunctionCallingConfig(disable=True),
            tool_config=genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(
                    mode="AUTO"
                )
            )
        )

        response = await gemini_client.aio.models.generate_content(
            model=model_name,
            contents=[system_prompt],
            config=config
        )

        # Check for function call in the response
        if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'function_call') and part.function_call:
                    print(f"LLM_PROCESSOR (NLI_ToolCall): Gemini suggested function call: {part.function_call.name}")
                    return {
                        "function_call": part.function_call,
                        "model_response_parts": list(response.candidates[0].content.parts)
                    }

        print("LLM_PROCESSOR (NLI_ToolCall): Gemini provided a direct text response.")
        return response.text if response.text else "I'm not sure how to respond to that."

    except Exception as e:
        error_str = str(e)
        print(f"{user_interface.Fore.RED}LLM_PROCESSOR (NLI_ToolCall): Error during Gemini API call: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()

        # Handle specific duplicate tool error
        if "already defined" in error_str:
            return "I'm having trouble with overlapping tool definitions. Let me try to help you in a different way. What specifically would you like me to do?"

        return f"Sorry, I encountered an error trying to process your request: {str(e)}"

async def get_final_response_after_tool_execution(
    gemini_client: genai.Client,
    model_name: str,
    original_user_query: str,
    previous_model_parts: List[genai_types.Part],
    tool_name: str,
    tool_execution_result: Dict[str, Any],
    active_mcp_sessions_for_llm: List[ClientSession],  # Changed from List[ClientSession] parameter name
    user_config: Dict[str, Any]
) -> str:
    print(f"LLM_PROCESSOR (NLI_FinalResp): Getting final response after executing tool '{tool_name}'.")

    # Construct the conversation history for Gemini
    history_contents = [
        genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=original_user_query)]
        ),
        genai_types.Content(
            role="model",
            parts=previous_model_parts
        ),
        genai_types.Content(
            role="tool",
            parts=[genai_types.Part.from_function_response(
                name=tool_name,
                response=tool_execution_result
            )]
        )
    ]

    # For this second call, disable function calling for final response
    config = genai_types.GenerateContentConfig(
        temperature=0.7,
        tool_config=genai_types.ToolConfig(
            function_calling_config=genai_types.FunctionCallingConfig(
                mode="NONE"
            )
        )
    )

    try:
        response = await gemini_client.aio.models.generate_content(
            model=model_name,
            contents=history_contents,
            config=config
        )

        return response.text if response.text else "I've processed that, but I don't have anything more to say."

    except Exception as e:
        print(f"{user_interface.Fore.RED}LLM_PROCESSOR (NLI_FinalResp): Error during Gemini API call for final response: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()

        # Fallback: try to present the tool result directly if LLM fails to summarize
        if tool_execution_result.get("successful"):
            success_message = tool_execution_result.get('message')
            if success_message:
                return f"Action '{tool_name}' was successful: {success_message}"
            data_preview = tool_execution_result.get('data', tool_execution_result.get('created_event_data', tool_execution_result.get('free_slots', None)))
            if data_preview:
                return f"Action '{tool_name}' was successful. Data: {json.dumps(data_preview, indent=2, default=str)[:200]}..."
            return f"Action '{tool_name}' was successful."
        else:
            return f"Action '{tool_name}' failed. Error: {tool_execution_result.get('error', 'Unknown error from tool.')}"

# --- Test Stub (Comprehensive) ---

async def _test_llm_processor():
    """Comprehensive test for all LLM processor functions with new SDK."""
    print("=" * 60)
    print("--- Testing llm_processor.py (All Functions) ---")
    print("=" * 60)

    # Get API key and initialize client
    google_api_key_from_config = config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY)
    MODEL_NAME_TEST = 'gemini-2.5-flash-preview-05-20'

    if not google_api_key_from_config:
        print("❌ GOOGLE_API_KEY not found. Please set it in your .env file.")
        print("Skipping all LLM tests.")
        return

    try:
        gemini_client_for_test = genai.Client(api_key=google_api_key_from_config)
        print(f"✅ Gemini client initialized with model '{MODEL_NAME_TEST}'")
    except Exception as e:
        print(f"❌ Failed to initialize Gemini client: {e}")
        return

    # Mock user config for testing
    mock_user_config = {
        config_manager.USER_EMAIL_KEY: "test_user@example.com",
        config_manager.USER_PERSONA_KEY: "a software developer testing AI integration",
        config_manager.USER_PRIORITIES_KEY: "testing functionality and debugging code",
    }

    print("\n" + "="*50)
    print("TEST 1: Email Processing with LLM")
    print("="*50)

    # Test email processing
    mock_emails_data = [
        {
            "messageId": "test_email_1",
            "id": "test_email_1",  # Fallback ID
            "snippet": "Meeting request for tomorrow at 2 PM",
            "payload": {
                "headers": [
                    {"name": "from", "value": "john.doe@example.com"},
                    {"name": "subject", "value": "Important Meeting Request"}
                ]
            },
            "messageText": "Hi! Could we schedule a meeting tomorrow at 2 PM to discuss the project timeline?"
        }
    ]

    try:
        email_results = await process_emails_with_llm(
            gemini_client_for_test,
            MODEL_NAME_TEST,
            mock_emails_data,
            mock_user_config[config_manager.USER_PERSONA_KEY],
            mock_user_config[config_manager.USER_PRIORITIES_KEY]
        )
        print(f"✅ Email processing successful. Processed {len(email_results)} emails.")
        if email_results:
            print(f"   First result - Important: {email_results[0].get('is_important')}")
            print(f"   Summary: {email_results[0].get('summary', 'N/A')[:100]}...")
    except Exception as e:
        print(f"❌ Email processing failed: {e}")
        traceback.print_exc()

    print("\n" + "="*50)
    print("TEST 2: Calendar Event Processing with LLM")
    print("="*50)

    # Test calendar event processing
    mock_events_data = [
        {
            "id": "test_event_1",
            "summary": "Team Standup",
            "start": {"dateTime": "2025-06-05T09:00:00+05:30"},
            "end": {"dateTime": "2025-06-05T09:30:00+05:30"},
            "description": "Daily team standup meeting"
        }
    ]

    try:
        event_results = await process_calendar_events_with_llm(
            gemini_client_for_test,
            MODEL_NAME_TEST,
            mock_events_data,
            mock_user_config,
            mock_user_config[config_manager.USER_PERSONA_KEY],
            mock_user_config[config_manager.USER_PRIORITIES_KEY]
        )
        print(f"✅ Calendar processing successful. Processed {len(event_results)} events.")
        if event_results:
            print(f"   First result summary: {event_results[0].get('summary_llm', 'N/A')[:100]}...")
    except Exception as e:
        print(f"❌ Calendar processing failed: {e}")
        traceback.print_exc()

    print("\n" + "="*50)
    print("TEST 3: Email Reply Drafting")
    print("="*50)

    try:
        reply_result = await draft_email_reply_with_llm(
            gemini_client_for_test,
            MODEL_NAME_TEST,
            mock_emails_data[0],  # Use the first mock email
            "confirm availability and suggest alternative times",
            mock_user_config[config_manager.USER_PERSONA_KEY],
            mock_user_config[config_manager.USER_PRIORITIES_KEY]
        )

        if not reply_result.get("error"):  # Check if error is falsy
            print("✅ Email reply drafting successful.")
            print(f"   Subject: {reply_result.get('subject', 'N/A')}")
            print(f"   Body preview: {reply_result.get('body', 'N/A')[:100]}...")
        else:
            print(f"❌ Email reply drafting failed: {reply_result['error']}")
    except Exception as e:
        print(f"❌ Email reply drafting failed: {e}")
        traceback.print_exc()

    print("\n" + "="*50)
    print("TEST 4: Event Creation Parsing")
    print("="*50)

    try:
        from datetime import datetime
        current_datetime_iso = datetime.now().isoformat()

        event_parsing_result = await parse_event_creation_details_from_suggestion(
            gemini_client_for_test,
            MODEL_NAME_TEST,
            "Schedule a team meeting for tomorrow at 2 PM about project review",
            "Email context: Need to discuss Q2 project milestones",
            mock_user_config[config_manager.USER_PERSONA_KEY],
            mock_user_config[config_manager.USER_PRIORITIES_KEY],
            current_datetime_iso
        )

        if "error" not in event_parsing_result:
            print("✅ Event creation parsing successful.")
            print(f"   Summary: {event_parsing_result.get('summary', 'N/A')}")
            print(f"   Start time: {event_parsing_result.get('start_datetime', 'N/A')}")
        else:
            print(f"❌ Event creation parsing failed: {event_parsing_result['error']}")
    except Exception as e:
        print(f"❌ Event creation parsing failed: {e}")
        traceback.print_exc()

    print("\n" + "="*50)
    print("TEST 5: NLI Tool Call Function (No MCP Sessions)")
    print("="*50)

    # Test NLI function with empty MCP sessions (should return fallback message)
    try:
        nli_result = await get_llm_tool_call_from_natural_language(
            gemini_client_for_test,
            MODEL_NAME_TEST,
            "What meetings do I have tomorrow?",
            [],  # Empty MCP sessions list
            mock_user_config
        )

        if isinstance(nli_result, str):
            print("✅ NLI tool call test successful (no tools available).")
            print(f"   Response: {nli_result}")
        else:
            print(f"❌ Unexpected NLI result type: {type(nli_result)}")
    except Exception as e:
        print(f"❌ NLI tool call test failed: {e}")
        traceback.print_exc()

    print("\n" + "="*50)
    print("TEST 6: Final Response Function (Mock)")
    print("="*50)

    # Test final response function with mock data
    try:
        from google.genai import types as genai_types

        # Mock previous model parts (simulating a function call)
        mock_previous_parts = [
            genai_types.Part.from_function_call(
                name="get_calendar_events",
                args={"date": "2025-06-06"}
            )
        ]

        mock_tool_result = {
            "successful": True,
            "message": "Retrieved 3 calendar events",
            "data": [
                {"title": "Morning Standup", "time": "09:00"},
                {"title": "Project Review", "time": "14:00"},
                {"title": "Client Call", "time": "16:00"}
            ]
        }

        final_response = await get_final_response_after_tool_execution(
            gemini_client_for_test,
            MODEL_NAME_TEST,
            "What meetings do I have tomorrow?",
            mock_previous_parts,
            "get_calendar_events",
            mock_tool_result,
            [],  # Empty MCP sessions
            mock_user_config
        )

        print("✅ Final response generation successful.")
        print(f"   Response: {final_response[:150]}...")

    except Exception as e:
        print(f"❌ Final response test failed: {e}")
        traceback.print_exc()

    print("\n" + "="*60)
    print("🎉 ALL TESTS COMPLETED!")
    print("="*60)
    print("Note: These tests use actual Gemini API calls.")
    print("If any tests failed, check the error messages above.")
    print("For MCP integration tests, you'll need actual MCP servers running.")


if __name__ == "__main__":
    print("🚀 Starting LLM Processor Tests...")
    print("This will make actual API calls to test the functions.")
    print("Make sure your GOOGLE_API_KEY is set in the .env file.")
    print()

    try:
        asyncio.run(_test_llm_processor())
    except KeyboardInterrupt:
        print("\n❌ Tests interrupted by user.")
    except Exception as e:
        print(f"\n❌ Test runner failed: {e}")
        traceback.print_exc()

    print("\n✅ Test runner finished.")
