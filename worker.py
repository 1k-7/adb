import asyncio
import random
import os
import time

# Import all our DB collections
from config import (
    accounts_collection, targets_collection, 
    scheduler_collection, message_collection
)

# --- DUAL LIBRARY IMPORTS ---
# We try to import both libraries and their specific error types
try:
    from telethon import TelegramClient
    from telethon.errors import (
        SessionPasswordNeededError as Telethon2FAError,
        FloodWaitError as TelethonFloodWait,
        UserDeactivatedBanError as TelethonBanned
    )
    from telethon.sessions import StringSession
    print("Telethon library loaded.")
except ImportError:
    print("Warning: Telethon not installed. Telethon sessions will fail.")
    TelethonClient = None

try:
    # Use pyroblack if available, fall back to pyrogram
    try:
        from pyroblack import Client
        from pyroblack.errors import (
            SessionPasswordNeeded as Pyrogram2FAError,
            FloodWait as PyrogramFloodWait,
            UserDeactivatedBan as PyrogramBanned
        )
        print("Pyroblack library loaded.")
    except ImportError:
        from pyrogram import Client
        from pyrogram.errors import (
            SessionPasswordNeeded as Pyrogram2FAError,
            FloodWait as PyrogramFloodWait,
            UserDeactivatedBan as PyrogramBanned
        )
        print("Pyrogram library loaded.")
except ImportError:
    print("Warning: Pyrogram/Pyroblack not installed. Pyrogram sessions will fail.")
    Client = None


# --- Global Client Cache ---
# Cache will store a dictionary of active clients
# { "session_name.session": {"client": <ClientObject>, "type": "telethon" | "pyrogram"} }
ACTIVE_CLIENTS = {}
CURRENT_ACCOUNT_INDEX = 0

async def load_and_verify_accounts():
    """
    Connects to all 'active' or 'new' accounts from the DB.
    Conditionally uses Telethon or Pyrogram.
    """
    print("Verifying and loading accounts...")
    accounts = accounts_collection.find({"status": {"$in": ["new", "active"]}})
    
    for acc in accounts:
        session_file = acc['session_file']
        session_path = os.path.join("sessions", session_file)
        
        # Pyrogram sessions are often just 'name', not 'name.session'
        # We'll use the session_file as the name, and store it in the 'sessions/' dir
        pyrogram_session_name = os.path.splitext(session_file)[0]
        
        api_id = acc['api_id']
        api_hash = acc['api_hash']
        client_type = acc.get('client_type', 'telethon') # Default to telethon if not set
        
        if session_file in ACTIVE_CLIENTS:
            continue # Already loaded

        client = None
        try:
            if client_type == "telethon":
                if TelethonClient is None:
                    continue
                client = TelegramClient(session_path, api_id, api_hash)
                await client.connect()
                
                if not await client.is_user_authorized():
                    raise Telethon2FAError("Session not authorized. Needs login.")

            elif client_type == "pyrogram":
                if Client is None:
                    continue
                
                #
                # --- THIS IS THE FIX ---
                # The 'name' should be just the session name, not the path.
                # The 'workdir' handles the path.
                #
                client = Client(
                    name=pyrogram_session_name, # CHANGED
                    api_id=api_id,
                    api_hash=api_hash,
                    workdir="sessions" # This tells Pyrogram to look in the /app/sessions/ folder
                )
                # --- END OF FIX ---
                
                await client.start() # Pyrogram uses start() to connect
                
            # Success!
            print(f"[+] {client_type} session {session_file} loaded and authorized.")
            accounts_collection.update_one(
                {"_id": acc['_id']}, {"$set": {"status": "active"}}
            )
            ACTIVE_CLIENTS[session_file] = {"client": client, "type": client_type}

        except (Telethon2FAError, Pyrogram2FAError):
            print(f"[!] Session {session_file} needs 2FA/Auth. Marking as 'error'.")
            accounts_collection.update_one(
                {"_id": acc['_id']},
                {"$set": {"status": "error", "error_message": "2FA/Auth required"}}
            )
            if client and client.is_connected:
                await (client.disconnect() if client_type == "telethon" else client.stop())
        
        except Exception as e:
            print(f"[!] Error loading {session_file} ({client_type}): {e}")
            accounts_collection.update_one(
                {"_id": acc['_id']},
                {"$set": {"status": "error", "error_message": str(e)}}
            )
            if client and client.is_connected:
                await (client.disconnect() if client_type == "telethon" else client.stop())

async def get_next_client_details():
    """
    Gets the next active client object and its type.
    This implements the account switching.
    """
    global CURRENT_ACCOUNT_INDEX
    
    active_keys = list(ACTIVE_CLIENTS.keys())
    if not active_keys:
        return None, None, None

    # Cycle through the index
    CURRENT_ACCOUNT_INDEX = (CURRENT_ACCOUNT_INDEX + 1) % len(active_keys)
    session_name = active_keys[CURRENT_ACCOUNT_INDEX]
    
    client_details = ACTIVE_CLIENTS.get(session_name)
    if not client_details:
        return None, None, None
        
    return client_details['client'], client_details['type'], session_name

async def get_interval():
    """Gets the sleep interval from the DB."""
    try:
        schedule = scheduler_collection.find_one({"_id": "main_interval"})
        if schedule:
            min_m = schedule.get("min_minutes", 5)
            max_m = schedule.get("max_minutes", 10)
            return random.randint(min_m * 60, max_m * 60) # In seconds
    except Exception as e:
        print(f"Error reading interval, using default: {e}")
    
    # Default fallback
    return random.randint(300, 600)

async def worker_loop():
    """The main sending loop, reading all settings from the DB."""
    while True:
        # 1. Load any new/changed accounts
        await load_and_verify_accounts()
        
        # 2. Get the next client to use
        client, client_type, session_name = await get_next_client_details()
        
        if not client:
            print("No active clients found. Sleeping for 60s...")
            await asyncio.sleep(60)
            continue
            
        # 3. Load settings from DB *inside* the loop (so they are fresh)
        message_data = message_collection.find_one({"_id": "main_message"})
        if not message_data:
            print("No message set in DB. Sleeping for 60s.")
            await asyncio.sleep(60)
            continue

        targets = list(targets_collection.find({}))
        if not targets:
            print("No targets set in DB. Sleeping for 60s.")
            await asyncio.sleep(60)
            continue

        # 4. Process all targets with the current client
        print(f"Worker {session_name} ({client_type}) processing {len(targets)} targets.")
        
        for target_doc in targets:
            target_id = target_doc["_id"] # This is the chat_id or @username
            
            try:
                # === CONDITIONAL SEND/FORWARD LOGIC ===
                msg_type = message_data.get("type")
                
                if msg_type == "forward":
                    from_chat = message_data['from_chat_id']
                    msg_id = message_data['message_id']
                    
                    if client_type == "telethon":
                        await client.forward_messages(target_id, msg_id, from_chat)
                    elif client_type == "pyrogram":
                        await client.forward_messages(target_id, from_chat, msg_id)
                
                elif msg_type == "text":
                    content = message_data.get("content", "")
                    if client_type == "telethon":
                        await client.send_message(target_id, content)
                    elif client_type == "pyrogram":
                        await client.send_message(target_id, content)

                elif msg_type == "photo":
                    file_id = message_data.get("file_id")
                    caption = message_data.get("content", "")
                    if client_type == "telethon":
                        await client.send_file(target_id, file_id, caption=caption)
                    elif client_type == "pyrogram":
                        await client.send_photo(target_id, file_id, caption=caption)

                elif msg_type == "video":
                    file_id = message_data.get("file_id")
                    caption = message_data.get("content", "")
                    if client_type == "telethon":
                        await client.send_file(target_id, file_id, caption=caption)
                    elif client_type == "pyrogram":
                        await client.send_video(target_id, file_id, caption=caption)

                elif msg_type == "document":
                    file_id =.get("file_id")
                    caption = message_data.get("content", "")
                    if client_type == "telethon":
                        await client.send_file(target_id, file_id, caption=caption)
                    elif client_type == "pyrogram":
                        await client.send_document(target_id, file_id, caption=caption)
                
                print(f"  > Successfully sent to {target_id}")
                # Small sleep between targets to avoid internal flood limits
                await asyncio.sleep(random.randint(2, 5)) 

            except (TelethonBanned, PyrogramBanned) as e:
                print(f"[!] ACCOUNT BANNED: {session_name}. Marking as 'banned'.")
                accounts_collection.update_one(
                    {"session_file": session_name},
                    {"$set": {"status": "banned", "error_message": str(e)}}
                )
                if session_name in ACTIVE_CLIENTS:
                    del ACTIVE_CLIENTS[session_name]
                break # Stop processing targets for this banned account
                
            except (TelethonFloodWait, PyrogramFloodWait) as e:
                sleep_time = e.x if hasattr(e, 'x') else e.seconds
                print(f"[!] Flood Wait on {session_name}: sleeping for {sleep_time}s.")
                await asyncio.sleep(sleep_time)
                
            except Exception as e:
                print(f"[!] Error sending to {target_id} with {session_name}: {e}")
                # This could be a bad target, e.g. "Chat not found"
                # We'll just log it and continue

        # 5. Wait for the main interval
        sleep_time = await get_interval()
        print(f"Cycle complete for {session_name}. Sleeping for {sleep_time} seconds...")
        await asyncio.sleep(sleep_time)

if __name__ == "__main__":
    print("Session Worker started...")
    try:
        asyncio.run(worker_loop())
    except KeyboardInterrupt:
        print("Worker shutting down.")
