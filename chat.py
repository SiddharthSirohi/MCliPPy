import asyncio
import os
import sys
import json
import warnings
import traceback
import time
from datetime import datetime
from dotenv import load_dotenv
from typing import Optional

# New SDK imports
from google import genai
from google.genai import types
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.shared.exceptions import McpError
from anyio import ClosedResourceError

# --- 1. Setup ---

warnings.filterwarnings('ignore', message='The object <.+> is being destroyed an asyncio event loop is not running')
warnings.filterwarnings('ignore', message='The object <.+> is being destroyed but the event loop is already closed')

load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
INTEGRATED_MCP_SERVER_UUID = os.getenv("INTEGRATED_MCP_SERVER_UUID")

if not GOOGLE_API_KEY:
    print("Error: GOOGLE_API_KEY not found in .env file.")
    sys.exit(1)
if not INTEGRATED_MCP_SERVER_UUID:
    print("Error: INTEGRATED_MCP_SERVER_UUID not found in .env file.")
    sys.exit(1)

# Construct the Composio MCP Server URL
COMPOSIO_MCP_BASE_URL = "https://mcp.composio.dev/composio/server/"
MCP_SERVER_URL = f"{COMPOSIO_MCP_BASE_URL}{INTEGRATED_MCP_SERVER_UUID}?transport=sse&include_composio_helper_actions=true"

DEBUG_MODE = True

def debug_print(message, data=None, timestamp=True):
    """Enhanced debug printing function with timestamps"""
    if DEBUG_MODE:
        ts = f"[{datetime.now().strftime('%H:%M:%S.%f')[:-3]}] " if timestamp else ""
        print(f"\n=== DEBUG {ts}: {message} ===")
        if data is not None:
            try:
                if isinstance(data, (dict, list)):
                    print(json.dumps(data, indent=2, default=str))
                else:
                    print(str(data))
            except Exception as e:
                print(f"Could not serialize debug data: {e}")
                print(repr(data))
        print("=" * (len(message) + 20 + (len(ts) if timestamp else 0)))

def timing_info(start_time, operation):
    """Helper to calculate and log timing information"""
    elapsed = time.time() - start_time
    debug_print(f"{operation} completed", {"elapsed_seconds": round(elapsed, 3)})
    return elapsed

# --- 2. Fixed MCP Connection Manager ---
class UltraRobustMCPManager:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.session: Optional[ClientSession] = None
        self.streams_context = None
        self.session_context = None
        self.is_connected = False
        self.connection_lock = asyncio.Lock()

        # Enhanced timing and lifecycle tracking
        self.connection_start_time: Optional[float] = None
        self.last_successful_operation: Optional[float] = None
        self.connection_attempts = 0
        self.successful_operations = 0
        self.connection_id = 0

        # Aggressive connection management
        self.keep_alive_task: Optional[asyncio.Task] = None
        self.preemptive_reconnect_task: Optional[asyncio.Task] = None
        self.max_connection_age = 50  # Preemptively reconnect after 50 seconds
        self.keep_alive_interval = 8   # Very frequent keep-alive (8 seconds)

    async def connect(self) -> bool:
        """Establish connection with corrected timeout handling"""
        async with self.connection_lock:
            start_time = time.time()
            self.connection_attempts += 1
            self.connection_id += 1

            try:
                debug_print(f"Establishing MCP connection (attempt #{self.connection_attempts}, id #{self.connection_id})", {
                    "server_url": self.server_url,
                    "timestamp": datetime.now().isoformat(),
                    "max_age_seconds": self.max_connection_age
                })

                # Clean up any existing connection
                await self._cleanup_internal()

                # Create SSE client with FIXED timeout parameters (no httpx.Timeout object)
                self.streams_context = sse_client(
                    url=self.server_url,
                    headers={
                        "Accept": "text/event-stream",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "User-Agent": "MCP-Client/2.0",
                        "Keep-Alive": "timeout=45, max=1000"  # Explicit keep-alive
                    },
                    sse_read_timeout=30.0  # Simple float timeout (FIXED)
                    # Removed the invalid timeout=timeout_config parameter
                )

                debug_print("Creating SSE streams with corrected timeouts")
                streams = await self.streams_context.__aenter__()

                debug_print("Creating MCP session")
                self.session_context = ClientSession(*streams)
                self.session = await self.session_context.__aenter__()

                debug_print("Initializing MCP session")
                await self.session.initialize()

                # Test connection immediately with proper None check
                debug_print("Testing connection with initial list_tools")
                if self.session is not None:
                    tools_response = await self.session.list_tools()
                    debug_print("Initial connection test successful", {
                        "tools_count": len(tools_response.tools)
                    })
                else:
                    raise RuntimeError("Session is None after initialization")

                self.is_connected = True
                self.connection_start_time = time.time()
                self.last_successful_operation = time.time()

                elapsed = timing_info(start_time, "Enhanced MCP connection establishment")

                debug_print("MCP connection established successfully", {
                    "connection_id": self.connection_id,
                    "tools_count": len(tools_response.tools),
                    "setup_time_seconds": round(elapsed, 3),
                    "keep_alive_interval": self.keep_alive_interval,
                    "max_connection_age": self.max_connection_age
                })

                # Start aggressive management tasks
                self.keep_alive_task = asyncio.create_task(self._aggressive_keep_alive_loop())
                self.preemptive_reconnect_task = asyncio.create_task(self._preemptive_reconnect_loop())

                return True

            except Exception as e:
                elapsed = timing_info(start_time, "Failed MCP connection attempt")
                debug_print("Failed to establish MCP connection", {
                    "connection_id": self.connection_id,
                    "attempt": self.connection_attempts,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "elapsed_seconds": round(elapsed, 3)
                })
                await self._cleanup_internal()
                return False

    async def _aggressive_keep_alive_loop(self):
        """Very aggressive keep-alive with detailed monitoring"""
        ping_count = 0
        consecutive_failures = 0

        while self.is_connected:
            try:
                await asyncio.sleep(self.keep_alive_interval)

                if not self.is_connected:
                    break

                ping_count += 1
                ping_start = time.time()

                # FIXED: Proper None check before calling methods
                if self.session is not None:
                    # Multiple types of health checks
                    await self.session.list_tools()

                    # Reset failure counter on success
                    consecutive_failures = 0
                    self.last_successful_operation = time.time()

                    ping_elapsed = time.time() - ping_start
                    connection_age = (
                        round(time.time() - self.connection_start_time, 1)
                        if self.connection_start_time is not None
                        else "unknown"
                    )

                    debug_print(f"Aggressive keep-alive ping #{ping_count} successful", {
                        "connection_id": self.connection_id,
                        "ping_time_ms": round(ping_elapsed * 1000, 1),
                        "connection_age_seconds": connection_age,
                        "consecutive_failures": consecutive_failures,
                        "next_ping_in": self.keep_alive_interval
                    })
                else:
                    debug_print(f"Keep-alive ping #{ping_count} failed - session is None")
                    self.is_connected = False
                    break

            except Exception as e:
                consecutive_failures += 1
                connection_age = (
                    round(time.time() - self.connection_start_time, 1)
                    if self.connection_start_time is not None
                    else "unknown"
                )

                debug_print(f"Keep-alive ping #{ping_count} failed", {
                    "connection_id": self.connection_id,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "connection_age_seconds": connection_age,
                    "consecutive_failures": consecutive_failures
                })

                # If we have multiple consecutive failures, mark as disconnected
                if consecutive_failures >= 2:
                    debug_print("Multiple consecutive keep-alive failures, marking as disconnected")
                    self.is_connected = False
                    break

    async def _preemptive_reconnect_loop(self):
        """Preemptively reconnect before hitting server timeout"""
        while self.is_connected:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds

                if not self.is_connected or self.connection_start_time is None:
                    break

                connection_age = time.time() - self.connection_start_time

                # Preemptively reconnect before hitting the ~60 second timeout
                if connection_age >= self.max_connection_age:
                    debug_print("Preemptive reconnection triggered", {
                        "connection_age_seconds": round(connection_age, 1),
                        "max_age_seconds": self.max_connection_age,
                        "connection_id": self.connection_id
                    })

                    # Mark current connection as needing replacement
                    self.is_connected = False
                    break

            except Exception as e:
                debug_print("Error in preemptive reconnect loop", str(e))
                break

    async def health_check_with_retry(self) -> bool:
        """Enhanced health check with immediate retry capability and proper None checks"""
        if not self.session or not self.is_connected:
            return False

        max_retries = 2
        for attempt in range(max_retries):
            try:
                start_time = time.time()
                # FIXED: Explicit None check before method call
                if self.session is not None:
                    await self.session.list_tools()
                else:
                    debug_print("Health check failed - session became None during check")
                    return False

                elapsed = time.time() - start_time

                debug_print("Health check passed", {
                    "connection_id": self.connection_id,
                    "attempt": attempt + 1,
                    "response_time_ms": round(elapsed * 1000, 1)
                })
                self.last_successful_operation = time.time()
                return True

            except Exception as e:
                debug_print("Health check failed", {
                    "connection_id": self.connection_id,
                    "attempt": attempt + 1,
                    "error_type": type(e).__name__,
                    "error_message": str(e)
                })

                if attempt < max_retries - 1:
                    await asyncio.sleep(1)  # Brief pause before retry

        self.is_connected = False
        return False

    async def ensure_connected_with_preemption(self) -> bool:
        """Enhanced connection management with preemptive handling"""
        # Check if we need preemptive reconnection
        if (self.is_connected and self.connection_start_time is not None and
            time.time() - self.connection_start_time >= self.max_connection_age):
            debug_print("Connection approaching timeout, preemptively reconnecting")
            self.is_connected = False

        if not self.is_connected or self.session is None:
            debug_print("Connection lost or aged out, reconnecting")
            return await self.connect()

        # Perform enhanced health check
        if not await self.health_check_with_retry():
            debug_print("Health check failed, reconnecting")
            return await self.connect()

        return True

    async def safe_operation(self, operation_name: str, operation_func):
        """Safely execute MCP operations with automatic reconnection and proper None checks"""
        if not await self.ensure_connected_with_preemption():
            raise ConnectionError(f"Could not establish MCP connection for {operation_name}")

        # FIXED: Explicit None check before using session
        if self.session is None:
            raise RuntimeError(f"Session is None for {operation_name} after connection validation")

        try:
            start_time = time.time()
            result = await operation_func()
            elapsed = time.time() - start_time

            self.successful_operations += 1
            debug_print(f"{operation_name} completed successfully", {
                "connection_id": self.connection_id,
                "operation_count": self.successful_operations,
                "response_time_ms": round(elapsed * 1000, 1)
            })

            self.last_successful_operation = time.time()
            return result

        except Exception as e:
            debug_print(f"{operation_name} failed", {
                "connection_id": self.connection_id,
                "error_type": type(e).__name__,
                "error_message": str(e)
            })
            self.is_connected = False
            raise

    async def _cleanup_internal(self):
        """Internal cleanup with task cancellation"""
        # Cancel management tasks
        for task in [self.keep_alive_task, self.preemptive_reconnect_task]:
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self.keep_alive_task = None
        self.preemptive_reconnect_task = None

        # Clean up connections
        try:
            if self.session_context is not None:
                await self.session_context.__aexit__(None, None, None)
            if self.streams_context is not None:
                await self.streams_context.__aexit__(None, None, None)
        except Exception as e:
            debug_print("Error during internal cleanup", str(e))

        self.session = None
        self.session_context = None
        self.streams_context = None

    async def close(self):
        """Close the connection"""
        debug_print("Closing MCP connection", {
            "connection_id": self.connection_id,
            "successful_operations": self.successful_operations
        })
        self.is_connected = False
        await self._cleanup_internal()

# --- 3. Enhanced Gemini API Wrapper ---
class EnhancedGeminiWrapper:
    def __init__(self, client, model_name, mcp_manager):
        self.client = client
        self.model_name = model_name
        self.mcp_manager = mcp_manager

    async def generate_content_with_ultra_robust_mcp(self, contents, config):
        """Generate content with ultra-robust MCP handling"""
        start_time = time.time()

        try:
            # Use safe operation for connection validation
            debug_print("Pre-validating MCP connection before Gemini call")
            connection_valid = await self.mcp_manager.ensure_connected_with_preemption()

            if not connection_valid:
                debug_print("MCP connection validation failed, proceeding without tools")
                config = types.GenerateContentConfig(
                    temperature=config.temperature if hasattr(config, 'temperature') else 0.1,
                    tools=[]
                )

            debug_print("Making Gemini API call", {
                "model": self.model_name,
                "has_mcp_tools": len(config.tools) > 0 if hasattr(config, 'tools') and config.tools else False,
                "contents_length": len(contents),
                "connection_id": self.mcp_manager.connection_id
            })

            response = await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config
            )

            elapsed = timing_info(start_time, "Gemini API call")

            debug_print("Gemini API call successful", {
                "candidates_count": len(response.candidates) if response.candidates else 0,
                "response_time_seconds": round(elapsed, 3),
                "connection_id": self.mcp_manager.connection_id
            })

            return response

        except (McpError, ClosedResourceError) as e:
            elapsed = timing_info(start_time, "Failed Gemini API call (MCP error)")
            debug_print("MCP-related error during Gemini call", {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "elapsed_seconds": round(elapsed, 3),
                "connection_id": self.mcp_manager.connection_id,
                "will_retry_without_tools": True
            })

            # Mark MCP as disconnected and retry without tools
            self.mcp_manager.is_connected = False

            debug_print("Retrying Gemini call without MCP tools")
            retry_config = types.GenerateContentConfig(
                temperature=config.temperature if hasattr(config, 'temperature') else 0.1,
                tools=[]
            )

            return await self.client.aio.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=retry_config
            )

        except Exception as e:
            elapsed = timing_info(start_time, "Failed Gemini API call (other error)")
            debug_print("Non-MCP error during Gemini call", {
                "error_type": type(e).__name__,
                "error_message": str(e),
                "elapsed_seconds": round(elapsed, 3)
            })
            raise

# --- 4. Initialize Gemini Client ---
try:
    client = genai.Client(api_key=GOOGLE_API_KEY)
    GEMINI_MODEL_NAME = "gemini-2.5-flash-preview-05-20"
    print(f"Successfully initialized Gemini client with model: {GEMINI_MODEL_NAME}")
except Exception as e:
    print(f"Error initializing Gemini client: {e}")
    sys.exit(1)

def safe_extract_text_from_parts(parts):
    """Safely extract text from response parts"""
    if not parts:
        return ""

    text_parts = []
    for part in parts:
        try:
            if hasattr(part, 'text') and part.text:
                text_parts.append(part.text)
        except Exception as e:
            debug_print(f"Error extracting text from part: {e}")
            continue

    return "".join(text_parts)

# --- 5. Main Agent Logic ---
async def run_ultra_robust_gemini_mcp_agent():
    session_start_time = time.time()
    print(f"Attempting to connect to MCP Server URL: {MCP_SERVER_URL}")

    # Create ultra-robust MCP connection manager
    mcp_manager = UltraRobustMCPManager(MCP_SERVER_URL)
    conversation_history = []
    turn_count = 0

    try:
        # Establish initial connection
        if not await mcp_manager.connect():
            print("Failed to establish MCP connection. Exiting.")
            return

        print("Successfully established SSE transport with the server.")
        print("MCP Session initialized successfully. Composio server should be ready.")

        # Create enhanced Gemini API wrapper
        gemini_wrapper = EnhancedGeminiWrapper(client, GEMINI_MODEL_NAME, mcp_manager)

        # List tools using safe operation with FIXED None check
        try:
            # FIXED: Use safe_operation with proper lambda that checks session
            tools_response = await mcp_manager.safe_operation(
                "list_tools",
                lambda: mcp_manager.session.list_tools() if mcp_manager.session is not None else None
            )
            if tools_response is not None:
                discovered_tools = [tool.name for tool in tools_response.tools]
                print(f"Discovered MCP Tools: {discovered_tools}")
            else:
                print("Could not list tools: session is None")
        except Exception as e:
            print(f"Could not list tools: {e}")

        print(f"\n🚀 Ultra-Robust Gemini MCP Agent Started!")
        print(f"⚡ Keep-alive interval: {mcp_manager.keep_alive_interval}s")
        print(f"🔄 Max connection age: {mcp_manager.max_connection_age}s")
        print(f"Type 'quit' to exit.")

        while True:
            user_input = input("\nYou: ").strip()
            if user_input.lower() == 'quit':
                break
            if not user_input:
                continue

            turn_start_time = time.time()
            turn_count += 1

            print(f"DEBUG: Sending to Gemini: {user_input}")

            # Safe calculations for timing
            session_age = round(time.time() - session_start_time, 1)
            connection_age = (
                round(time.time() - mcp_manager.connection_start_time, 1)
                if mcp_manager.connection_start_time is not None
                else "N/A"
            )

            debug_print(f"Starting conversation turn #{turn_count}", {
                "session_age_seconds": session_age,
                "mcp_connection_age_seconds": connection_age,
                "mcp_connection_id": mcp_manager.connection_id,
                "user_input_length": len(user_input)
            })

            # Add user message to history
            conversation_history.append({
                'role': 'user',
                'parts': [{'text': user_input}]
            })

            debug_print("Current conversation history length", len(conversation_history))

            try:
                # Prepare config with enhanced MCP session and FIXED None check
                config = types.GenerateContentConfig(
                    temperature=0.1,
                    tools=[mcp_manager.session] if mcp_manager.session is not None and mcp_manager.is_connected else []
                )

                # Use enhanced wrapper for ultra-robust handling
                response = await gemini_wrapper.generate_content_with_ultra_robust_mcp(
                    contents=conversation_history,
                    config=config
                )

                # Extract and display response
                model_response_text = ""
                if response.candidates and len(response.candidates) > 0:
                    candidate = response.candidates[0]
                    if candidate.content and candidate.content.parts:
                        model_response_text = safe_extract_text_from_parts(candidate.content.parts)

                if model_response_text:
                    print(f"\nGemini: {model_response_text}")
                else:
                    print(f"\nGemini: [Processing tools...]")

                # Add response to history
                if response.candidates and len(response.candidates) > 0:
                    candidate_content = response.candidates[0].content
                    if candidate_content and candidate_content.parts:
                        parts_for_history = []
                        for part in candidate_content.parts:
                            try:
                                if hasattr(part, 'text') and part.text:
                                    parts_for_history.append({'text': part.text})
                            except Exception as e:
                                debug_print(f"Error processing part for history: {e}")
                                parts_for_history.append({'text': str(part)})

                        conversation_history.append({
                            'role': 'model',
                            'parts': parts_for_history
                        })
                    else:
                        conversation_history.append({
                            'role': 'model',
                            'parts': [{'text': model_response_text or "No response"}]
                        })
                else:
                    conversation_history.append({
                        'role': 'model',
                        'parts': [{'text': model_response_text or "No response generated"}]
                    })

                turn_elapsed = timing_info(turn_start_time, f"Conversation turn #{turn_count}")
                debug_print("Turn completed successfully", {
                    "turn_number": turn_count,
                    "total_turn_time_seconds": round(turn_elapsed, 3),
                    "conversation_length": len(conversation_history),
                    "mcp_connected": mcp_manager.is_connected,
                    "mcp_connection_id": mcp_manager.connection_id,
                    "successful_operations": mcp_manager.successful_operations
                })

            except Exception as e:
                turn_elapsed = timing_info(turn_start_time, f"Failed conversation turn #{turn_count}")
                error_msg = f"An error occurred: {str(e)}"
                print(f"Error during Gemini API call or tool execution: {e}")
                debug_print("Exception Details", {
                    "turn_number": turn_count,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "turn_time_seconds": round(turn_elapsed, 3),
                    "mcp_connected": mcp_manager.is_connected,
                    "traceback": traceback.format_exc()
                })

                # Add error to history
                conversation_history.append({
                    'role': 'model',
                    'parts': [{'text': error_msg}]
                })

    except Exception as e:
        session_elapsed = timing_info(session_start_time, "Session (with error)")
        print(f"An unexpected error occurred: {e}")
        debug_print("Unexpected Session Error", {
            "error_type": type(e).__name__,
            "error_message": str(e),
            "session_time_seconds": round(session_elapsed, 3),
            "turns_completed": turn_count,
            "traceback": traceback.format_exc()
        })
    finally:
        session_elapsed = timing_info(session_start_time, "Total session")
        print(f"\nClosing MCP connection... (Session ran for {round(session_elapsed, 1)} seconds)")
        await mcp_manager.close()
        print("Agent session ended.")

# --- 6. Testing Helper Functions ---
def test_client_initialization():
    """Test function to verify client setup"""
    try:
        test_client = genai.Client(api_key=GOOGLE_API_KEY)
        print("✓ Client initialization test passed")
        return True
    except Exception as e:
        print(f"✗ Client initialization test failed: {e}")
        return False

def test_env_variables():
    """Test function to verify environment variables"""
    missing = []
    if not GOOGLE_API_KEY:
        missing.append("GOOGLE_API_KEY")
    if not INTEGRATED_MCP_SERVER_UUID:
        missing.append("INTEGRATED_MCP_SERVER_UUID")

    if missing:
        print(f"✗ Missing environment variables: {missing}")
        return False
    else:
        print("✓ Environment variables test passed")
        return True

async def start_chat_session():
    """Main chat function that can be called from other modules"""
    # Move all your main logic here
    print("🚀 Starting Ultra-Robust Gemini MCP Agent...")

    # Run basic tests
    if not test_env_variables() or not test_client_initialization():
        print("Pre-flight checks failed. Exiting.")
        return False

    try:
        await run_ultra_robust_gemini_mcp_agent()
        return True
    except KeyboardInterrupt:
        print("\nExiting chat...")
        return False

if __name__ == "__main__":
    asyncio.run(start_chat_session())
