import os
import sys
import zipfile
import rarfile
import py7zr
import shutil
import time
import math
import asyncio
import base64
import json
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import logging
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration - All from environment variables with error checking
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    logger.error("❌ BOT_TOKEN environment variable is required!")
    sys.exit(1)

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024))
TEMP_DIR = os.getenv("TEMP_DIR", "temp_downloads")

# GitHub Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    logger.error("❌ GITHUB_TOKEN environment variable is required!")
    sys.exit(1)

GITHUB_OWNER = os.getenv("GITHUB_OWNER")
if not GITHUB_OWNER:
    logger.error("❌ GITHUB_OWNER environment variable is required!")
    sys.exit(1)

GITHUB_REPO = os.getenv("GITHUB_REPO")
if not GITHUB_REPO:
    logger.error("❌ GITHUB_REPO environment variable is required!")
    sys.exit(1)

GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

# Force Join Configuration
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", "@NCK_Dev")
FORCE_CHANNEL_ID = int(os.getenv("FORCE_CHANNEL_ID", "-1002583286874"))

# Create temp directory
os.makedirs(TEMP_DIR, exist_ok=True)

# User sessions storage
user_data = {}

# Print startup info
logger.info("🤖 Bot starting with GitHub storage integration...")
logger.info(f"📁 GitHub: {GITHUB_OWNER}/{GITHUB_REPO}")
logger.info(f"🌿 Branch: {GITHUB_BRANCH}")

class ProgressBar:
    @staticmethod
    def circular(percentage):
        """Generate a circular progress bar with percentage"""
        if percentage > 100:
            percentage = 100
        if percentage < 0:
            percentage = 0
            
        segments = 12
        filled = int((percentage / 100) * segments)
        if filled > segments:
            filled = segments
            
        filled_char = '●'
        empty_char = '○'
        
        circle = ''
        for i in range(segments):
            if i < filled:
                circle += filled_char
            else:
                circle += empty_char
                
        return f"┌{'─' * segments}┐\n│{circle}│ {percentage:.1f}%\n└{'─' * segments}┘"

class GitHubManager:
    def __init__(self, token, owner, repo, branch="main"):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.repo_full = f"{owner}/{repo}"
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}/contents"
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

    async def upload_file(self, file_path, file_name, user_id):
        """Upload a file to GitHub repository"""
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            
            encoded_content = base64.b64encode(content).decode('utf-8')
            path = f"user_files/{user_id}/{file_name}"
            url = f"{self.base_url}/{path}"
            sha = None
            
            try:
                response = requests.get(url, headers=self.headers)
                if response.status_code == 200:
                    sha = response.json().get('sha')
            except:
                pass
            
            data = {
                "message": f"Upload {file_name} by user {user_id}",
                "content": encoded_content,
                "branch": self.branch
            }
            if sha:
                data["sha"] = sha
            
            response = requests.put(url, headers=self.headers, json=data)
            
            if response.status_code in [200, 201]:
                return True, f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/{path}"
            else:
                return False, f"Upload failed: {response.text}"
                
        except Exception as e:
            return False, str(e)

    async def delete_file(self, file_name, user_id):
        """Delete a file from GitHub repository"""
        try:
            path = f"user_files/{user_id}/{file_name}"
            url = f"{self.base_url}/{path}"
            
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                return False, "File not found"
            
            sha = response.json().get('sha')
            
            data = {
                "message": f"Delete {file_name} by user {user_id}",
                "sha": sha,
                "branch": self.branch
            }
            
            response = requests.delete(url, headers=self.headers, json=data)
            
            if response.status_code in [200, 204]:
                return True, "File deleted successfully"
            else:
                return False, f"Delete failed: {response.text}"
                
        except Exception as e:
            return False, str(e)

    async def list_user_files(self, user_id):
        """List all files for a user"""
        try:
            path = f"user_files/{user_id}/"
            url = f"{self.base_url}/{path}"
            
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                files = response.json()
                return [f.get('name') for f in files if f.get('type') == 'file']
            else:
                return []
        except:
            return []

class ArchiveBot:
    def __init__(self):
        self.application = None
        self.github = GitHubManager(GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH)
        logger.info("✅ Bot initialized successfully!")

    async def delete_previous_messages(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Delete previous messages from user to keep chat clean"""
        user_id = update.effective_user.id
        
        if user_id in user_data and 'message_ids' in user_data[user_id]:
            for msg_id in user_data[user_id]['message_ids']:
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=msg_id
                    )
                except:
                    pass
            user_data[user_id]['message_ids'] = []

    async def save_message_id(self, update: Update, message):
        """Save message ID for later deletion"""
        user_id = update.effective_user.id
        
        if user_id not in user_data:
            user_data[user_id] = {}
        if 'message_ids' not in user_data[user_id]:
            user_data[user_id]['message_ids'] = []
            
        user_data[user_id]['message_ids'].append(message.message_id)

    async def check_force_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check if user has joined the force channel"""
        user_id = update.effective_user.id
        
        try:
            member = await context.bot.get_chat_member(
                chat_id=FORCE_CHANNEL_ID,
                user_id=user_id
            )
            
            if member.status in ['member', 'administrator', 'creator']:
                return True
            else:
                return False
                
        except Exception as e:
            return False

    async def send_or_edit_message(self, update, context, text, reply_markup=None, parse_mode='HTML'):
        """Safely send or edit a message"""
        try:
            if update.callback_query:
                msg = await update.callback_query.edit_message_text(
                    text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
            else:
                msg = await update.message.reply_text(
                    text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
            await self.save_message_id(update, msg)
            return msg
        except Exception as e:
            if "Message to edit not found" in str(e) or "message is not modified" in str(e):
                if update.callback_query:
                    msg = await update.callback_query.message.reply_text(
                        text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                else:
                    msg = await update.message.reply_text(
                        text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                await self.save_message_id(update, msg)
                return msg
            else:
                raise e

    async def force_join_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send force join message - blocks all access"""
        await self.delete_previous_messages(update, context)
        
        keyboard = [
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{FORCE_CHANNEL.replace('@', '')}")],
            [InlineKeyboardButton("🔄 Check Again", callback_data="check_join")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message = (
            f"🔒 <b>Access Denied</b>\n\n"
            f"You must join our channel to use this bot!\n\n"
            f"📢 <b>Channel:</b> {FORCE_CHANNEL}\n\n"
            f"<i>Click the button below to join, then click 'Check Again'</i>\n\n"
            f"⚠️ <b>Note:</b> You cannot access any bot features until you join."
        )
        
        await self.send_or_edit_message(update, context, message, reply_markup)

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command with force join check"""
        await self.delete_previous_messages(update, context)
        
        if not await self.check_force_join(update, context):
            await self.force_join_message(update, context)
            return
            
        user = update.effective_user
        
        keyboard = [
            [InlineKeyboardButton("📤 Upload Files", callback_data="add_more")],
            [InlineKeyboardButton("📋 My Files", callback_data="main_menu")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("📖 How to Use", callback_data="help")],
            [InlineKeyboardButton("ℹ️ About", callback_data="about")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        msg = await update.message.reply_text(
            f"🌟 Welcome {user.first_name}!\n\n"
            f"📤 <b>Upload files</b> or click <b>My Files</b> to manage\n\n"
            f"🔒 <b>GitHub Storage:</b> Files are stored securely\n"
            f"📁 <b>Custom Prefix:</b> Set your file naming format\n\n"
            f"All actions are available through the menu system.",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        await self.save_message_id(update, msg)

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        if data != "check_join":
            if not await self.check_force_join(update, context):
                await self.force_join_message(update, context)
                return
        
        if data == "check_join":
            await self.delete_previous_messages(update, context)
            
            if await self.check_force_join(update, context):
                user = update.effective_user
                keyboard = [
                    [InlineKeyboardButton("📤 Upload Files", callback_data="add_more")],
                    [InlineKeyboardButton("📋 My Files", callback_data="main_menu")],
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("📖 How to Use", callback_data="help")],
                    [InlineKeyboardButton("ℹ️ About", callback_data="about")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await self.send_or_edit_message(
                    update, context,
                    f"✅ <b>Success!</b> You've joined the channel!\n\n"
                    f"🌟 Welcome {user.first_name}!\n\n"
                    f"📤 <b>Upload files</b> or click <b>My Files</b> to manage",
                    reply_markup
                )
            else:
                await self.force_join_message(update, context)
            return
            
        elif data == "settings":
            await self.show_settings(update, context, user_id)
            
        elif data == "set_prefix":
            await self.handle_set_prefix(update, context, user_id)
            
        elif data == "remove_prefix":
            if user_id in user_data and 'file_prefix' in user_data[user_id]:
                del user_data[user_id]['file_prefix']
            await self.show_settings(update, context, user_id)
            
        elif data == "help":
            await self.delete_previous_messages(update, context)
            
            help_text = """
<b>📚 How to Use</b>

1️⃣ <b>Upload Files</b>
   • Click <b>Upload Files</b> or send files directly
   • Files are stored securely on GitHub

2️⃣ <b>Access Menu</b>
   • Click <b>My Files</b> to see uploaded files
   • All actions are available from there

3️⃣ <b>Available Actions:</b>
   • <b>📦 Extract</b> - Unpack archives (ZIP/RAR/7z)
   • <b>🗜️ Compress</b> - Create archive with levels
   • <b>✏️ Rename</b> - Rename any file
   • <b>🔒 Set Password</b> - Protect archives
   • <b>🖼️ Set Thumbnail</b> - Custom preview
   • <b>📋 Get File ID</b> - Get Telegram file ID
   • <b>⚙️ Settings</b> - Customize file naming

4️⃣ <b>File Naming:</b>
   • Set custom prefix for your files
   • Format: PREFIX + ORIGINAL_NAME.extension

<i>Max file size: 2GB per file</i>
<i>Files are deleted from GitHub after processing</i>
"""
            keyboard = [
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await self.send_or_edit_message(update, context, help_text, reply_markup)
            
        elif data == "about":
            await self.delete_previous_messages(update, context)
            
            about_text = """
<b>🤖 Advanced Archive Bot</b>

✨ <b>Features:</b>
• Upload & manage files
• GitHub storage integration
• Extract archives (ZIP/RAR/7z)
• Compress with levels
• Rename files (standalone)
• Custom file prefix
• Password protection
• Custom thumbnails
• Telegram File ID support
• Force join channel

⚡ <b>Version:</b> 4.0
📝 <b>All controls via menu</b>
🔒 <b>No commands needed</b>
"""
            keyboard = [
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await self.send_or_edit_message(update, context, about_text, reply_markup)
            
        elif data == "home":
            await self.delete_previous_messages(update, context)
            
            keyboard = [
                [InlineKeyboardButton("📤 Upload Files", callback_data="add_more")],
                [InlineKeyboardButton("📋 My Files", callback_data="main_menu")],
                [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                [InlineKeyboardButton("📖 How to Use", callback_data="help")],
                [InlineKeyboardButton("ℹ️ About", callback_data="about")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await self.send_or_edit_message(
                update, context,
                f"🏠 <b>Home</b>\n\n"
                f"📤 Upload files or manage existing ones.\n"
                f"🔒 Files are stored securely on GitHub.",
                reply_markup
            )
            
        elif data == "back_to_main":
            await self.show_main_options(update, context, user_id)
            
        elif data == "main_menu":
            await self.show_main_options(update, context, user_id)
            
        elif data == "done_upload":
            await self.show_main_options(update, context, user_id)
            
        elif data == "add_more":
            await self.delete_previous_messages(update, context)
            
            if user_id in user_data:
                user_data[user_id]['message_ids'] = []
                
            keyboard = [[InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.send_or_edit_message(
                update, context,
                f"📤 <b>Upload Files</b>\n\n"
                f"Send any files you want to process.\n"
                f"Files will be stored on GitHub.\n\n"
                f"After uploading, click <b>✅ Done</b> or <b>Back to Menu</b>.",
                reply_markup
            )
            
        elif data == "thumb_send":
            await self.delete_previous_messages(update, context)
            
            keyboard = [
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.send_or_edit_message(
                update, context,
                f"🖼️ <b>Upload Thumbnail Image</b>\n\n"
                f"Please send a photo to use as thumbnail.\n\n"
                f"📸 <b>Supported:</b> JPG, PNG, WEBP\n"
                f"📏 <b>Recommended:</b> 320x320 pixels\n\n"
                f"<i>Send the photo now, or click Back to cancel.</i>",
                reply_markup
            )
            
            if user_id not in user_data:
                user_data[user_id] = {}
            user_data[user_id]['awaiting_thumb'] = True
            return
            
        elif data == "thumb_remove":
            if user_id in user_data and 'thumb' in user_data[user_id]:
                if os.path.exists(user_data[user_id]['thumb']):
                    os.remove(user_data[user_id]['thumb'])
                del user_data[user_id]['thumb']
                await self.show_main_options(update, context, user_id)
            else:
                keyboard = [
                    [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
                    [InlineKeyboardButton("🏠 Home", callback_data="home")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await self.send_or_edit_message(
                    update, context,
                    "❌ No thumbnail set to remove.",
                    reply_markup
                )
            return
            
        elif data == "set_thumb":
            await self.delete_previous_messages(update, context)
            
            keyboard = [
                [InlineKeyboardButton("📸 Send Image", callback_data="thumb_send")],
                [InlineKeyboardButton("🗑️ Remove Thumbnail", callback_data="thumb_remove")],
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await self.send_or_edit_message(
                update, context,
                f"🖼️ <b>Custom Thumbnail</b>\n\n"
                f"Set a custom thumbnail for your files.\n"
                f"Click 'Send Image' and upload a photo.",
                reply_markup
            )
            
        elif data in ["extract", "compress", "add_password", "rename", "get_fileid", "clear_files", "cancel"]:
            if user_id not in user_data:
                await self.send_or_edit_message(update, context, "❌ Session expired. Please upload files again.")
                return
                
            if data == "extract":
                await self.extract_archives(update, context, user_id)
            elif data == "compress":
                await self.show_compress_options(update, context, user_id)
            elif data == "add_password":
                await self.handle_password(update, context, user_id)
            elif data == "rename":
                await self.handle_rename(update, context, user_id)
            elif data == "get_fileid":
                await self.show_fileid(update, context, user_id)
            elif data == "clear_files":
                await self.clear_files(update, context, user_id)
            elif data == "cancel":
                await self.delete_previous_messages(update, context)
                if user_id in user_data:
                    del user_data[user_id]
                await self.send_or_edit_message(update, context, "❌ Operation cancelled.")
                    
        elif data.startswith("compress_"):
            format_type = data.replace("compress_", "")
            await self.show_compression_level(update, context, user_id, format_type)
            
        elif data.startswith("level_"):
            parts = data.split("_")
            format_type = parts[1]
            level = parts[2]
            await self.start_compression(update, context, user_id, format_type, level)
            
        elif data.startswith("password_"):
            action = data.replace("password_", "")
            if action == "set":
                await self.delete_previous_messages(update, context)
                await self.send_or_edit_message(
                    update, context,
                    "🔑 <b>Enter your password</b>\n\n"
                    "Type your password in the chat below.\n"
                    "<i>Send the password message now.</i>\n\n"
                    "After sending, click the button below to continue.",
                    None
                )
                if user_id not in user_data:
                    user_data[user_id] = {}
                user_data[user_id]['awaiting_password'] = True
            elif action == "skip":
                if user_id in user_data and 'password' in user_data[user_id]:
                    del user_data[user_id]['password']
                await self.delete_previous_messages(update, context)
                await self.show_compress_options(update, context, user_id)

    async def handle_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle file upload - adds to user's file list"""
        if not await self.check_force_join(update, context):
            await self.force_join_message(update, context)
            return
            
        user_id = update.effective_user.id
        file = update.message.document
        
        if not file:
            await update.message.reply_text("❌ Please send a file.")
            return
            
        if file.file_size > MAX_FILE_SIZE:
            await self.delete_previous_messages(update, context)
            msg = await update.message.reply_text(
                f"❌ <b>File Too Large!</b>\n\n"
                f"📄 File: {file.file_name}\n"
                f"📦 Size: {file.file_size / (1024 * 1024 * 1024):.2f} GB\n"
                f"⚠️ Max allowed: 2 GB\n\n"
                f"<i>Please send a smaller file.</i>",
                parse_mode='HTML'
            )
            await self.save_message_id(update, msg)
            return
        
        if user_id not in user_data:
            user_data[user_id] = {
                'files': [],
                'file_names': [],
                'file_ids': [],
                'file_sizes': [],
                'message_ids': []
            }
        elif 'message_ids' not in user_data[user_id]:
            user_data[user_id]['message_ids'] = []
        elif 'files' not in user_data[user_id]:
            user_data[user_id]['files'] = []
            user_data[user_id]['file_names'] = []
            user_data[user_id]['file_ids'] = []
            user_data[user_id]['file_sizes'] = []
        
        user_data[user_id]['files'].append(file)
        user_data[user_id]['file_names'].append(file.file_name)
        user_data[user_id]['file_ids'].append(file.file_id)
        user_data[user_id]['file_sizes'].append(file.file_size)
        
        # Upload to GitHub
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file.file_name}")
        file_obj = await file.get_file()
        await file_obj.download_to_drive(temp_path)
        
        success, result = await self.github.upload_file(temp_path, file.file_name, user_id)
        os.remove(temp_path)
        
        if success:
            user_data[user_id]['github_files'] = user_data[user_id].get('github_files', [])
            user_data[user_id]['github_files'].append(file.file_name)
        
        await self.delete_previous_messages(update, context)
        
        keyboard = [
            [InlineKeyboardButton("📋 Go to Menu", callback_data="done_upload")],
            [InlineKeyboardButton("➕ Upload More", callback_data="add_more")],
            [InlineKeyboardButton("🗑️ Clear All", callback_data="clear_files")],
            [InlineKeyboardButton("🏠 Home", callback_data="home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        file_count = len(user_data[user_id]['files'])
        total_size = sum(user_data[user_id]['file_sizes']) / (1024 * 1024)
        
        status = "✅ Uploaded to GitHub" if success else "❌ Upload failed"
        
        msg = await update.message.reply_text(
            f"✅ <b>File uploaded!</b>\n\n"
            f"📄 <b>Files:</b> {file_count}\n"
            f"📦 <b>Total Size:</b> {total_size:.2f} MB\n"
            f"📝 <b>Last file:</b> {file.file_name}\n"
            f"🔒 <b>GitHub:</b> {status}\n\n"
            f"Click <b>📋 Go to Menu</b> to access all features.",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
        await self.save_message_id(update, msg)

    # ... (rest of the methods - show_main_options, show_compress_options, etc.)
    # I'll continue with the full code in the next message due to character limit

    def run(self):
        """Run the bot"""
        try:
            self.application = Application.builder().token(BOT_TOKEN).build()
            
            self.application.add_handler(CommandHandler("start", self.start))
            self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_file))
            self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_input))
            self.application.add_handler(CallbackQueryHandler(self.button_handler))
            
            logger.info("🤖 Bot is running successfully!")
            self.application.run_polling(allowed_updates=Update.ALL_TYPES)
        except Exception as e:
            logger.error(f"❌ Failed to start bot: {e}")
            sys.exit(1)

if __name__ == "__main__":
    bot = ArchiveBot()
    bot.run()