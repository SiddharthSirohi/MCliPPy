# llm_processor.py
import google.generativeai as genai
import json
import traceback
import asyncio
import config_manager # For GOOGLE_API_KEY in test stub
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
1. For EACH email, determine if it is "important" based on the user's role, priorities, and the email's content (sender, subject, preview).
2. For EACH email deemed important, provide a concise 1-2 sentence summary.
3. For EACH email deemed important, suggest 1-3 brief, actionable next steps or quick actions the user might want to take. Examples: "Draft a positive reply", "Draft a polite decline", "Acknowledge receipt", "Create a calendar event for follow-up on [date]", "Add to to-do list: [task]".

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
  "email_id": "1971abc...",
  "is_important": true,
  "summary": "The sender wants to schedule an urgent meeting to discuss the Q3 roadmap.",
  "suggested_actions": ["Draft 'confirm availability' reply", "Check calendar for next Tuesday", "Prepare Q3 notes"]
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
async def process_calendar_events_with_llm(gemini_llm_model, events_data: list, user_persona: str, user_priorities: str):
    if not events_data:
        return []
    prompt_event_parts = []
    for i, event in enumerate(events_data):
        summary = event.get("summary", "No Title")
        start_time = event.get("start", {}).get("dateTime", "No Start Time")
        end_time = event.get("end", {}).get("dateTime", "No End Time")
        description = event.get("description", "No description")[:200]
        location = event.get("location", "No location")

        attendees_list = event.get("attendees")
        attendees_actual_emails = []
        if isinstance(attendees_list, list): # Ensure attendees_list is a list before iterating
            attendees_actual_emails = [att.get("email") for att in attendees_list
                                       if isinstance(att, dict) and att.get("email")]

        attendees_str = ', '.join(attendees_actual_emails) if attendees_actual_emails else 'None listed'

        prompt_event_parts.append(
            f"Event {i+1}:\n"
            f"ID: {event.get('id', 'N/A')}\n"
            f"Title: {summary}\n"
            f"Start: {start_time}\n"
            f"End: {end_time}\n"
            f"Location: {location}\n"
            f"Attendees: {attendees_str}\n" # Use the processed string
            f"Description Snippet: {description}\n---\n"
        )
    if not prompt_event_parts:
        print("LLM_PROCESSOR: No event content to process.")
        return []
    event_details_str = "\n".join(prompt_event_parts)

    system_prompt = f"""
You are a highly efficient AI assistant for a user whose role is: '{user_persona}'.
Their key priorities are: '{user_priorities}'.

You will be given a list of upcoming calendar events. Your tasks are:
1. For EACH event, provide a very brief highlight or summary focusing on what's most relevant to the user.
2. For EACH event, suggest 1-2 brief, actionable next steps or quick actions. Examples: "Prepare agenda points", "Confirm attendance (if RSVP needed and tool exists)", "Set reminder 15 mins prior", "Check related documents for [topic]".

Analyze the following events:
{event_details_str}

Please format your response as a single JSON array, where each object in the array corresponds to an event you analyzed.
Each object should have the following keys:
- "event_id": (string) The ID of the event (e.g., from "ID: ...").
- "summary_llm": (string) Your brief highlight/summary of the event.
- "suggested_actions": (array of strings) A list of 1-2 suggested actions for this event.
Ensure the entire response is a valid JSON array.
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

# llm_processor.py

# ... (other imports: genai, json, traceback, asyncio, config_manager) ...
# ... (process_emails_with_llm and process_calendar_events_with_llm functions remain the same) ...

# llm_processor.py
async def draft_email_reply_with_llm(
    gemini_llm_model,
    original_email_data: Dict[str, Any],
    action_sentiment: str,
    user_persona: str,
    user_priorities: str,
    user_edit_instructions: Optional[str] = None
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

    final_prompt_for_llm = "\n".join(prompt_construction_parts)

    response_text_for_debugging = "Gemini call for draft did not occur or failed."
    try:
        # print(f"LLM_PROCESSOR (Draft Reply Debug): Final Prompt Sent:\n{final_prompt_for_llm}") # For debugging if needed
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
                "recipient_email_for_reply": original_sender_email_only, # CRITICAL: Use the parsed email
                "original_thread_id": original_thread_id,
                "error": None
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
        # traceback.print_exc() # Uncomment for full traceback if needed
        return {"error": f"LLM API call error for draft: {str(e)}"}

# --- Test Stub for this module ---
async def _test_llm_processor():
    print("--- Testing llm_processor.py ---")

    google_api_key_from_config = config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY)

    if not google_api_key_from_config:
        print(f"{config_manager.ENV_GOOGLE_API_KEY} not found by config_manager. Ensure it's in .env and config_manager.py loads it.")
        return

    try:
        # EXPLICITLY CONFIGURE GENAI with the key loaded by config_manager
        print(f"Configuring Gemini with API key: {'********' if google_api_key_from_config else 'None'}")
        genai.configure(api_key=google_api_key_from_config)

        model = genai.GenerativeModel('gemini-2.5-flash-preview-05-20')
        print("Gemini model initialized successfully.")
    except Exception as e:
        print(f"Error initializing Gemini model (even with explicit configure): {e}")
        traceback.print_exc()
        return

    # ... (rest of the mock data and test calls for emails and calendar) ...
    # (This part remains the same as the previous version)
    mock_emails_data_from_tool = {"data": {"messages": [
        {"messageId": "email123", "snippet": "Q3 budget deadline approaching next Friday.", "messageText": "Team, quick reminder that the Q3 budget deadline is fast approaching next Friday. Please ensure all submissions are in by EOD Thursday.", "payload": {"headers": [{"name": "Subject", "value": "URGENT: Budget Deadline"}, {"name": "From", "value": "Boss <boss@example.com>"}]}},
        {"messageId": "email456", "snippet": "Team lunch tomorrow to celebrate!", "messageText": "Hey everyone, to celebrate the successful project launch, we're having a team lunch tomorrow at The Great Eatery at 1 PM. Hope to see you all there!", "payload": {"headers": [{"name": "Subject", "value": "Team Lunch!"}, {"name": "From", "value": "Friendly Colleague <colleague@example.com>"}]}},
        {"messageId": "email789", "snippet": "Your subscription to CloudServicePro is renewing soon.", "messageText": "This is a notification that your annual subscription to CloudServicePro will auto-renew on June 15th for $99.", "payload": {"headers": [{"name": "Subject", "value": "Subscription Renewal Notice"}, {"name": "From", "value": "CloudServicePro <billing@cloudservicepro.com>"}]}}
    ]}}
    actual_mock_emails = mock_emails_data_from_tool.get("data", {}).get("messages", [])
    mock_persona = "Product Manager focused on new feature development and team coordination."
    mock_priorities = "Client feedback, project deadlines, team blockers, and innovative ideas."

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
        {"id": "cal_event_1", "summary": "Project Phoenix Daily Standup", "start": {"dateTime": "2025-05-31T09:00:00-07:00"}, "end": {"dateTime": "2025-05-31T09:15:00-07:00"}, "attendees": [{"email":"dev1@example.com"}, {"email":"dev2@example.com"}]},
        {"id": "cal_event_2", "summary": "Client Demo - Alpha Release", "start": {"dateTime": "2025-05-31T14:00:00-07:00"}, "end": {"dateTime": "2025-05-31T15:00:00-07:00"}, "description": "Showcase new features to Client X. Focus on UI improvements and performance gains.", "location": "ClientX HQ, Meeting Room 3"}
    ]
    if mock_calendar_events:
        print("\nProcessing mock calendar events...")
        processed_events = await process_calendar_events_with_llm(model, mock_calendar_events, mock_persona, mock_priorities)
        print("\n--- Processed Calendar Events Output from LLM ---")
        for pe_cal in processed_events:
            print(json.dumps(pe_cal, indent=2))
            print("-" * 20)


if __name__ == "__main__":
    asyncio.run(_test_llm_processor())
