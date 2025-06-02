from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional

# Assume IST for all operations for now as per your requirement
IST = timezone(timedelta(hours=5, minutes=30))

def parse_iso_to_ist(datetime_str: str) -> Optional[datetime]:
    """Parses ISO string (potentially with offset) and returns IST datetime object."""
    try:
        dt = datetime.fromisoformat(datetime_str)
        return dt.astimezone(IST)
    except (ValueError, TypeError):
        return None

def format_datetime_to_iso_ist(dt_obj: datetime) -> str:
    """Formats a datetime object to ISO string with IST offset."""
    return dt_obj.isoformat()

def calculate_free_slots(
    query_start_dt_ist: datetime, # Query range start, IST
    query_end_dt_ist: datetime,   # Query range end, IST
    busy_slots_data: List[Dict[str, str]], # List of {"start": iso_str, "end": iso_str} in IST
    meeting_duration_minutes: int,
    workday_start_hour: int = 9,  # 9 AM
    workday_end_hour: int = 18   # 6 PM
) -> List[Dict[str, str]]:
    """
    Calculates available free slots within a given query range and working hours,
    considering busy slots. All datetimes are handled as IST.
    Returns a list of dicts, each like {"start": "iso_start_str_ist", "end": "iso_end_str_ist"}
    """
    free_slots_found = []

    # Convert busy_slots_data to datetime objects and sort
    parsed_busy_slots = []
    for busy in busy_slots_data:
        start = parse_iso_to_ist(busy.get("start"))
        end = parse_iso_to_ist(busy.get("end"))
        if start and end and start < end: # Basic validation
            parsed_busy_slots.append({"start": start, "end": end})

    parsed_busy_slots.sort(key=lambda x: x["start"])

    # Effective start of the day for slot finding (max of query start or workday start for that day)
    day_work_start_dt = query_start_dt_ist.replace(hour=workday_start_hour, minute=0, second=0, microsecond=0)
    current_slot_start = max(query_start_dt_ist, day_work_start_dt)

    # Effective end of the day for slot finding (min of query end or workday end for that day)
    day_work_end_dt = query_end_dt_ist.replace(hour=workday_end_hour, minute=0, second=0, microsecond=0)

    # Merge overlapping/adjacent busy slots (optional but good for cleaner logic)
    # For simplicity, let's skip merging for now and handle overlaps in the loop.
    # A more robust solution would merge overlapping busy intervals first.

    for busy_period in parsed_busy_slots:
        busy_start_dt = busy_period["start"]
        busy_end_dt = busy_period["end"]

        # Consider the effective query end for this iteration
        effective_query_end_this_iteration = min(busy_start_dt, query_end_dt_ist, day_work_end_dt)

        # Find slots between current_slot_start and busy_start_dt (or effective_query_end)
        while current_slot_start + timedelta(minutes=meeting_duration_minutes) <= effective_query_end_this_iteration:
            slot_end_candidate = current_slot_start + timedelta(minutes=meeting_duration_minutes)
            # Ensure slot is within the workday boundaries of its own day
            slot_workday_start = current_slot_start.replace(hour=workday_start_hour, minute=0, second=0, microsecond=0)
            slot_workday_end = current_slot_start.replace(hour=workday_end_hour, minute=0, second=0, microsecond=0)

            if current_slot_start >= slot_workday_start and slot_end_candidate <= slot_workday_end:
                 free_slots_found.append({
                     "start": format_datetime_to_iso_ist(current_slot_start),
                     "end": format_datetime_to_iso_ist(slot_end_candidate)
                 })
            current_slot_start += timedelta(minutes=meeting_duration_minutes) # Check next potential slot

        # Move current_slot_start to the end of the current busy period, if it's later
        current_slot_start = max(current_slot_start, busy_end_dt)
        # And ensure it's not before the day's working start time if busy_end_dt was early
        current_slot_start = max(current_slot_start, day_work_start_dt)


    # Check for free slots after the last busy period until query_end_dt_ist / workday_end
    effective_final_query_end = min(query_end_dt_ist, day_work_end_dt)
    while current_slot_start + timedelta(minutes=meeting_duration_minutes) <= effective_final_query_end:
        slot_end_candidate = current_slot_start + timedelta(minutes=meeting_duration_minutes)
        # Ensure slot is within the workday boundaries of its own day
        slot_workday_start = current_slot_start.replace(hour=workday_start_hour, minute=0, second=0, microsecond=0)
        slot_workday_end = current_slot_start.replace(hour=workday_end_hour, minute=0, second=0, microsecond=0)

        if current_slot_start >= slot_workday_start and slot_end_candidate <= slot_workday_end:
            free_slots_found.append({
                "start": format_datetime_to_iso_ist(current_slot_start),
                "end": format_datetime_to_iso_ist(slot_end_candidate)
            })
        current_slot_start += timedelta(minutes=meeting_duration_minutes)

    return free_slots_found

if __name__ == '__main__':
    # Test the calculate_free_slots function
    print("--- Testing Calendar Utils ---")

    # Example from your log for 2025-06-02, IST
    test_query_start_str = "2025-06-02T09:00:00+05:30"
    test_query_end_str = "2025-06-02T18:00:00+05:30"

    # Busy slots from your example response (ensure they are for the query day)
    test_busy_slots = [
        {"start": "2025-06-02T09:00:00+05:30", "end": "2025-06-02T10:30:00+05:30"}, # Study ConSys + Deep Dive
        {"start": "2025-06-02T13:00:00+05:30", "end": "2025-06-02T15:45:00+05:30"}  # Lunch + Study MuE
    ]
    # More busy slots for testing
    # test_busy_slots.append({"start": "2025-06-02T16:00:00+05:30", "end": "2025-06-02T17:00:00+05:30"})

    query_start_dt = parse_iso_to_ist(test_query_start_str)
    query_end_dt = parse_iso_to_ist(test_query_end_str)

    if query_start_dt and query_end_dt:
        print(f"\nFinding 30-minute slots for {query_start_dt.date()} between 9 AM and 6 PM IST")
        slots_30min = calculate_free_slots(query_start_dt, query_end_dt, test_busy_slots, 30)
        for slot in slots_30min:
            print(f"  Free: {slot['start']} to {slot['end']}")

        print(f"\nFinding 60-minute slots for {query_start_dt.date()} between 9 AM and 6 PM IST")
        slots_60min = calculate_free_slots(query_start_dt, query_end_dt, test_busy_slots, 60)
        for slot in slots_60min:
            print(f"  Free: {slot['start']} to {slot['end']}")

        print(f"\nFinding 15-minute slots for {query_start_dt.date()} between 9 AM and 6 PM IST")
        slots_15min = calculate_free_slots(query_start_dt, query_end_dt, test_busy_slots, 15)
        for slot in slots_15min:
            print(f"  Free: {slot['start']} to {slot['end']}")

        print("\nTesting with no busy slots:")
        slots_none_busy = calculate_free_slots(query_start_dt, query_end_dt, [], 60)
        for slot in slots_none_busy:
            print(f"  Free: {slot['start']} to {slot['end']}")

        print("\nTesting with a busy slot that covers the whole day:")
        full_day_busy = [{"start": "2025-06-02T09:00:00+05:30", "end": "2025-06-02T18:00:00+05:30"}]
        slots_full_busy = calculate_free_slots(query_start_dt, query_end_dt, full_day_busy, 60)
        if not slots_full_busy:
            print("  Correctly no slots found.")
        else:
            for slot in slots_full_busy: print(f"  Free: {slot['start']} to {slot['end']}")
    else:
        print("Error parsing test query dates.")
