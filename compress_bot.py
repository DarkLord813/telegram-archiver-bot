#!/usr/bin/env python3
# ============================================
# TELEGRAM ARCHIVE BOT - DIRECT GITHUB UPLOAD
# Compatible with python-telegram-bot 13.7
# ============================================

import os
import sys
import sqlite3
import secrets
import logging
import shutil
import zipfile
import rarfile
import py7zr
import time
import base64
import requests
import urllib.request
from datetime import datetime
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
from dotenv import load_dotenv

load_dotenv()

# ============================================
# CONFIG
# ============================================
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    print('❌ BOT_TOKEN is not set')
    sys.exit(1)

GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
GITHUB_OWNER = os.getenv('GITHUB_OWNER')
GITHUB_REPO = os.getenv('GITHUB_REPO')
GITHUB_BRANCH = os.getenv('GITHUB_BRANCH', 'main')

if not GITHUB_TOKEN or not GITHUB_OWNER or not GITHUB_REPO:
    print('❌ GitHub credentials not set')
    sys.exit(1)

FORCE_CHANNEL = os.getenv('FORCE_CHANNEL', '@NCK_Dev')
FORCE_CHANNEL_ID = int(os.getenv('FORCE_CHANNEL_ID', '-1002583286874'))

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
TEMP_DIR = os.getenv('TEMP_DIR', 'temp_downloads')
DB_PATH = os.getenv('DB_PATH', './data/bot_database.db')

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============================================
# DATABASE CLASS
# ============================================
class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = None
        self.init_db()

    def init_db(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                file_prefix TEXT DEFAULT '',
                archive_password TEXT DEFAULT '',
                thumbnail_path TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id TEXT PRIMARY KEY,
                user_id INTEGER,
                name TEXT,
                size INTEGER,
                file_id TEXT,
                github_path TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER DEFAULT 1
            )
        ''')

        self.conn.commit()
        logger.info('✅ Database initialized')

    def get_user(self, user_id: int) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_user(self, user_id: int, username: str, first_name: str):
        cursor = self.conn.cursor()
        cursor.execute(
            '''INSERT OR IGNORE INTO users (user_id, username, first_name) 
               VALUES (?, ?, ?)''',
            (user_id, username or '', first_name)
        )
        self.conn.commit()

    def update_file_prefix(self, user_id: int, prefix: str):
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE users SET file_prefix = ? WHERE user_id = ?',
            (prefix, user_id)
        )
        self.conn.commit()

    def get_file_prefix(self, user_id: int) -> str:
        cursor = self.conn.cursor()
        cursor.execute('SELECT file_prefix FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        return row['file_prefix'] if row else ''

    def update_archive_password(self, user_id: int, password: str):
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE users SET archive_password = ? WHERE user_id = ?',
            (password, user_id)
        )
        self.conn.commit()

    def get_archive_password(self, user_id: int) -> str:
        cursor = self.conn.cursor()
        cursor.execute('SELECT archive_password FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        return row['archive_password'] if row else ''

    def update_thumbnail(self, user_id: int, thumb_path: str):
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE users SET thumbnail_path = ? WHERE user_id = ?',
            (thumb_path, user_id)
        )
        self.conn.commit()

    def get_thumbnail(self, user_id: int) -> str:
        cursor = self.conn.cursor()
        cursor.execute('SELECT thumbnail_path FROM users WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        return row['thumbnail_path'] if row else ''

    def add_file(self, file_id: str, user_id: int, name: str, size: int, telegram_file_id: str, github_path: str):
        cursor = self.conn.cursor()
        cursor.execute(
            '''INSERT INTO files (id, user_id, name, size, file_id, github_path) 
               VALUES (?, ?, ?, ?, ?, ?)''',
            (file_id, user_id, name, size, telegram_file_id, github_path)
        )
        self.conn.commit()

    def get_user_files(self, user_id: int) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT * FROM files WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC',
            (user_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_file(self, file_id: str) -> Optional[Dict]:
        cursor = self.conn.cursor()
        cursor.execute(
            'SELECT * FROM files WHERE id = ? AND is_active = 1',
            (file_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def delete_file(self, file_id: str):
        cursor = self.conn.cursor()
        cursor.execute(
            'UPDATE files SET is_active = 0 WHERE id = ?',
            (file_id,)
        )
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()


# ============================================
# TELEGRAM TO GITHUB DIRECT UPLOADER
# ============================================
class TelegramToGitHubUploader:
    @staticmethod
    def upload_file_directly(bot_token: str, file_id: str, github_token: str, github_owner: str, 
                           github_repo: str, github_branch: str, file_name: str, user_id: int,
                           progress_callback=None) -> tuple:
        """Upload file directly from Telegram to GitHub without saving locally"""
        try:
            # Step 1: Get file info from Telegram
            telegram_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
            response = requests.get(telegram_url)
            response.raise_for_status()
            file_info = response.json()
            
            if not file_info.get('ok'):
                return False, f"Failed to get file info: {file_info}"
            
            file_path = file_info['result']['file_path']
            download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            
            if progress_callback:
                progress_callback(20, "Fetching file from Telegram...")
            
            # Step 2: Stream download from Telegram
            response = requests.get(download_url, stream=True)
            response.raise_for_status()
            
            # Step 3: Get total size for progress
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            # Step 4: Read content in chunks and encode for GitHub
            content_parts = []
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    content_parts.append(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress = 20 + (downloaded / total_size) * 60
                        progress_callback(progress, f"Downloading... {progress:.1f}%")
            
            content = b''.join(content_parts)
            encoded = base64.b64encode(content).decode('utf-8')
            
            if progress_callback:
                progress_callback(80, "Uploading to GitHub...")
            
            # Step 5: Upload to GitHub
            github_path = f"user_files/{user_id}/{file_name}"
            github_url = f"https://api.github.com/repos/{github_owner}/{github_repo}/contents/{github_path}"
            
            headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            # Check if file exists
            sha = None
            try:
                check_response = requests.get(github_url, headers=headers)
                if check_response.status_code == 200:
                    sha = check_response.json().get('sha')
            except:
                pass
            
            data = {
                "message": f"Upload {file_name} by user {user_id}",
                "content": encoded,
                "branch": github_branch
            }
            if sha:
                data["sha"] = sha
            
            upload_response = requests.put(github_url, headers=headers, json=data)
            
            if upload_response.status_code in [200, 201]:
                if progress_callback:
                    progress_callback(100, "Upload complete!")
                return True, f"https://raw.githubusercontent.com/{github_owner}/{github_repo}/{github_branch}/{github_path}"
            else:
                return False, f"GitHub upload failed: {upload_response.text}"
                
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False, str(e)


# ============================================
# PROGRESS BAR
# ============================================
class ProgressBar:
    @staticmethod
    def circular(percentage: float) -> str:
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
        
        circle = ''.join(filled_char if i < filled else empty_char for i in range(segments))
        return f"┌{'─' * segments}┐\n│{circle}│ {percentage:.1f}%\n└{'─' * segments}┘"


# ============================================
# BOT HANDLERS
# ============================================
class ArchiveBot:
    def __init__(self):
        self.db = Database()
        self.bot_username = ''
        self.bot_id = 0
        self.user_sessions = {}

    def format_size(self, bytes: int) -> str:
        if bytes < 1024:
            return f'{bytes} B'
        if bytes < 1048576:
            return f'{bytes / 1024:.1f} KB'
        if bytes < 1073741824:
            return f'{bytes / 1048576:.1f} MB'
        return f'{bytes / 1073741824:.2f} GB'

    def check_force_join(self, context: CallbackContext, user_id: int) -> bool:
        try:
            member = context.bot.get_chat_member(FORCE_CHANNEL_ID, user_id)
            return member.status in ['member', 'administrator', 'creator']
        except:
            return False

    def get_force_join_keyboard(self):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{FORCE_CHANNEL.replace('@', '')}")],
            [InlineKeyboardButton("🔄 Check Again", callback_data="check_join")]
        ])

    def get_user_id(self, update: Update) -> Optional[int]:
        if update.effective_user:
            return update.effective_user.id
        elif update.callback_query and update.callback_query.from_user:
            return update.callback_query.from_user.id
        elif update.message and update.message.from_user:
            return update.message.from_user.id
        return None

    # ============================================
    # START COMMAND
    # ============================================
    def start_command(self, update: Update, context: CallbackContext):
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        if not self.check_force_join(context, user_id):
            update.message.reply_text(
                f"🔒 <b>Access Denied</b>\n\n"
                f"You must join our channel to use this bot!\n\n"
                f"📢 <b>Channel:</b> {FORCE_CHANNEL}\n\n"
                f"<i>Click the button below to join, then click 'Check Again'</i>",
                reply_markup=self.get_force_join_keyboard(),
                parse_mode=ParseMode.HTML
            )
            return
        
        user = update.effective_user
        self.db.create_user(user_id, user.username or '', user.first_name or 'User')
        
        kb = [
            [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
            [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        update.message.reply_text(
            f"🌟 <b>Welcome {user.first_name}!</b>\n\n"
            f"📤 Upload files directly to GitHub\n"
            f"📁 Files are stored securely\n"
            f"⚙️ Customize settings from the menu\n\n"
            f"Choose an option:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    # ============================================
    # SETTINGS
    # ============================================
    def settings_menu(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        prefix = self.db.get_file_prefix(user_id)
        password = self.db.get_archive_password(user_id)
        thumb = self.db.get_thumbnail(user_id)
        
        kb = [
            [InlineKeyboardButton("📝 Set File Prefix", callback_data="set_prefix")],
            [InlineKeyboardButton("🔑 Set Archive Password", callback_data="set_password")],
            [InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="set_thumb")],
            [InlineKeyboardButton("🗑️ Remove Thumbnail", callback_data="remove_thumb")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
        ]
        
        settings_text = (
            f"⚙️ <b>Settings</b>\n\n"
            f"📝 <b>File Prefix:</b> {prefix if prefix else 'None'}\n"
            f"🔑 <b>Archive Password:</b> {'✅ Set' if password else '❌ Not set'}\n"
            f"🖼️ <b>Thumbnail:</b> {'✅ Set' if thumb else '❌ Not set'}\n\n"
            f"<i>Configure your file settings below:</i>"
        )
        
        query.edit_message_text(
            settings_text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    def handle_set_prefix(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        self.user_sessions[user_id] = {'step': 'waiting_prefix'}
        query.edit_message_text(
            "📝 <b>Set File Prefix</b>\n\n"
            "Send your desired prefix in the chat.\n"
            "Example: <code>MY_FILE_</code>\n\n"
            "Send /cancel to cancel",
            parse_mode=ParseMode.HTML
        )

    def handle_set_password(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        self.user_sessions[user_id] = {'step': 'waiting_password'}
        query.edit_message_text(
            "🔑 <b>Set Archive Password</b>\n\n"
            "Send your desired password in the chat.\n"
            "Example: <code>mysecret123</code>\n\n"
            "This password will be used for all archives you create.\n\n"
            "Send /cancel to cancel",
            parse_mode=ParseMode.HTML
        )

    def handle_set_thumb(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        self.user_sessions[user_id] = {'step': 'waiting_thumb'}
        query.edit_message_text(
            "🖼️ <b>Set Thumbnail</b>\n\n"
            "Send a photo to use as thumbnail.\n\n"
            "📸 <b>Supported:</b> JPG, PNG, WEBP\n"
            "📏 <b>Recommended:</b> 320x320 pixels\n\n"
            "Send /cancel to cancel",
            parse_mode=ParseMode.HTML
        )

    def handle_remove_thumb(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        self.db.update_thumbnail(user_id, '')
        query.edit_message_text(
            "🗑️ Thumbnail removed!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
            ])
        )

    # ============================================
    # CALLBACK HANDLER
    # ============================================
    def callback_handler(self, update: Update, context: CallbackContext):
        query = update.callback_query
        query.answer()
        
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        data = query.data
        
        if data != "check_join" and not self.check_force_join(context, user_id):
            query.edit_message_text(
                f"🔒 <b>Access Denied</b>\n\n"
                f"You must join our channel to use this bot!\n\n"
                f"📢 <b>Channel:</b> {FORCE_CHANNEL}",
                reply_markup=self.get_force_join_keyboard(),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "check_join":
            if self.check_force_join(context, user_id):
                user = query.from_user
                kb = [
                    [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
                    [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("❓ Help", callback_data="help")]
                ]
                query.edit_message_text(
                    f"✅ <b>Success!</b> You've joined the channel!\n\n"
                    f"🌟 Welcome {user.first_name}!",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML
                )
            else:
                query.edit_message_text(
                    f"🔒 Still not joined. Please join {FORCE_CHANNEL}",
                    reply_markup=self.get_force_join_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            return
        
        # ---- HELP ----
        if data == "help":
            query.edit_message_text(
                "❓ <b>Help</b>\n\n"
                "📤 <b>Upload Files</b>: Send files directly to GitHub\n"
                "📋 <b>My Files</b>: View and manage your files\n"
                "⚙️ <b>Settings</b>: Customize your preferences\n\n"
                "<b>Settings:</b>\n"
                "📝 File Prefix: Add prefix to filenames\n"
                "🔑 Archive Password: Protect archives\n"
                "🖼️ Thumbnail: Set custom thumbnail\n\n"
                "<b>File Actions:</b>\n"
                "📦 Extract: Unpack ZIP/RAR/7z\n"
                "🗜️ Compress: Create ZIP/7z\n"
                "✏️ Rename: Rename files\n"
                "🗑️ Delete: Remove from storage\n\n"
                f"📢 Required Channel: {FORCE_CHANNEL}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        # ---- BACK TO MENU ----
        if data == "back_to_menu":
            self.show_main_menu(update, context, user_id)
            return
        
        # ---- SETTINGS ----
        if data == "settings":
            self.settings_menu(update, context)
            return
        
        if data == "set_prefix":
            self.handle_set_prefix(update, context)
            return
        
        if data == "set_password":
            self.handle_set_password(update, context)
            return
        
        if data == "set_thumb":
            self.handle_set_thumb(update, context)
            return
        
        if data == "remove_thumb":
            self.handle_remove_thumb(update, context)
            return
        
        # ---- UPLOAD ----
        if data == "upload":
            self.user_sessions[user_id] = {'step': 'waiting_file', 'files': []}
            query.edit_message_text(
                "📤 <b>Upload Files</b>\n\n"
                "Send any file(s) you want to store on GitHub.\n"
                "You can send multiple files.\n\n"
                "After uploading all files, click <b>✅ Done</b>.\n\n"
                "Send /cancel to cancel",
                parse_mode=ParseMode.HTML
            )
            return
        
        # ---- DONE UPLOAD ----
        if data == "done_upload":
            session = self.user_sessions.get(user_id, {})
            files = session.get('files', [])
            
            if not files:
                query.edit_message_text(
                    "❌ No files uploaded yet!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                    ])
                )
                return
            
            # Show progress
            query.edit_message_text(
                f"📤 <b>Uploading {len(files)} files to GitHub...</b>\n\n"
                f"{ProgressBar.circular(0)}\n\n"
                f"<i>Please wait...</i>",
                parse_mode=ParseMode.HTML
            )
            
            # Upload files directly to GitHub
            uploaded_count = 0
            total_files = len(files)
            
            for i, (file, file_name) in enumerate(files):
                # Show progress
                def upload_progress(progress, message):
                    overall = ((i + (progress / 100)) / total_files) * 100
                    query.edit_message_text(
                        f"📤 <b>Uploading to GitHub...</b>\n\n"
                        f"📄 {file_name}\n"
                        f"{ProgressBar.circular(overall)}\n\n"
                        f"<i>{message}</i>",
                        parse_mode=ParseMode.HTML
                    )
                
                # Upload directly from Telegram to GitHub
                success, result = TelegramToGitHubUploader.upload_file_directly(
                    BOT_TOKEN,
                    file.file_id,
                    GITHUB_TOKEN,
                    GITHUB_OWNER,
                    GITHUB_REPO,
                    GITHUB_BRANCH,
                    file_name,
                    user_id,
                    upload_progress
                )
                
                if success:
                    unique_id = secrets.token_hex(16)
                    self.db.add_file(unique_id, user_id, file_name, file.file_size, file.file_id, result)
                    uploaded_count += 1
                else:
                    query.edit_message_text(f"❌ Failed to upload {file_name}: {result}")
            
            # Clear session files
            self.user_sessions[user_id] = {}
            
            # Show completion and ask for action
            kb = [
                [InlineKeyboardButton("📦 Extract All", callback_data="extract_all")],
                [InlineKeyboardButton("🗜️ Compress All", callback_data="compress_all")],
                [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
            ]
            
            query.edit_message_text(
                f"✅ <b>Upload Complete!</b>\n\n"
                f"📄 {uploaded_count}/{total_files} files uploaded to GitHub\n\n"
                f"<b>What would you like to do with your files?</b>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        # ---- MY FILES ----
        if data == "my_files":
            files = self.db.get_user_files(user_id)
            
            if not files:
                query.edit_message_text(
                    "📂 <b>My Files</b>\n\n"
                    "No files uploaded yet.\n\n"
                    "Upload a file to get started!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📤 Upload", callback_data="upload")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                    ]),
                    parse_mode=ParseMode.HTML
                )
                return
            
            text = f"📂 <b>My Files</b> ({len(files)})\n\n"
            btns = []
            
            for f in files[:5]:
                text += f"📄 {f['name']}\n"
                text += f"📦 {self.format_size(f['size'])}\n\n"
                btns.append([
                    InlineKeyboardButton(f"📦 Extract", callback_data=f"extract_{f['id']}"),
                    InlineKeyboardButton(f"🗜️ Compress", callback_data=f"compress_{f['id']}")
                ])
                btns.append([
                    InlineKeyboardButton(f"✏️ Rename", callback_data=f"rename_{f['id']}"),
                    InlineKeyboardButton(f"🗑️ Delete", callback_data=f"delete_{f['id']}")
                ])
            
            btns.append([InlineKeyboardButton("📤 Upload More", callback_data="upload")])
            btns.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")])
            
            query.edit_message_text(
                text,
                reply_markup=InlineKeyboardMarkup(btns),
                parse_mode=ParseMode.HTML
            )
            return
        
        # ---- DELETE FILE ----
        if data.startswith("delete_"):
            file_id = data.replace("delete_", "")
            file_data = self.db.get_file(file_id)
            
            if file_data:
                # Delete from GitHub
                github_path = f"user_files/{user_id}/{file_data['name']}"
                github_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"
                headers = {
                    "Authorization": f"token {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json"
                }
                
                # Get SHA
                try:
                    check_response = requests.get(github_url, headers=headers)
                    if check_response.status_code == 200:
                        sha = check_response.json().get('sha')
                        delete_data = {
                            "message": f"Delete {file_data['name']} by user {user_id}",
                            "sha": sha,
                            "branch": GITHUB_BRANCH
                        }
                        requests.delete(github_url, headers=headers, json=delete_data)
                except:
                    pass
                
                self.db.delete_file(file_id)
            
            query.edit_message_text(
                "✅ File deleted!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )
            return
        
        # ---- EXTRACT FILE ----
        if data.startswith("extract_"):
            file_id = data.replace("extract_", "")
            self.extract_file(update, context, user_id, file_id)
            return
        
        # ---- EXTRACT ALL ----
        if data == "extract_all":
            self.extract_all_files(update, context, user_id)
            return
        
        # ---- COMPRESS FILE ----
        if data.startswith("compress_"):
            file_id = data.replace("compress_", "")
            self.compress_file(update, context, user_id, file_id)
            return
        
        # ---- COMPRESS ALL ----
        if data == "compress_all":
            self.compress_all_files(update, context, user_id)
            return
        
        # ---- RENAME FILE ----
        if data.startswith("rename_"):
            file_id = data.replace("rename_", "")
            self.user_sessions[user_id] = {'step': 'waiting_rename', 'file_id': file_id}
            query.edit_message_text(
                f"✏️ <b>Rename File</b>\n\n"
                f"Send the new name for this file.\n"
                f"Example: <code>new_name.txt</code>\n\n"
                f"Send /cancel to cancel",
                parse_mode=ParseMode.HTML
            )
            return
        
        # ---- CANCEL ----
        if data == "cancel":
            self.user_sessions.pop(user_id, None)
            query.edit_message_text(
                "❌ Cancelled",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )
            return

    # ============================================
    # SHOW MAIN MENU
    # ============================================
    def show_main_menu(self, update, context, user_id):
        query = update.callback_query
        user = self.db.get_user(user_id)
        name = user['first_name'] if user else 'User'
        
        kb = [
            [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
            [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        query.edit_message_text(
            f"🌟 <b>Welcome back {name}!</b>\n\n"
            f"📤 Upload files directly to GitHub\n"
            f"📁 Files are stored securely\n"
            f"⚙️ Customize settings from the menu\n\n"
            f"Choose an option:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    # ============================================
    # FILE HANDLER
    # ============================================
    def file_handler(self, update: Update, context: CallbackContext):
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        msg = update.message
        
        if not self.check_force_join(context, user_id):
            msg.reply_text(
                f"🔒 Please join {FORCE_CHANNEL} first",
                reply_markup=self.get_force_join_keyboard()
            )
            return
        
        session = self.user_sessions.get(user_id)
        if not session or session.get('step') != 'waiting_file':
            msg.reply_text(
                "⚠️ Please use the 'Upload Files' button first.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Upload", callback_data="upload")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )
            return
        
        file = None
        file_name = None
        file_size = 0
        file_id = None
        
        if msg.document:
            file = msg.document
            file_name = file.file_name or 'document'
            file_size = file.file_size
            file_id = file.file_id
        elif msg.photo:
            file = msg.photo[-1]
            file_name = f'photo_{int(time.time())}.jpg'
            file_size = file.file_size
            file_id = file.file_id
        elif msg.video:
            file = msg.video
            file_name = file.file_name or 'video.mp4'
            file_size = file.file_size
            file_id = file.file_id
        else:
            msg.reply_text("❌ Please send a document, photo, or video.")
            return
        
        if file_size > MAX_FILE_SIZE:
            msg.reply_text(f"❌ File too large ({self.format_size(file_size)}). Max: 2GB")
            return
        
        # Store file in session
        if 'files' not in session:
            session['files'] = []
        session['files'].append((file, file_name))
        
        # Show file uploaded message with file list
        file_list = ""
        for f, name in session['files']:
            file_list += f"• {name}\n"
        
        kb = [
            [InlineKeyboardButton("✅ Done", callback_data="done_upload")],
            [InlineKeyboardButton("➕ Upload More", callback_data="upload")],
            [InlineKeyboardButton("🗑️ Clear All", callback_data="cancel")]
        ]
        
        msg.reply_text(
            f"✅ <b>File uploaded!</b>\n\n"
            f"📄 <b>Uploaded Files ({len(session['files'])}):</b>\n"
            f"{file_list}\n\n"
            f"Click <b>✅ Done</b> when finished uploading.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    # ============================================
    # TEXT HANDLER
    # ============================================
    def text_handler(self, update: Update, context: CallbackContext):
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        text = update.message.text
        
        if not self.check_force_join(context, user_id):
            update.message.reply_text(
                f"🔒 Please join {FORCE_CHANNEL} first",
                reply_markup=self.get_force_join_keyboard()
            )
            return
        
        if text and text.lower() == '/cancel':
            self.user_sessions.pop(user_id, None)
            update.message.reply_text(
                "❌ Cancelled",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )
            return
        
        session = self.user_sessions.get(user_id)
        
        # Handle prefix
        if session and session.get('step') == 'waiting_prefix':
            self.db.update_file_prefix(user_id, text)
            self.user_sessions.pop(user_id, None)
            update.message.reply_text(
                f"✅ Prefix set to: <b>{text}</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        # Handle password
        if session and session.get('step') == 'waiting_password':
            self.db.update_archive_password(user_id, text)
            self.user_sessions.pop(user_id, None)
            update.message.reply_text(
                f"✅ Archive password set!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        # Handle rename
        if session and session.get('step') == 'waiting_rename':
            file_id = session.get('file_id')
            file_data = self.db.get_file(file_id)
            
            if file_data:
                # Download from GitHub
                github_path = f"user_files/{user_id}/{file_data['name']}"
                github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{github_path}"
                response = requests.get(github_url)
                
                if response.status_code == 200:
                    content = response.content
                    encoded = base64.b64encode(content).decode('utf-8')
                    
                    # Upload with new name
                    new_path = f"user_files/{user_id}/{text}"
                    new_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{new_path}"
                    headers = {
                        "Authorization": f"token {GITHUB_TOKEN}",
                        "Accept": "application/vnd.github.v3+json"
                    }
                    
                    # Check if exists
                    sha = None
                    try:
                        check_response = requests.get(new_url, headers=headers)
                        if check_response.status_code == 200:
                            sha = check_response.json().get('sha')
                    except:
                        pass
                    
                    data = {
                        "message": f"Rename {file_data['name']} to {text} by user {user_id}",
                        "content": encoded,
                        "branch": GITHUB_BRANCH
                    }
                    if sha:
                        data["sha"] = sha
                    
                    upload_response = requests.put(new_url, headers=headers, json=data)
                    
                    if upload_response.status_code in [200, 201]:
                        # Delete old file
                        old_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"
                        check_response = requests.get(old_url, headers=headers)
                        if check_response.status_code == 200:
                            old_sha = check_response.json().get('sha')
                            delete_data = {
                                "message": f"Delete {file_data['name']} by user {user_id}",
                                "sha": old_sha,
                                "branch": GITHUB_BRANCH
                            }
                            requests.delete(old_url, headers=headers, json=delete_data)
                        
                        # Update database
                        self.db.delete_file(file_id)
                        unique_id = secrets.token_hex(16)
                        self.db.add_file(unique_id, user_id, text, file_data['size'], file_data['file_id'], new_url)
                        
                        update.message.reply_text(
                            f"✅ File renamed to: <b>{text}</b>",
                            reply_markup=InlineKeyboardMarkup([
                                [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                                [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                            ]),
                            parse_mode=ParseMode.HTML
                        )
                    else:
                        update.message.reply_text(f"❌ Rename failed")
                else:
                    update.message.reply_text("❌ Could not download file from GitHub")
            else:
                update.message.reply_text("❌ File not found")
            
            self.user_sessions.pop(user_id, None)
            return
        
        # If not in session, treat as file upload
        self.file_handler(update, context)

    # ============================================
    # PHOTO HANDLER (for thumbnail)
    # ============================================
    def photo_handler(self, update: Update, context: CallbackContext):
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        session = self.user_sessions.get(user_id)
        
        # Check if in thumbnail upload mode
        if session and session.get('step') == 'waiting_thumb':
            photo = update.message.photo[-1]
            file_obj = context.bot.get_file(photo.file_id)
            thumb_path = os.path.join(TEMP_DIR, f"{user_id}_thumb.jpg")
            file_obj.download(thumb_path)
            
            self.db.update_thumbnail(user_id, thumb_path)
            self.user_sessions.pop(user_id, None)
            
            update.message.reply_text(
                f"✅ <b>Thumbnail set successfully!</b>\n\n"
                f"🆔 File ID: <code>{photo.file_id[:30]}...</code>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        # Otherwise handle as regular file upload
        self.file_handler(update, context)

    # ============================================
    # EXTRACT ALL FILES
    # ============================================
    def extract_all_files(self, update, context, user_id):
        query = update.callback_query
        files = self.db.get_user_files(user_id)
        
        if not files:
            query.edit_message_text("❌ No files to extract.")
            return
        
        query.edit_message_text(f"📦 Extracting {len(files)} files...\n\n{ProgressBar.circular(0)}")
        
        all_extracted = []
        
        for idx, file_data in enumerate(files):
            progress = ((idx + 1) / len(files)) * 100
            query.edit_message_text(
                f"📦 Extracting {idx+1}/{len(files)}: {file_data['name']}\n\n{ProgressBar.circular(progress)}"
            )
            
            # Download from GitHub
            github_path = f"user_files/{user_id}/{file_data['name']}"
            github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{github_path}"
            response = requests.get(github_url)
            
            if response.status_code != 200:
                continue
            
            temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_data['name']}")
            with open(temp_path, 'wb') as f:
                f.write(response.content)
            
            ext = os.path.splitext(file_data['name'])[1].lower()
            if ext not in ['.zip', '.rar', '.7z']:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                continue
            
            try:
                extract_dir = os.path.join(TEMP_DIR, f"{user_id}_extracted")
                os.makedirs(extract_dir, exist_ok=True)
                
                password = self.db.get_archive_password(user_id) or None
                
                if ext == '.zip':
                    with zipfile.ZipFile(temp_path, 'r') as zip_ref:
                        if password:
                            zip_ref.setpassword(password.encode())
                        zip_ref.extractall(extract_dir)
                elif ext == '.rar':
                    with rarfile.RarFile(temp_path) as rar_ref:
                        if password:
                            rar_ref.setpassword(password)
                        rar_ref.extractall(extract_dir)
                elif ext == '.7z':
                    with py7zr.SevenZipFile(temp_path, mode='r', password=password) as sz_ref:
                        sz_ref.extractall(extract_dir)
                
                for root, dirs, files_in_dir in os.walk(extract_dir):
                    for f in files_in_dir:
                        all_extracted.append(os.path.join(root, f))
                        
            except Exception as e:
                query.edit_message_text(f"❌ Error extracting {file_data['name']}: {str(e)}")
            
            if os.path.exists(temp_path):
                os.remove(temp_path)
        
        query.edit_message_text(f"✅ Extracted {len(all_extracted)} files!\n\n{ProgressBar.circular(100)}")
        
        if all_extracted:
            context.bot.send_message(
                chat_id=query.message.chat_id,
                text=f"📤 Sending {len(all_extracted)} extracted files..."
            )
            
            thumb = self.db.get_thumbnail(user_id)
            prefix = self.db.get_file_prefix(user_id)
            
            for file_path in all_extracted:
                if os.path.getsize(file_path) < MAX_FILE_SIZE:
                    file_name = os.path.basename(file_path)
                    if prefix:
                        file_name = f"{prefix}{file_name}"
                    
                    with open(file_path, 'rb') as doc:
                        context.bot.send_document(
                            chat_id=query.message.chat_id,
                            document=doc,
                            filename=file_name,
                            thumbnail=open(thumb, 'rb') if thumb and os.path.exists(thumb) else None
                        )
        
        shutil.rmtree(os.path.join(TEMP_DIR, f"{user_id}_extracted"), ignore_errors=True)
        
        # Show menu
        kb = [
            [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
            [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
        ]
        context.bot.send_message(
            chat_id=query.message.chat_id,
            text="📤 Return to menu:",
            reply_markup=InlineKeyboardMarkup(kb)
        )

    # ============================================
    # COMPRESS ALL FILES
    # ============================================
    def compress_all_files(self, update, context, user_id):
        query = update.callback_query
        
        # Show compression options
        kb = [
            [InlineKeyboardButton("📦 ZIP", callback_data="compress_all_zip")],
            [InlineKeyboardButton("📦 7Z", callback_data="compress_all_7z")],
            [InlineKeyboardButton("🔙 Back", callback_data="my_files")]
        ]
        
        query.edit_message_text(
            "🗜️ <b>Compress All Files</b>\n\n"
            "Choose compression format:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    # ============================================
    # EXTRACT SINGLE FILE
    # ============================================
    def extract_file(self, update, context, user_id, file_id):
        query = update.callback_query
        file_data = self.db.get_file(file_id)
        
        if not file_data:
            query.edit_message_text("❌ File not found")
            return
        
        query.edit_message_text(f"📦 Extracting {file_data['name']}...\n\n{ProgressBar.circular(0)}")
        
        # Download from GitHub
        github_path = f"user_files/{user_id}/{file_data['name']}"
        github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{github_path}"
        response = requests.get(github_url)
        
        if response.status_code != 200:
            query.edit_message_text("❌ Could not download file from GitHub")
            return
        
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_data['name']}")
        with open(temp_path, 'wb') as f:
            f.write(response.content)
        
        ext = os.path.splitext(file_data['name'])[1].lower()
        if ext not in ['.zip', '.rar', '.7z']:
            query.edit_message_text("❌ Not an archive file. Supported: ZIP, RAR, 7z")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return
        
        try:
            extract_dir = os.path.join(TEMP_DIR, f"{user_id}_extracted")
            os.makedirs(extract_dir, exist_ok=True)
            
            password = self.db.get_archive_password(user_id) or None
            
            if ext == '.zip':
                with zipfile.ZipFile(temp_path, 'r') as zip_ref:
                    total = len(zip_ref.namelist())
                    for i, name in enumerate(zip_ref.namelist()):
                        zip_ref.extract(name, extract_dir)
                        if i % 5 == 0:
                            progress = (i / total) * 100 if total > 0 else 0
                            query.edit_message_text(
                                f"📦 Extracting {file_data['name']}...\n\n{ProgressBar.circular(progress)}"
                            )
                            
            elif ext == '.rar':
                with rarfile.RarFile(temp_path) as rar_ref:
                    total = len(rar_ref.namelist())
                    for i, name in enumerate(rar_ref.namelist()):
                        rar_ref.extract(name, extract_dir)
                        if i % 5 == 0:
                            progress = (i / total) * 100 if total > 0 else 0
                            query.edit_message_text(
                                f"📦 Extracting {file_data['name']}...\n\n{ProgressBar.circular(progress)}"
                            )
                            
            elif ext == '.7z':
                with py7zr.SevenZipFile(temp_path, mode='r', password=password) as sz_ref:
                    files = sz_ref.getnames()
                    total = len(files)
                    for i, name in enumerate(files):
                        sz_ref.extract(targets=[name], path=extract_dir)
                        if i % 2 == 0:
                            progress = (i / total) * 100 if total > 0 else 0
                            query.edit_message_text(
                                f"📦 Extracting {file_data['name']}...\n\n{ProgressBar.circular(progress)}"
                            )
            
            query.edit_message_text(f"✅ Extraction complete!\n\n{ProgressBar.circular(100)}")
            
            extracted = []
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    extracted.append(os.path.join(root, f))
            
            if extracted:
                context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"📤 Sending {len(extracted)} extracted files..."
                )
                
                thumb = self.db.get_thumbnail(user_id)
                prefix = self.db.get_file_prefix(user_id)
                
                for f_path in extracted:
                    if os.path.getsize(f_path) < MAX_FILE_SIZE:
                        file_name = os.path.basename(f_path)
                        if prefix:
                            file_name = f"{prefix}{file_name}"
                        
                        with open(f_path, 'rb') as doc:
                            context.bot.send_document(
                                chat_id=query.message.chat_id,
                                document=doc,
                                filename=file_name,
                                thumbnail=open(thumb, 'rb') if thumb and os.path.exists(thumb) else None
                            )
            
            shutil.rmtree(extract_dir, ignore_errors=True)
            
        except Exception as e:
            query.edit_message_text(f"❌ Extraction error: {str(e)}")
        
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # ============================================
    # COMPRESS SINGLE FILE
    # ============================================
    def compress_file(self, update, context, user_id, file_id):
        query = update.callback_query
        file_data = self.db.get_file(file_id)
        
        if not file_data:
            query.edit_message_text("❌ File not found")
            return
        
        kb = [
            [InlineKeyboardButton("📦 ZIP", callback_data=f"compress_single_zip_{file_id}")],
            [InlineKeyboardButton("📦 7Z", callback_data=f"compress_single_7z_{file_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="my_files")]
        ]
        
        query.edit_message_text(
            f"🗜️ <b>Compress: {file_data['name']}</b>\n\n"
            f"Choose compression format:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    # ============================================
    # RUN BOT
    # ============================================
    def run(self):
        logger.info('🚀 Starting Archive Bot...')
        
        try:
            updater = Updater(BOT_TOKEN, use_context=True)
            dp = updater.dispatcher
            
            bot_info = updater.bot.get_me()
            self.bot_username = bot_info.username
            self.bot_id = bot_info.id
            logger.info(f'✅ Bot running: @{self.bot_username}')
            
            updater.bot.set_my_commands([
                ('start', '🚀 Start the bot'),
            ])
            
            dp.add_handler(CommandHandler('start', self.start_command))
            dp.add_handler(MessageHandler(Filters.document, self.file_handler))
            dp.add_handler(MessageHandler(Filters.photo, self.photo_handler))
            dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.text_handler))
            dp.add_handler(CallbackQueryHandler(self.callback_handler))
            
            logger.info('✅ Bot is ready!')
            
            updater.start_polling()
            logger.info('🔄 Polling started...')
            
            updater.idle()
            
        except Exception as e:
            logger.error(f'❌ Bot error: {e}')
            raise
        
        self.db.close()
        logger.info('🛑 Bot stopped')


# ============================================
# MAIN
# ============================================
if __name__ == '__main__':
    try:
        bot = ArchiveBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info('🛑 Bot stopped by user')
    except Exception as e:
        logger.error(f'❌ Fatal error: {e}')
        sys.exit(1)