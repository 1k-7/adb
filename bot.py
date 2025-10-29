import os
import zipfile
import functools
import html
from telegram import Update, Bot, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    filters, ContextTypes
)
from telegram.constants import ParseMode

# Import config and collections from our config.py file
from config import (
    AUTH_USERS, accounts_collection, targets_collection, 
    scheduler_collection, message_collection
)

# --- Bot Token ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("No BOT_TOKEN found in environment!")

# --- Auth Decorator ---
def auth_required(func):
    """Restricts command access to users in AUTH_USERS."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if update.effective_user.id not in AUTH_USERS:
            await update.message.reply_text("⛔ You are not authorized to use this command.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

# === CONVERSATION 1: Add Account ===

# --- Conversation States ---
(API_ID, API_HASH, CLIENT_TYPE, SESSION_FILE) = range(4)

@auth_required
async def start_add_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Let's add a new account.\n"
                                    "Please send me the <b>API_ID</b>.",
                                    parse_mode=ParseMode.HTML)
    return API_ID

async def receive_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['api_id'] = update.message.text.strip()
    await update.message.reply_text("Got it. Now, please send the <b>API_HASH</b>.",
                                    parse_mode=ParseMode.HTML)
    return API_HASH

async def receive_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['api_hash'] = update.message.text.strip()
    
    reply_keyboard = [["Telethon", "Pyrogram/Pyroblack"]]
    await update.message.reply_text(
        "Great. Now, what type of session is this?",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True),
    )
    return CLIENT_TYPE

async def receive_client_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    client_type = update.message.text.lower()
    if "pyrogram" in client_type or "pyroblack" in client_type:
        context.user_data['client_type'] = "pyrogram"
    elif "telethon" in client_type:
        context.user_data['client_type'] = "telethon"
    else:
        await update.message.reply_text("Please select 'Telethon' or 'Pyrogram/Pyroblack'.")
        return CLIENT_TYPE
        
    await update.message.reply_text(f"Understood. A {context.user_data['client_type']} session.\n"
                                    "Now, upload the <code>.session</code> file or a <code>.zip</code> file.",
                                    parse_mode=ParseMode.HTML)
    return SESSION_FILE

async def receive_session_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not (doc.file_name.endswith(".session") or doc.file_name.endswith(".zip")):
        await update.message.reply_text("That's not a <code>.session</code> or <code>.zip</code> file. "
                                        "Please upload a valid file.",
                                        parse_mode=ParseMode.HTML)
        return SESSION_FILE

    file = await context.bot.get_file(doc.file_id)
    file_path = os.path.join("sessions", doc.file_name)
    os.makedirs("sessions", exist_ok=True)
    
    await file.download_to_drive(file_path)
    
    api_id = context.user_data['api_id']
    api_hash = context.user_data['api_hash']
    client_type = context.user_data['client_type']
    processed_files = []

    try:
        if doc.file_name.endswith(".zip"):
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                for name in zip_ref.namelist():
                    if name.endswith(".session"):
                        zip_ref.extract(name, "sessions")
                        session_name = os.path.basename(name)
                        processed_files.append(session_name)
            os.remove(file_path) # Clean up zip
        else:
            session_name = doc.file_name
            processed_files.append(session_name)

        # Add all processed sessions to the database
        for session_name in processed_files:
            accounts_collection.update_one(
                {"_id": session_name},
                {"$set": {
                    "api_id": api_id,
                    "api_hash": api_hash,
                    "client_type": client_type,
                    "session_file": session_name,
                    "status": "new" # Worker will pick this up
                }},
                upsert=True
            )
        
        await update.message.reply_text(
            f"✅ Successfully processed {len(processed_files)} session(s) as {client_type}.\n"
            f"The worker will now attempt to connect.",
            reply_markup=ReplyKeyboardRemove()
        )

    except Exception as e:
        await update.message.reply_text(f"An error occurred: {e}")
    
    context.user_data.clear()
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Action canceled.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END

# === CONVERSATION 2: Set Message ===

SET_MESSAGE_STATE = range(1)

@auth_required
async def set_message_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the process to set the new master message."""
    await update.message.reply_text(
        "Please send me the new message you want the accounts to send. "
        "This will replace the current message.\n\n"
        "To set a message for <b>forwarding</b>, just forward a message here.\n"
        "To set a <b>text/media message</b>, just send it normally.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.HTML
    )
    return SET_MESSAGE_STATE

async def set_message_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Saves the new message to the database."""
    if not update.message:
        await update.message.reply_text("Invalid message. Action canceled.")
        return ConversationHandler.END

    message_data = {}
    info = ""
    
    if update.message.forward_from or update.message.forward_from_chat:
        # This is a forwarded message
        message_data = {
            "type": "forward",
            "from_chat_id": update.message.forward_from_chat.id if update.message.forward_from_chat else update.message.forward_from.id,
            "message_id": update.message.forward_from_message_id if update.message.forward_from_chat else update.message.message_id
        }
        info = "Saved as a message to be <b>forwarded</b>."
        
    elif update.message.text:
        message_data = {
            "type": "text",
            "content": update.message.text,
            "entities": update.message.entities
        }
        info = "Saved as a <b>text message</b> (will be sent as a copy)."
        
    elif update.message.photo:
        message_data = {
            "type": "photo",
            "file_id": update.message.photo[-1].file_id,
            "content": update.message.caption,
            "entities": update.message.caption_entities
        }
        info = "Saved as a <b>photo</b> (will be sent as a copy)."

    elif update.message.video:
        message_data = {
            "type": "video",
            "file_id": update.message.video.file_id,
            "content": update.message.caption,
            "entities": update.message.caption_entities
        }
        info = "Saved as a <b>video</b> (will be sent as a copy)."

    elif update.message.document:
        message_data = {
            "type": "document",
            "file_id": update.message.document.file_id,
            "content": update.message.caption,
            "entities": update.message.caption_entities
        }
        info = "Saved as a <b>document</b> (will be sent as a copy)."
    
    else:
        await update.message.reply_text("This message type isn't supported yet. Canceled.")
        return ConversationHandler.END

    # Save to DB
    message_collection.update_one(
        {"_id": "main_message"},
        {"$set": message_data},
        upsert=True
    )
    
    await update.message.reply_text(f"✅ <b>New message saved!</b>\n{info}", parse_mode=ParseMode.HTML)
    return ConversationHandler.END

# === SIMPLE COMMANDS ===

@auth_required
async def add_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adds a new target chat ID or username."""
    if not context.args:
        await update.message.reply_text("Usage: <code>/add_target &lt;Chat ID or @username&gt;</code>",
                                        parse_mode=ParseMode.HTML)
        return
        
    target_id = context.args[0]
    
    # Try to convert to int, otherwise store as string username
    try:
        target_key = int(target_id)
    except ValueError:
        target_key = target_id.lower()
        if not target_key.startswith('@'):
            target_key = f"@{target_key}"

    targets_collection.update_one(
        {"_id": target_key},
        {"$set": {"added_by": update.effective_user.id}},
        upsert=True
    )
    await update.message.reply_text(f"🎯 Target added: <code>{html.escape(str(target_key))}</code>",
                                    parse_mode=ParseMode.HTML)

@auth_required
async def clear_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Removes all targets."""
    count = targets_collection.delete_many({}).deleted_count
    await update.message.reply_text(f"🔥 All {count} targets have been cleared.")

@auth_required
async def list_targets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lists all current targets."""
    targets = targets_collection.find({})
    target_list = [f"<code>{html.escape(str(t['_id']))}</code>" for t in targets]
    
    if not target_list:
        await update.message.reply_text("No targets are set.")
        return

    await update.message.reply_text("<b>Current Targets:</b>\n" + "\n".join(target_list),
                                    parse_mode=ParseMode.HTML)

@auth_required
async def set_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets the min and max random interval in minutes."""
    if len(context.args) != 2:
        await update.message.reply_text("Usage: <code>/set_interval &lt;min_minutes&gt; &lt;max_minutes&gt;</code>\n"
                                        "Example: <code>/set_interval 10 30</code>",
                                        parse_mode=ParseMode.HTML)
        return
        
    try:
        min_m = int(context.args[0])
        max_m = int(context.args[1])
        
        if min_m > max_m:
            await update.message.reply_text("Min cannot be greater than Max.")
            return

        scheduler_collection.update_one(
            {"_id": "main_interval"},
            {"$set": {
                "type": "random",
                "min_minutes": min_m,
                "max_minutes": max_m
            }},
            upsert=True
        )
        await update.message.reply_text(f"⏰ Interval set to random between {min_m} and {max_m} minutes.")
        
    except ValueError:
        await update.message.reply_text("Invalid numbers. Please use integers.")

@auth_required
async def show_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows an overview of all settings."""
    # 1. Accounts
    active_accounts = accounts_collection.count_documents({"status": "active"})
    error_accounts = accounts_collection.count_documents({"status": "error"})
    new_accounts = accounts_collection.count_documents({"status": "new"})
    banned_accounts = accounts_collection.count_documents({"status": "banned"})
    
    # 2. Interval
    interval = scheduler_collection.find_one({"_id": "main_interval"})
    interval_str = f"{interval['min_minutes']} - {interval['max_minutes']} mins"
    
    # 3. Targets
    target_count = targets_collection.count_documents({})
    
    # 4. Message
    msg = message_collection.find_one({"_id": "main_message"})
    msg_type = msg.get('type', 'N/A').capitalize()
    
    status_text = (
        f"<b>📊 Bot Status</b>\n\n"
        f"<b>Accounts:</b>\n"
        f"  - Active: {active_accounts}\n"
        f"  - Error: {error_accounts}\n"
        f"  - New: {new_accounts}\n"
        f"  - Banned: {banned_accounts}\n\n"
        f"<b>Scheduler:</b>\n"
        f"  - Interval: {interval_str}\n\n"
        f"<b>Messaging:</b>\n"
        f"  - Targets: {target_count}\n"
        f"  - Message Type: {msg_type}"
    )
    
    await update.message.reply_text(status_text, parse_mode=ParseMode.HTML)

#
# --- NEW HELP COMMAND ---
#
@auth_required
async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the help message with all commands."""
    help_text = (
        f"<b>🤖 Bot Command List</b>\n\n"
        f"<b>Management:</b>\n"
        f"  /start or /status - Show bot status\n"
        f"  /help - Show this help message\n\n"
        f"<b>Accounts:</b>\n"
        f"  /add_account - Start conversation to add a new account\n\n"
        f"<b>Messaging:</b>\n"
        f"  /set_message - Set the message to send/forward\n\n"
        f"<b>Targets:</b>\n"
        f"  /add_target <code>&lt;id/@username&gt;</code> - Add a target chat\n"
        f"  /list_targets - List all current targets\n"
        f"  /clear_targets - Clear all targets\n\n"
        f"<b>Scheduler:</b>\n"
        f"  /set_interval <code>&lt;min&gt; &lt;max&gt;</code> - Set random interval in minutes\n\n"
        f"<b>Other:</b>\n"
        f"  /cancel - Cancel the current operation"
    )
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML)


def main():
    """Starts the admin bot."""
    app = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler for adding accounts
    add_account_conv = ConversationHandler(
        entry_points=[CommandHandler("add_account", start_add_account)],
        states={
            API_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_id)],
            API_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_api_hash)],
            CLIENT_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_client_type)],
            SESSION_FILE: [MessageHandler(filters.ATTACHMENT, receive_session_file)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Conversation handler for setting the message
    set_message_conv = ConversationHandler(
        entry_points=[CommandHandler("set_message", set_message_start)],
        states={
            SET_MESSAGE_STATE: [MessageHandler(filters.ALL & ~filters.COMMAND, set_message_receive)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(add_account_conv)
    app.add_handler(set_message_conv)
    
    # Handlers for simple commands
    app.add_handler(CommandHandler("start", show_status))
    app.add_handler(CommandHandler("status", show_status))
    app.add_handler(CommandHandler("add_target", add_target))
    app.add_handler(CommandHandler("clear_targets", clear_targets))
    app.add_handler(CommandHandler("list_targets", list_targets))
    app.add_handler(CommandHandler("set_interval", set_interval))
    
    #
    # --- ADD THE NEW HELP HANDLER ---
    #
    app.add_handler(CommandHandler("help", show_help))
    
    print("Admin Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
