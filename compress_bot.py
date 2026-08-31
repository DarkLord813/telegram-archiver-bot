import os
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
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration - All from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 2 * 1024 * 1024 * 1024))  # Default 2GB
TEMP_DIR = os.getenv("TEMP_DIR", "temp_downloads")

# GitHub Configuration - Separate Owner and Repo
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    raise ValueError("GITHUB_TOKEN environment variable is required!")

GITHUB_OWNER = os.getenv("GITHUB_OWNER")
if not GITHUB_OWNER:
    raise ValueError("GITHUB_OWNER environment variable is required!")

GITHUB_REPO = os.getenv("GITHUB_REPO")
if not GITHUB_REPO:
    raise ValueError("GITHUB_REPO environment variable is required!")

GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

# Combine owner and repo for API calls
GITHUB_REPO_FULL = f"{GITHUB_OWNER}/{GITHUB_REPO}"

# Force Join Configuration
FORCE_CHANNEL = os.getenv("FORCE_CHANNEL", "@NCK_Dev")
FORCE_CHANNEL_ID = int(os.getenv("FORCE_CHANNEL_ID", "-1002583286874"))

# Create temp directory
os.makedirs(TEMP_DIR, exist_ok=True)

# User sessions storage
user_data = {}

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
            # Read the file
            with open(file_path, 'rb') as f:
                content = f.read()
            
            # Encode to base64
            encoded_content = base64.b64encode(content).decode('utf-8')
            
            # Create path with user_id prefix
            path = f"user_files/{user_id}/{file_name}"
            
            # Check if file exists
            url = f"{self.base_url}/{path}"
            sha = None
            
            try:
                response = requests.get(url, headers=self.headers)
                if response.status_code == 200:
                    sha = response.json().get('sha')
            except:
                pass
            
            # Prepare data for upload
            data = {
                "message": f"Upload {file_name} by user {user_id}",
                "content": encoded_content,
                "branch": self.branch
            }
            if sha:
                data["sha"] = sha
            
            # Upload to GitHub
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
            
            # Get file SHA
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                return False, "File not found"
            
            sha = response.json().get('sha')
            
            # Delete the file
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
            
        # Check file size
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
        
        # Initialize user session
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
        
        # Store file info locally
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
        
        # Delete previous messages
        await self.delete_previous_messages(update, context)
        
        # Show upload status with menu access
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

    async def show_main_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
        """Show main menu with all files and actions"""
        await self.delete_previous_messages(update, context)
        
        file_data = user_data.get(user_id, {})
        files = file_data.get('files', [])
        file_names = file_data.get('file_names', [])
        
        if not files:
            keyboard = [
                [InlineKeyboardButton("📤 Upload Files", callback_data="add_more")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await self.send_or_edit_message(
                update, context,
                f"📋 <b>My Files</b>\n\n"
                f"❌ No files uploaded yet.\n"
                f"Click <b>Upload Files</b> to get started.",
                reply_markup
            )
            return
            
        file_count = len(files)
        total_size = sum(file_data.get('file_sizes', [0])) / (1024 * 1024)
        has_password = 'password' in file_data
        has_thumb = 'thumb' in file_data
        has_rename = 'rename' in file_data
        
        # Show settings status
        settings_text = ""
        if has_password:
            settings_text += "🔒 Password: ✅ Set\n"
        else:
            settings_text += "🔒 Password: ❌ Not set\n"
            
        if has_thumb:
            settings_text += "🖼️ Thumbnail: ✅ Set\n"
        else:
            settings_text += "🖼️ Thumbnail: ❌ Not set\n"
            
        if has_rename:
            settings_text += f"✏️ Rename: {file_data['rename']}\n"
        else:
            settings_text += "✏️ Rename: ❌ Not set\n"
        
        # Main menu keyboard - ALL actions accessible
        keyboard = [
            [
                InlineKeyboardButton("📦 Extract", callback_data="extract"),
                InlineKeyboardButton("🗜️ Compress", callback_data="compress")
            ],
            [
                InlineKeyboardButton("✏️ Rename", callback_data="rename"),
                InlineKeyboardButton("🔒 Set Password", callback_data="add_password")
            ],
            [
                InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="set_thumb"),
                InlineKeyboardButton("📋 Get File ID", callback_data="get_fileid")
            ],
            [
                InlineKeyboardButton("➕ Upload More", callback_data="add_more"),
                InlineKeyboardButton("🗑️ Clear All", callback_data="clear_files")
            ],
            [
                InlineKeyboardButton("🏠 Home", callback_data="home"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        file_list = "\n".join([f"• {name}" for name in file_names[:5]])
        if len(file_names) > 5:
            file_list += f"\n• ... and {len(file_names) - 5} more"
        
        await self.send_or_edit_message(
            update, context,
            f"📋 <b>My Files</b>\n\n"
            f"📄 <b>Files:</b> {file_count}\n"
            f"📦 <b>Total Size:</b> {total_size:.2f} MB\n"
            f"📝 <b>Files:</b>\n{file_list}\n\n"
            f"⚙️ <b>Settings:</b>\n{settings_text}\n\n"
            f"🔧 <b>Choose an action:</b>",
            reply_markup
        )

    async def show_compress_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
        """Show compression format options - Step 1"""
        await self.delete_previous_messages(update, context)
        
        keyboard = [
            [
                InlineKeyboardButton("📦 ZIP", callback_data="compress_zip"),
                InlineKeyboardButton("📦 7Z", callback_data="compress_7z")
            ],
            [
                InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main"),
                InlineKeyboardButton("🏠 Home", callback_data="home")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.send_or_edit_message(
            update, context,
            f"🗜️ <b>Step 1: Choose Compression Format</b>\n\n"
            f"Select the archive format you want to use.\n\n"
            f"<i>After selecting, you'll choose compression level.</i>",
            reply_markup
        )

    async def show_compression_level(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id, format_type):
        """Show compression level options - Step 2"""
        await self.delete_previous_messages(update, context)
        
        keyboard = [
            [
                InlineKeyboardButton("⚡ Fast", callback_data=f"level_{format_type}_fast"),
                InlineKeyboardButton("📊 Normal", callback_data=f"level_{format_type}_normal")
            ],
            [
                InlineKeyboardButton("🔄 High", callback_data=f"level_{format_type}_high"),
                InlineKeyboardButton("💾 Ultra", callback_data=f"level_{format_type}_ultra")
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="compress"),
                InlineKeyboardButton("🏠 Home", callback_data="home")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.send_or_edit_message(
            update, context,
            f"🗜️ <b>Step 2: Choose Compression Level</b>\n\n"
            f"📦 Format: {format_type.upper()}\n\n"
            f"Choose compression level:\n"
            f"• ⚡ Fast - Quick but larger files\n"
            f"• 📊 Normal - Balanced\n"
            f"• 🔄 High - Smaller files, slower\n"
            f"• 💾 Ultra - Smallest files, slowest",
            reply_markup
        )

    async def handle_password(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
        """Handle password options - Step 1 for password"""
        await self.delete_previous_messages(update, context)
        
        keyboard = [
            [InlineKeyboardButton("🔑 Set Password", callback_data="password_set")],
            [InlineKeyboardButton("⏭️ Skip Password", callback_data="password_skip")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
            [InlineKeyboardButton("🏠 Home", callback_data="home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.send_or_edit_message(
            update, context,
            f"🔒 <b>Step 1: Password Protection</b>\n\n"
            f"Do you want to add a password to your archive?\n\n"
            f"<i>If you select 'Set Password', you'll be prompted to enter it.</i>",
            reply_markup
        )

    async def handle_rename(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
        """Handle rename - Step 1"""
        await self.delete_previous_messages(update, context)
        
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
            [InlineKeyboardButton("🏠 Home", callback_data="home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.send_or_edit_message(
            update, context,
            f"✏️ <b>Step 1: Rename File</b>\n\n"
            f"Type the new filename in the chat below.\n"
            f"Example: <code>my_new_file.txt</code>\n\n"
            f"<i>This will rename the output file when extracted or compressed.</i>",
            reply_markup
        )
        
        if user_id not in user_data:
            user_data[user_id] = {}
        user_data[user_id]['awaiting_rename'] = True

    async def show_fileid(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
        """Show file ID information"""
        await self.delete_previous_messages(update, context)
        
        file_data = user_data.get(user_id, {})
        file_ids = file_data.get('file_ids', [])
        file_names = file_data.get('file_names', [])
        
        if not file_ids:
            keyboard = [
                [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await self.send_or_edit_message(
                update, context,
                "❌ No files to show.",
                reply_markup
            )
            return
            
        text = "📋 <b>Telegram File IDs</b>\n\n"
        for name, fid in zip(file_names[:5], file_ids[:5]):
            text += f"📄 <b>{name}</b>\n<code>{fid}</code>\n\n"
            
        if len(file_names) > 5:
            text += f"• ... and {len(file_names) - 5} more"
            
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
            [InlineKeyboardButton("🏠 Home", callback_data="home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.send_or_edit_message(update, context, text, reply_markup)

    async def show_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
        """Show settings menu"""
        await self.delete_previous_messages(update, context)
        
        file_data = user_data.get(user_id, {})
        current_prefix = file_data.get('file_prefix', 'Not set')
        
        keyboard = [
            [InlineKeyboardButton("📝 Set File Prefix", callback_data="set_prefix")],
            [InlineKeyboardButton("🗑️ Remove Prefix", callback_data="remove_prefix")],
            [InlineKeyboardButton("🔙 Back to Menu", callback_data="back_to_main")],
            [InlineKeyboardButton("🏠 Home", callback_data="home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.send_or_edit_message(
            update, context,
            f"⚙️ <b>Settings</b>\n\n"
            f"📝 <b>Current File Prefix:</b> {current_prefix}\n\n"
            f"<i>File naming format: PREFIX + ORIGINAL_NAME.extension</i>\n\n"
            f"Set a custom prefix for your files.",
            reply_markup
        )

    async def handle_set_prefix(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
        """Handle setting file prefix"""
        await self.delete_previous_messages(update, context)
        
        keyboard = [
            [InlineKeyboardButton("🔙 Back to Settings", callback_data="settings")],
            [InlineKeyboardButton("🏠 Home", callback_data="home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.send_or_edit_message(
            update, context,
            f"📝 <b>Set File Prefix</b>\n\n"
            f"Type your desired prefix in the chat below.\n"
            f"Example: <code>MY_FILE_</code>\n\n"
            f"<i>Files will be named as: PREFIX + ORIGINAL_NAME.extension</i>",
            reply_markup
        )
        
        if user_id not in user_data:
            user_data[user_id] = {}
        user_data[user_id]['awaiting_prefix'] = True

    async def clear_files(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
        """Clear all uploaded files from local storage and GitHub"""
        await self.delete_previous_messages(update, context)
        
        # Delete from GitHub
        file_data = user_data.get(user_id, {})
        github_files = file_data.get('github_files', [])
        
        for file_name in github_files:
            await self.github.delete_file(file_name, user_id)
        
        if user_id in user_data:
            del user_data[user_id]
            
        keyboard = [
            [InlineKeyboardButton("📤 Upload Files", callback_data="add_more")],
            [InlineKeyboardButton("🏠 Home", callback_data="home")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self.send_or_edit_message(
            update, context,
            "🗑️ All files cleared from GitHub and local storage. Upload new files to get started.",
            reply_markup
        )

    async def start_compression(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id, format_type, level):
        """Start compression process with proper workflow"""
        query = update.callback_query
        file_data = user_data.get(user_id, {})
        files = file_data.get('files', [])
        file_names = file_data.get('file_names', [])
        password = file_data.get('password', None)
        prefix = file_data.get('file_prefix', '')
        
        if not files:
            await query.edit_message_text("❌ No files to compress.")
            return
            
        # Show progress
        await self.delete_previous_messages(update, context)
        msg = await query.message.reply_text(
            f"🗜️ Starting compression...\n\n{ProgressBar.circular(0)}"
        )
        await self.save_message_id(update, msg)
        
        try:
            await query.message.delete()
        except:
            pass
        
        # Create archive
        timestamp = int(time.time())
        archive_name = f"compressed_{timestamp}.{format_type}"
        
        # Apply prefix if set
        if prefix:
            archive_name = f"{prefix}{archive_name}"
        
        archive_path = os.path.join(TEMP_DIR, f"{user_id}_{archive_name}")
        
        try:
            # Download files from GitHub
            downloaded_files = []
            total_files = len(files)
            
            for i, (file, name) in enumerate(zip(files, file_names)):
                file_path = os.path.join(TEMP_DIR, f"{user_id}_{i}_{name}")
                
                # Download from GitHub
                github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/user_files/{user_id}/{name}"
                response = requests.get(github_url)
                
                if response.status_code == 200:
                    with open(file_path, 'wb') as f:
                        f.write(response.content)
                else:
                    # Fallback: download from Telegram
                    file_obj = await file.get_file()
                    await file_obj.download_to_drive(file_path)
                
                progress = ((i + 1) / total_files) * 40
                await context.bot.edit_message_text(
                    f"🗜️ Downloading files...\nFile {i+1}/{total_files}: {name}\n\n{ProgressBar.circular(progress)}",
                    chat_id=msg.chat_id,
                    message_id=msg.message_id
                )
                await asyncio.sleep(0.1)
                downloaded_files.append((file_path, name))
            
            await context.bot.edit_message_text(
                f"🗜️ Creating {format_type.upper()} archive...\n\n{ProgressBar.circular(40)}",
                chat_id=msg.chat_id,
                message_id=msg.message_id
            )
            
            # Create archive
            if format_type == 'zip':
                with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    if password:
                        zipf.setpassword(password.encode())
                    total_files = len(downloaded_files)
                    for idx, (file_path, name) in enumerate(downloaded_files):
                        arcname = os.path.basename(name)
                        if prefix:
                            arcname = f"{prefix}{arcname}"
                        zipf.write(file_path, arcname=arcname)
                        
                        progress = 40 + ((idx + 1) / total_files) * 50
                        await context.bot.edit_message_text(
                            f"🗜️ Compressing... {idx+1}/{total_files}\n📄 {name}\n\n{ProgressBar.circular(progress)}",
                            chat_id=msg.chat_id,
                            message_id=msg.message_id
                        )
                        await asyncio.sleep(0.05)
                        
            elif format_type == '7z':
                with py7zr.SevenZipFile(archive_path, 'w', password=password) as szf:
                    total_files = len(downloaded_files)
                    for idx, (file_path, name) in enumerate(downloaded_files):
                        arcname = os.path.basename(name)
                        if prefix:
                            arcname = f"{prefix}{arcname}"
                        szf.write(file_path, arcname)
                        progress = 40 + ((idx + 1) / total_files) * 50
                        await context.bot.edit_message_text(
                            f"🗜️ Compressing... {idx+1}/{total_files}\n📄 {name}\n\n{ProgressBar.circular(progress)}",
                            chat_id=msg.chat_id,
                            message_id=msg.message_id
                        )
                        await asyncio.sleep(0.05)
                        
            # Complete
            archive_size = os.path.getsize(archive_path)
            
            await context.bot.edit_message_text(
                f"✅ Compression complete!\n\n{ProgressBar.circular(100)}",
                chat_id=msg.chat_id,
                message_id=msg.message_id
            )
            
            # Upload to GitHub
            await context.bot.edit_message_text(
                f"📤 Uploading to GitHub...\n\n{ProgressBar.circular(95)}",
                chat_id=msg.chat_id,
                message_id=msg.message_id
            )
            
            success, result = await self.github.upload_file(archive_path, archive_name, user_id)
            
            if success:
                await context.bot.edit_message_text(
                    f"✅ Uploaded to GitHub!\n\n{ProgressBar.circular(100)}",
                    chat_id=msg.chat_id,
                    message_id=msg.message_id
                )
                
                # Send the file to user from GitHub
                github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/user_files/{user_id}/{archive_name}"
                response = requests.get(github_url)
                
                if response.status_code == 200:
                    with open(archive_path, 'wb') as f:
                        f.write(response.content)
                    
                    thumb = file_data.get('thumb', None)
                    with open(archive_path, 'rb') as doc:
                        await context.bot.send_document(
                            chat_id=msg.chat_id,
                            document=doc,
                            filename=archive_name,
                            thumbnail=thumb if thumb and os.path.exists(thumb) else None
                        )
                    
                    # Delete from GitHub after sending
                    await self.github.delete_file(archive_name, user_id)
                    
                    await context.bot.send_message(
                        msg.chat_id,
                        f"✅ <b>Archive created and sent successfully!</b>\n\n"
                        f"📦 Format: {format_type.upper()}\n"
                        f"🔒 Password: {'Yes' if password else 'No'}\n"
                        f"📊 Level: {level.capitalize()}\n"
                        f"📄 Files: {len(files)}\n"
                        f"📦 Size: {archive_size / (1024 * 1024):.2f} MB\n"
                        f"📁 Prefix: {prefix if prefix else 'None'}",
                        parse_mode='HTML'
                    )
                else:
                    await context.bot.send_message(
                        msg.chat_id,
                        f"❌ Failed to download from GitHub: {response.status_code}"
                    )
            else:
                await context.bot.send_message(
                    msg.chat_id,
                    f"❌ Failed to upload to GitHub: {result}"
                )
            
            # Show menu after completion
            keyboard = [
                [InlineKeyboardButton("📋 My Files", callback_data="main_menu")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                msg.chat_id,
                "📤 Return to menu:",
                reply_markup=reply_markup
            )
            
        except Exception as e:
            error_msg = str(e)
            await context.bot.send_message(
                msg.chat_id,
                f"❌ Error during compression:\n{error_msg}",
                parse_mode='HTML'
            )
            
        finally:
            # Cleanup local files
            for file_path, _ in downloaded_files:
                if os.path.exists(file_path):
                    os.remove(file_path)
            if os.path.exists(archive_path):
                os.remove(archive_path)

    async def extract_archives(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_id):
        """Extract archives with continuous progress"""
        if not await self.check_force_join(update, context):
            await self.force_join_message(update, context)
            return
            
        query = update.callback_query
        file_data = user_data.get(user_id, {})
        files = file_data.get('files', [])
        file_names = file_data.get('file_names', [])
        prefix = file_data.get('file_prefix', '')
        
        if not files:
            await query.edit_message_text("❌ No files to extract.")
            return
            
        await self.delete_previous_messages(update, context)
        msg = await query.message.reply_text("📦 Starting extraction...\n\n" + ProgressBar.circular(0))
        await self.save_message_id(update, msg)
        
        try:
            await query.message.delete()
        except:
            pass
        
        extracted_files = []
        total_files = len(files)
        extracted_count = 0
        
        for file_idx, (file, file_name) in enumerate(zip(files, file_names)):
            ext = os.path.splitext(file_name)[1].lower()
            
            if ext not in ['.zip', '.rar', '.7z']:
                continue
                
            try:
                # Download from GitHub
                github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/user_files/{user_id}/{file_name}"
                response = requests.get(github_url)
                
                if response.status_code == 200:
                    file_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
                    with open(file_path, 'wb') as f:
                        f.write(response.content)
                else:
                    # Fallback: download from Telegram
                    file_obj = await file.get_file()
                    file_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
                    await file_obj.download_to_drive(file_path)
                
                progress = ((file_idx + 1) / total_files) * 50
                await context.bot.edit_message_text(
                    f"📦 Extracting: {file_name}\n\n{ProgressBar.circular(progress)}",
                    chat_id=msg.chat_id,
                    message_id=msg.message_id
                )
                
                extract_dir = os.path.join(TEMP_DIR, f"{user_id}_extracted")
                os.makedirs(extract_dir, exist_ok=True)
                
                password = file_data.get('password', None)
                
                if ext == '.zip':
                    with zipfile.ZipFile(file_path, 'r') as zip_ref:
                        if password:
                            zip_ref.setpassword(password.encode())
                        zip_ref.extractall(extract_dir)
                elif ext == '.rar':
                    with rarfile.RarFile(file_path) as rar_ref:
                        if password:
                            rar_ref.setpassword(password)
                        rar_ref.extractall(extract_dir)
                elif ext == '.7z':
                    with py7zr.SevenZipFile(file_path, mode='r', password=password) as sz_ref:
                        sz_ref.extractall(extract_dir)
                        
                # Add extracted files to list with prefix
                for root, dirs, files_in_dir in os.walk(extract_dir):
                    for f in files_in_dir:
                        f_path = os.path.join(root, f)
                        extracted_files.append(f_path)
                        extracted_count += 1
                        
                progress = 50 + ((file_idx + 1) / total_files) * 50
                await context.bot.edit_message_text(
                    f"📦 Extracted: {file_name}\n✅ Done\n\n{ProgressBar.circular(progress)}",
                    chat_id=msg.chat_id,
                    message_id=msg.message_id
                )
                        
            except Exception as e:
                await context.bot.send_message(query.message.chat_id, f"❌ Error extracting {file_name}: {str(e)}")
                
        if extracted_files:
            await context.bot.edit_message_text(
                f"✅ Extracted {extracted_count} files!\n\n{ProgressBar.circular(100)}",
                chat_id=msg.chat_id,
                message_id=msg.message_id
            )
            
            await context.bot.send_message(query.message.chat_id, "📤 Sending extracted files...")
            
            thumb = file_data.get('thumb', None)
            rename = file_data.get('rename', None)
            
            for file_path in extracted_files:
                file_size = os.path.getsize(file_path)
                if file_size > MAX_FILE_SIZE:
                    await context.bot.send_message(
                        query.message.chat_id,
                        f"⚠️ {os.path.basename(file_path)} exceeds 2GB, skipping."
                    )
                    continue
                    
                file_name = os.path.basename(file_path)
                
                # Apply prefix if set
                if prefix:
                    file_name = f"{prefix}{file_name}"
                if rename:
                    file_name = rename
                
                # Upload to GitHub
                success, result = await self.github.upload_file(file_path, file_name, user_id)
                
                if success:
                    # Download from GitHub and send
                    github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/user_files/{user_id}/{file_name}"
                    response = requests.get(github_url)
                    
                    if response.status_code == 200:
                        with open(file_path, 'wb') as f:
                            f.write(response.content)
                        
                        with open(file_path, 'rb') as doc:
                            await context.bot.send_document(
                                chat_id=query.message.chat_id,
                                document=doc,
                                filename=file_name,
                                thumbnail=thumb if thumb and os.path.exists(thumb) else None
                            )
                        
                        # Delete from GitHub after sending
                        await self.github.delete_file(file_name, user_id)
                    
            # Show menu after completion
            keyboard = [
                [InlineKeyboardButton("📋 My Files", callback_data="main_menu")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await context.bot.send_message(
                query.message.chat_id,
                "📤 Return to menu:",
                reply_markup=reply_markup
            )
                    
        shutil.rmtree(os.path.join(TEMP_DIR, f"{user_id}_extracted"), ignore_errors=True)

    async def handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text input for password, rename, and prefix"""
        if not await self.check_force_join(update, context):
            await self.force_join_message(update, context)
            return
            
        user_id = update.effective_user.id
        text = update.message.text
        
        if user_id not in user_data:
            user_data[user_id] = {}
            
        # Handle password input
        if user_data[user_id].get('awaiting_password'):
            user_data[user_id]['password'] = text
            user_data[user_id]['awaiting_password'] = False
            
            await self.delete_previous_messages(update, context)
            msg = await update.message.reply_text("✅ Password set successfully!\n\nClick the button below to continue.")
            await self.save_message_id(update, msg)
            
            keyboard = [
                [InlineKeyboardButton("🗜️ Continue to Compression", callback_data="compress")],
                [InlineKeyboardButton("📋 My Files", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            msg2 = await update.message.reply_text(
                "📤 Continue:",
                reply_markup=reply_markup
            )
            await self.save_message_id(update, msg2)
            return
            
        # Handle rename input
        if user_data[user_id].get('awaiting_rename'):
            user_data[user_id]['rename'] = text
            user_data[user_id]['awaiting_rename'] = False
            
            await self.delete_previous_messages(update, context)
            msg = await update.message.reply_text(f"✅ File will be renamed to: {text}")
            await self.save_message_id(update, msg)
            
            keyboard = [
                [InlineKeyboardButton("📋 My Files", callback_data="main_menu")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            msg2 = await update.message.reply_text(
                "📤 Return to menu:",
                reply_markup=reply_markup
            )
            await self.save_message_id(update, msg2)
            return
            
        # Handle prefix input
        if user_data[user_id].get('awaiting_prefix'):
            user_data[user_id]['file_prefix'] = text
            user_data[user_id]['awaiting_prefix'] = False
            
            await self.delete_previous_messages(update, context)
            msg = await update.message.reply_text(f"✅ File prefix set to: {text}")
            await self.save_message_id(update, msg)
            
            keyboard = [
                [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                [InlineKeyboardButton("📋 My Files", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            msg2 = await update.message.reply_text(
                "📤 Return to settings:",
                reply_markup=reply_markup
            )
            await self.save_message_id(update, msg2)
            return
            
        # If none of the above, treat as new file
        await self.handle_file(update, context)

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle photo upload for thumbnail"""
        if not await self.check_force_join(update, context):
            await self.force_join_message(update, context)
            return
            
        user_id = update.effective_user.id
        photo = update.message.photo[-1]
        
        if user_id not in user_data:
            user_data[user_id] = {}
            
        if not user_data[user_id].get('awaiting_thumb'):
            await update.message.reply_text(
                "❌ You're not in thumbnail upload mode.\n"
                "Click 🖼️ Set Thumbnail → 📸 Send Image first."
            )
            return
        
        try:
            file_obj = await photo.get_file()
            thumb_path = os.path.join(TEMP_DIR, f"{user_id}_thumb.jpg")
            await file_obj.download_to_drive(thumb_path)
            
            user_data[user_id]['thumb'] = thumb_path
            user_data[user_id]['awaiting_thumb'] = False
            
            await self.delete_previous_messages(update, context)
            msg = await update.message.reply_text(
                f"✅ <b>Thumbnail set successfully!</b>\n\n"
                f"🆔 File ID: <code>{photo.file_id[:30]}...</code>\n\n"
                f"<i>This thumbnail will be used for all your extracted/compressed files.</i>",
                parse_mode='HTML'
            )
            await self.save_message_id(update, msg)
            
            keyboard = [
                [InlineKeyboardButton("📋 My Files", callback_data="main_menu")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            msg2 = await update.message.reply_text(
                "📤 Return to menu:",
                reply_markup=reply_markup
            )
            await self.save_message_id(update, msg2)
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error saving thumbnail: {str(e)}")

    def run(self):
        """Run the bot"""
        self.application = Application.builder().token(BOT_TOKEN).build()
        
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(MessageHandler(filters.Document.ALL, self.handle_file))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_input))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        
        print("🤖 Bot is running with GitHub storage integration...")
        print(f"📢 Force Channel: {FORCE_CHANNEL}")
        print(f"📁 GitHub: {GITHUB_OWNER}/{GITHUB_REPO}")
        print(f"🌿 Branch: {GITHUB_BRANCH}")
        print("✅ Features: Upload, Extract, Compress, Rename, Password, Thumbnail, File ID")
        print("🔒 Files are stored on GitHub and deleted after processing")
        print("📋 All actions accessible from main & sub menus")
        print("🔄 Continuous progress bars")
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    bot = ArchiveBot()
    bot.run()