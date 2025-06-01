# llm_processor.py
import google.generativeai as genai
import json
import traceback
import asyncio
import config_manager
import calendar_utils
from typing import Dict, List, Optional, Any, Tuple


# --- Email Processing ---
# (process_emails_with_llm function remains largely the same, no changes for these specific errors)
async def process_emails_with_llm(gemini_llm_model, emails_data: list, user_persona: str, user_priorities: str):
    # ... (previous code for this function) ...
    if not emails_data:
        return []

    prompt_email_parts = []
    for i, email in enumerate(emails_data):
        subject = "No Subject"
        sender = "Unknown Sender"
        snippet = email.get("snippet", "No snippet available.")
        message_text_preview = email.get("messageText", snippet)[:500]

        if email.get("payload") and isinstance(email["payload"].get("headers"), list):
            for header in email["payload"]["headers"]:
                if header.get("name", "").lower() == "subject":
                    subject = header.get("value", "No Subject")
                if header.get("name", "").lower() == "from":
                    sender = header.get("value", "Unknown Sender")

        prompt_email_parts.append(
            f"Email {i+1}:\n"
            f"ID: {email.get('messageId', 'N/A')}\n"
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
        response = await gemini_llm_model.generate_content_async(system_prompt)
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
                original_id = original_email_data.get('messageId')
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
            for original_email_data in emails_data:
                processed_emails.append({"original_email_data": original_email_data, "is_important": False, "summary": "LLM response format error.", "suggested_actions": []})
    except json.JSONDecodeError as e:
        print(f"LLM_PROCESSOR (Emails): Failed to decode Gemini JSON response: {e}")
        print(f"LLM_PROCESSOR (Emails): Raw response that failed parsing:\n{response_text_for_debugging}")
        for original_email_data in emails_data:
             processed_emails.append({"original_email_data": original_email_data, "is_important": False, "summary": "LLM JSON parsing error.", "suggested_actions": []})
    except Exception as e:
        print(f"LLM_PROCESSOR (Emails): Error during Gemini API call: {e}")
        print(f"LLM_PROCESSOR (Emails): Raw response that might have caused error (if available):\n{response_text_for_debugging}")
        for original_email_data in emails_data:
             processed_emails.append({"original_email_data": original_email_data, "is_important": False, "summary": "LLM API call error.", "suggested_actions": []})
    return processed_emails

# --- Calendar Event Processing ---
async def process_calendar_events_with_llm(
    gemini_llm_model,
    events_data: list,
    user_persona: str,
    user_priorities: str,
    user_config: Dict[str, Any]
):
    if not events_data:
        return []

    prompt_event_parts = []
    for i, event in enumerate(events_data):
        summary = event.get("summary", "No Title")
        event_id = event.get("id", "N/A") # Make sure to include event ID
        start_time = event.get("start", {}).get("dateTime", "No Start Time")
        end_time = event.get("end", {}).get("dateTime", "No End Time")
        description_snippet = event.get("description", "No description")[:150] # Shorter snippet for this prompt
        location = event.get("location", "No location")
        attendees_list = event.get("attendees", [])
        attendees_emails = [
            att.get("email") for att in attendees_list
            if isinstance(att, dict) and att.get("email") and not att.get("resource", False) # Exclude resource calendars
        ]
        # Filter out the user's own email if present among attendees for brevity in prompt
        user_main_email = user_config.get(config_manager.USER_EMAIL_KEY) # Assuming user_config is accessible or passed
        if user_main_email and user_main_email in attendees_emails:
            attendees_emails.remove(user_main_email)

        attendees_str = f"Attendees: {', '.join(attendees_emails[:3])}{'...' if len(attendees_emails) > 3 else ''}" if attendees_emails else "Attendees: Just you"


        prompt_event_parts.append(
            f"Event {i+1} (ID: {event_id}):\n" # Include ID clearly
            f"  Title: {summary}\n"
            f"  Start: {start_time}\n"
            f"  End: {end_time}\n"
            # f"  Location: {location}\n" # Optional for brevity here
            # f"  {attendees_str}\n" # Optional for brevity here
            f"  Description Snippet: {description_snippet}\n---\n"
        )

    if not prompt_event_parts:
        print("LLM_PROCESSOR (Calendar): No event content to process for LLM.")
        return []
    event_details_str = "\n".join(prompt_event_parts)

    # --- MODIFIED SYSTEM PROMPT ---
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

Please format your response as a single JSON array...
Each object MUST have... "event_id", "summary_llm", "suggested_actions": [...]
"""

    processed_events = []
    response_text_for_debugging = "Gemini call did not occur or failed before response was received."
    try:
        response = await gemini_llm_model.generate_content_async(system_prompt)
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
        for original_event_data in events_data:
             processed_events.append({"original_event_data": original_event_data, "summary_llm": "LLM API call error.", "suggested_actions": []})
    return processed_events


async def draft_email_reply_with_llm(
    gemini_llm_model,
    original_email_data: Dict[str, Any],
    action_sentiment: str,
    user_persona: str,
    user_priorities: str,
    user_edit_instructions: Optional[str] = None,
    available_slots: Optional[List[Dict[str, str]]] = None
) -> Dict[str, str]:

    if not original_email_data:
        return {"error": "Original email data not provided."}

    original_subject = "No Subject"
    original_sender_email_only = None # Will store just the email address
    original_sender_full_header = "Unknown Sender" # For LLM context
    original_snippet = original_email_data.get("snippet", "")
    original_thread_id = original_email_data.get("threadId", "N/A")

    # Attempt 1: Parse from "payload" headers
    payload = original_email_data.get("payload")
    if payload and isinstance(payload.get("headers"), list):
        for header in payload["headers"]:
            header_name_lower = header.get("name", "").lower()
            if header_name_lower == "from":
                original_sender_full_header = header.get("value", "Unknown Sender")
                if "<" in original_sender_full_header and ">" in original_sender_full_header:
                    start_index = original_sender_full_header.find("<") + 1
                    end_index = original_sender_full_header.find(">")
                    if start_index < end_index:
                        original_sender_email_only = original_sender_full_header[start_index:end_index].strip()
                elif "@" in original_sender_full_header: # Basic check if it's just an email
                    # Check if it's "DisplayName email@domain.com" without <>
                    parts = original_sender_full_header.split()
                    if len(parts) > 1 and "@" in parts[-1]: # Last part might be email
                        potential_email = parts[-1]
                        if "@" in potential_email and "." in potential_email:
                            original_sender_email_only = potential_email.strip()
                        else: # Fallback to the whole string if parsing is tricky
                            original_sender_email_only = original_sender_full_header.strip()
                    elif "@" in original_sender_full_header and "." in original_sender_full_header : # It is just an email
                        original_sender_email_only = original_sender_full_header.strip()

            elif header_name_lower == "subject":
                original_subject = header.get("value", "No Subject")

    # Attempt 2: If parsing headers failed, use Composio's top-level `sender` field
    if not original_sender_email_only:
        composio_top_level_sender = original_email_data.get("sender")
        if composio_top_level_sender:
            original_sender_full_header = composio_top_level_sender # Update full header for LLM too
            if "<" in composio_top_level_sender and ">" in composio_top_level_sender:
                start_index = composio_top_level_sender.find("<") + 1
                end_index = composio_top_level_sender.find(">")
                if start_index < end_index:
                    original_sender_email_only = composio_top_level_sender[start_index:end_index].strip()
            elif "@" in composio_top_level_sender and "." in composio_top_level_sender: # Basic check if it's just an email
                 parts = composio_top_level_sender.split()
                 if len(parts) > 1 and "@" in parts[-1]:
                     potential_email = parts[-1]
                     if "@" in potential_email and "." in potential_email:
                         original_sender_email_only = potential_email.strip()
                     else:
                        original_sender_email_only = composio_top_level_sender.strip() # Fallback
                 elif "@" in composio_top_level_sender and "." in composio_top_level_sender:
                     original_sender_email_only = composio_top_level_sender.strip()


    if not original_sender_email_only:
        print(f"LLM_PROCESSOR (Draft Reply): CRITICAL - Could not determine a valid recipient email for the reply from header '{original_sender_full_header}' or top-level '{original_email_data.get('sender')}'.")
        return {"error": "Could not determine a valid recipient email for the reply."}
    else:
        print(f"LLM_PROCESSOR (Draft Reply): Determined recipient for reply as: '{original_sender_email_only}' (from full: '{original_sender_full_header}')")

    original_body_for_llm = original_email_data.get("snippet", "No content available.") # Fallback

    if original_email_data.get("messageText"): # Prioritize messageText
        original_body_for_llm = original_email_data["messageText"]
    elif payload and isinstance(payload.get("parts"), list): # Check payload parts for text/plain
        for part in payload["parts"]:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                try:
                    import base64
                    decoded_body = base64.urlsafe_b64decode(part["body"]["data"].encode('ASCII')).decode('utf-8')
                    original_body_for_llm = decoded_body
                    break # Found plain text part
                except Exception as e:
                    print(f"LLM_PROCESSOR (Draft Reply): Error decoding payload part: {e}")
                    # Stick with snippet if decoding fails
                    original_body_for_llm = original_email_data.get("snippet", "Error reading body.")
                    break

    # Limit length for the prompt to avoid overly long prompts
    max_body_length_for_prompt = 1500
    if len(original_body_for_llm) > max_body_length_for_prompt:
        original_body_for_llm_preview = original_body_for_llm[:max_body_length_for_prompt] + "\n[...message truncated...]"
    else:
        original_body_for_llm_preview = original_body_for_llm
    # --- End of body content extraction ---

    # Now construct the prompt using the correctly populated variables
    prompt_construction_parts = [
        f"You are an AI assistant helping a user draft an email reply. You have access to the user's persona and priorities. However, these are for your general information for steering the direction of the draft. Overall, the draft should be written focused more on the email that it is in response to.",
        f"User's Role: '{user_persona}'",
        f"User's Priorities: '{user_priorities}'",
        "\nOriginal Email Details:",
        f"From: {original_sender_full_header}", # Use full header for LLM context
        f"Subject: {original_subject}",
        f"Thread ID: {original_thread_id}",
        # f"Snippet/Preview: {original_snippet[:300]}", # OLD
        f"Body Preview of Original Email:\n{original_body_for_llm_preview}", # NEW - use more complete body
        f"\nThe user wants to: \"{action_sentiment}\"."
    ]

    if user_edit_instructions:
        prompt_construction_parts.append(f"\nUser's specific instructions for this draft: \"{user_edit_instructions}\"")

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

    if available_slots:
        prompt_construction_parts.append("\nThe user has indicated they are free during the following time slots. If relevant to the reply sentiment (e.g., proposing meeting times), please pick 1-3 suitable options from this list and incorporate them naturally into the email body. Format them readably (e.g., 'June 3rd from 2:00 PM to 3:00 PM IST').")
        prompt_construction_parts.append("Available Slots:")
        for slot in available_slots[:5]: # Show a few to the LLM
            # We need a readable format for the LLM here
            start_dt = calendar_utils.parse_iso_to_ist(slot["start"])
            end_dt = calendar_utils.parse_iso_to_ist(slot["end"])
            if start_dt and end_dt:
                # Example: "Monday, June 03, 2025, from 10:00 AM to 10:30 AM IST"
                slot_text = f"- {start_dt.strftime('%A, %B %d, %Y, from %I:%M %p')} to {end_dt.strftime('%I:%M %p %Z')}"
                prompt_construction_parts.append(slot_text)
    # +++++++++++++ END OF CONDITIONAL SLOTS +++++++++++++

    prompt_construction_parts.append(
        # ... (rest of the JSON output instruction and example, same as before) ...
        "\nBased on this, please generate:"
        "\n1. A suitable reply \"subject\" line (usually \"Re: [original subject]\")."
        "\n2. A professional and concise email \"body\" for the reply."
        # ...
    )
    final_prompt_for_llm = "\n".join(prompt_construction_parts)


    response_text_for_debugging = "Gemini call for draft did not occur or failed."
    try:
        # print(f"LLM_PROCESSOR (Draft Reply DEBUG): Final Prompt Sent:\n{final_prompt_for_llm}")
        response = await gemini_llm_model.generate_content_async(final_prompt_for_llm)
        response_text_for_debugging = response.text

        cleaned_response_text = response.text.strip()
        if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text[7:]
        if cleaned_response_text.endswith("```"): cleaned_response_text = cleaned_response_text[:-3]

        draft_data = json.loads(cleaned_response_text)
        if isinstance(draft_data, dict) and "subject" in draft_data and "body" in draft_data:
            return {
                "subject": draft_data["subject"],
                "body": draft_data["body"],
                "recipient_email_for_reply": original_sender_email_only,
                "original_thread_id": original_thread_id,
                "error": None # Explicitly None
            }
        else:
            print(f"LLM_PROCESSOR (Draft Reply): LLM response was not a dict with subject/body: {draft_data}")
            return {"error": "LLM did not return draft in expected subject/body format."}
    except json.JSONDecodeError:
        print(f"LLM_PROCESSOR (Draft Reply): Failed to decode Gemini JSON response for draft.")
        print(f"LLM_PROCESSOR (Draft Reply): Raw response: {response_text_for_debugging}")
        return {"error": "LLM JSON parsing error for draft."}
    except Exception as e:
        print(f"LLM_PROCESSOR (Draft Reply): Error during Gemini API call for draft: {e}")
        print(f"LLM_PROCESSOR (Draft Reply): Raw response (if available): {response_text_for_debugging}")
        return {"error": f"LLM API call error for draft: {str(e)}"}

async def parse_event_creation_details_from_suggestion(
    gemini_llm_model,
    llm_suggestion_text: str, # e.g., "Create event: 'Team Sync' Tuesday 3pm with john@example.com"
    original_context_text: Optional[str], # e.g., email body or original event summary that prompted this
    user_persona: str,
    user_priorities: str,
    current_datetime_iso: str # Current ISO datetime to help LLM resolve relative times like "tomorrow"
) -> Dict[str, Any]:
    """
    Parses an LLM's textual suggestion or email content to extract structured details
    for creating a new Google Calendar event.
    Returns a dictionary like:
    {
        "summary": "Team Sync",
        "start_datetime": "2025-06-03T15:00:00", // Naive
        "timezone": "Asia/Kolkata",
        "event_duration_hour": 1,
        "event_duration_minutes": 0,
        "attendees": ["john@example.com"], // List of email strings
        "description": "Generated from suggestion: ...",
        "location": None,
        "create_meeting_room": True, // Default to creating a meet link
        "error": None
    }
    or {"error": "message"}
    """
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
    final_prompt = "\n".join(prompt_parts)

    response_text_for_debugging = "Gemini call for parsing event creation details did not occur or failed."
    try:
        # print(f"LLM_PROCESSOR (Parse Create Event DEBUG): Prompt:\n{final_prompt}")
        response = await gemini_llm_model.generate_content_async(final_prompt)
        response_text_for_debugging = response.text
        # print(f"LLM_PROCESSOR (Parse Create Event DEBUG): Raw Response:\n{response_text_for_debugging}")


        cleaned_response_text = response.text.strip()
        if cleaned_response_text.startswith("```json"): cleaned_response_text = cleaned_response_text[7:]
        if cleaned_response_text.endswith("```"): cleaned_response_text = cleaned_response_text[:-3]

        parsed_details = json.loads(cleaned_response_text)

        # Basic validation of required fields
        if not all(k in parsed_details for k in ["summary", "start_datetime", "timezone"]):
            print(f"LLM_PROCESSOR (Parse Create Event): LLM did not return all required fields (summary, start_datetime, timezone). Parsed: {parsed_details}")
            return {"error": "LLM failed to extract all required event details (summary, start_datetime, timezone)."}

        # Ensure duration defaults if not present
        parsed_details.setdefault("event_duration_hour", 0)
        parsed_details.setdefault("event_duration_minutes", 30 if parsed_details["event_duration_hour"] == 0 else 0)
        parsed_details.setdefault("attendees", [])
        parsed_details.setdefault("create_meeting_room", True) # Default to true for meetings

        return parsed_details

    except json.JSONDecodeError:
        print(f"LLM_PROCESSOR (Parse Create Event): Failed to decode Gemini JSON response.")
        print(f"LLM_PROCESSOR (Parse Create Event): Raw response: {response_text_for_debugging}")
        return {"error": "LLM JSON parsing error for event creation details."}
    except Exception as e:
        print(f"LLM_PROCESSOR (Parse Create Event): Error during Gemini API call: {e}")
        # traceback.print_exc()
        return {"error": f"LLM API call error for event creation details: {str(e)}"}


# --- Test Stub for this module ---
async def _test_llm_processor():
    print("--- Testing llm_processor.py ---")

    google_api_key_from_config = config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY)

    if not google_api_key_from_config:
        print(f"{config_manager.ENV_GOOGLE_API_KEY} not found by config_manager. Ensure it's in .env and config_manager.py loads it.")
        return

    try:
        print(f"Configuring Gemini with API key: {'********' if google_api_key_from_config else 'None'}")
        genai.configure(api_key=google_api_key_from_config)
        model = genai.GenerativeModel('gemini-2.5-flash-preview-05-20')
        print("Gemini model initialized successfully.")
    except Exception as e:
        print(f"Error initializing Gemini model (even with explicit configure): {e}")
        traceback.print_exc()
        return

    mock_persona = "Product Manager focused on new feature development and team coordination."
    mock_priorities = "Client feedback, project deadlines, team blockers, and innovative ideas."

    # --- Mock user_config for testing process_calendar_events_with_llm ---
    mock_user_config_for_test = {
        config_manager.USER_EMAIL_KEY: "test_user@example.com", # Provide a mock email
        # Add other keys if process_calendar_events_with_llm were to use them, but it only needs USER_EMAIL_KEY for now
    }
    # --- End of mock user_config ---


    # ... (mock_emails_data_from_tool and processing mock emails - this part is fine) ...
    mock_emails_data_from_tool = {"data": {"messages": [
        {"messageId": "email123", "snippet": "Q3 budget deadline approaching next Friday.", "messageText": "Team, quick reminder that the Q3 budget deadline is fast approaching next Friday. Please ensure all submissions are in by EOD Thursday.", "payload": {"headers": [{"name": "Subject", "value": "URGENT: Budget Deadline"}, {"name": "From", "value": "Boss <boss@example.com>"}]}},
        {"messageId": "email456", "snippet": "Team lunch tomorrow to celebrate!", "messageText": "Hey everyone, to celebrate the successful project launch, we're having a team lunch tomorrow at The Great Eatery at 1 PM. Hope to see you all there!", "payload": {"headers": [{"name": "Subject", "value": "Team Lunch!"}, {"name": "From", "value": "Friendly Colleague <colleague@example.com>"}]}},
        {"messageId": "email789", "snippet": "Your subscription to CloudServicePro is renewing soon.", "messageText": "This is a notification that your annual subscription to CloudServicePro will auto-renew on June 15th for $99.", "payload": {"headers": [{"name": "Subject", "value": "Subscription Renewal Notice"}, {"name": "From", "value": "CloudServicePro <billing@cloudservicepro.com>"}]}}
    ]}}
    actual_mock_emails = mock_emails_data_from_tool.get("data", {}).get("messages", [])


    if actual_mock_emails:
        print("\nProcessing mock emails...")
        processed_emails = await process_emails_with_llm(model, actual_mock_emails, mock_persona, mock_priorities)
        print("\n--- Processed Emails Output from LLM ---")
        for pe in processed_emails:
            print(json.dumps(pe, indent=2))
            print("-" * 20)
    else:
        print("No mock emails to process.")


    mock_calendar_events = [
        {"id": "cal_event_1", "summary": "Project Phoenix Daily Standup", "start": {"dateTime": "2025-05-31T09:00:00-07:00"}, "end": {"dateTime": "2025-05-31T09:15:00-07:00"}, "attendees": [{"email":"dev1@example.com"}, {"email":"dev2@example.com"}, {"email":"test_user@example.com"}]}, # Added test_user
        {"id": "cal_event_2", "summary": "Client Demo - Alpha Release", "start": {"dateTime": "2025-05-31T14:00:00-07:00"}, "end": {"dateTime": "2025-05-31T15:00:00-07:00"}, "description": "Showcase new features to Client X. Focus on UI improvements and performance gains.", "location": "ClientX HQ, Meeting Room 3"}
    ]
    if mock_calendar_events:
        print("\nProcessing mock calendar events...")
        # Pass the mock_user_config_for_test
        processed_events = await process_calendar_events_with_llm(
            model,
            mock_calendar_events,
            mock_persona,
            mock_priorities,
            mock_user_config_for_test # <<< PASS THE MOCK CONFIG HERE
        )
        print("\n--- Processed Calendar Events Output from LLM ---")
        for pe_cal in processed_events:
            print(json.dumps(pe_cal, indent=2))
            print("-" * 20)

if __name__ == "__main__":
    asyncio.run(_test_llm_processor())
