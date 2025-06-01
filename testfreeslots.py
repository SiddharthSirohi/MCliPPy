# test_mcp_free_slots.py
import asyncio
from mcp_handler import McpSessionManager
import config_manager # To get calendar_mcp_url and user_id

async def main():
    user_cfg = config_manager.load_user_config()
    calendar_url = user_cfg.get(config_manager.CALENDAR_MCP_URL_KEY)
    user_id = user_cfg.get(config_manager.USER_EMAIL_KEY)

    if not calendar_url or not user_id:
        print("Calendar URL or User ID not found in config.")
        return

    time_min = "2025-06-03T09:00:00+05:30" # Example: June 3rd, 9 AM IST
    time_max = "2025-06-03T18:00:00+05:30" # Example: June 3rd, 6 PM IST
    duration = 60 # minutes

    async with McpSessionManager(calendar_url, user_id, "googlecalendar-findslots") as cal_manager:
        if cal_manager.session:
            result = await cal_manager.get_calendar_free_slots(time_min, time_max, duration)
            print("\n--- Free Slots Result ---")
            if result.get("successful"):
                print("Found free slots:")
                for slot in result.get("free_slots", []):
                    # Here you'd format them nicely for display
                    print(f"  Start: {slot['start']}, End: {slot['end']}")
            else:
                print(f"Error finding free slots: {result.get('error')}")
        else:
            print("Failed to establish MCP session for calendar.")

if __name__ == "__main__":
    asyncio.run(main())