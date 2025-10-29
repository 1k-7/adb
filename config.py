import os
from pymongo import MongoClient
from dotenv import load_dotenv

# Load .env file for local development
load_dotenv()

# --- Database Connection ---
MONGO_URI_ENV = os.environ.get("MONGO_URI")

if not MONGO_URI_ENV:
    print("FATAL: MONGO_URI not found in environment.")
    exit(1)

client = MongoClient(MONGO_URI_ENV)
db = client["multi_bot_db"]

# --- Settings Collection (for IDs) ---
settings_collection = db["settings"]

def get_setting(key, env_var):
    """
    1. Check for environment variable. If present, update DB and return it.
    2. If no env var, check DB. If present, return it.
    3. If not in env or DB, return None.
    """
    env_value = os.environ.get(env_var)
    
    if env_value:
        # If env var is set, it's the "source of truth".
        settings_collection.update_one(
            {"_id": key},
            {"$set": {"value": env_value}},
            upsert=True
        )
        return env_value
    else:
        # If no env var, try to load from DB
        stored_setting = settings_collection.find_one({"_id": key})
        if stored_setting:
            return stored_setting.get("value")
    
    return None

# --- Load All Configs ---
print("Loading configuration...")
MONGO_URI = MONGO_URI_ENV
ADMIN_ID = get_setting("admin_id", "ADMIN_ID")
OWNER_ID = get_setting("owner_id", "OWNER_ID")
DEV_ID = get_setting("dev_id", "DEV_ID")

# Convert IDs to integers for comparisons
AUTH_USERS = [int(uid) for uid in [ADMIN_ID, OWNER_ID, DEV_ID] if uid]

if not AUTH_USERS:
    print("Warning: No admin/owner/dev IDs configured.")
else:
    print(f"Authorized users: {AUTH_USERS}")


# --- Shared Collections ---
print("Loading database collections...")
accounts_collection = db["accounts"]
targets_collection = db["targets"] # Stores target chat IDs
scheduler_collection = db["scheduler"] # Stores interval settings
message_collection = db["message"] # Stores the message to be sent

# --- Default Scheduler (if none in DB) ---
# We use update_one with upsert to set a default only if it doesn't exist
scheduler_collection.update_one(
    {"_id": "main_interval"},
    {"$setOnInsert": {
        "type": "random",
        "min_minutes": 5,
        "max_minutes": 10
    }},
    upsert=True
)

# --- Default Message (if none in DB) ---
message_collection.update_one(
    {"_id": "main_message"},
    {"$setOnInsert": {
        "type": "text",
        "content": "This is the default message. Please change me using /set_message.",
    }},
    upsert=True
)

print("Configuration loaded successfully.")
