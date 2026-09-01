#!/usr/bin/env python3
# ============================================
# TELEGRAM ARCHIVE BOT - FIXED VERSION
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

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024
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
# GITHUB MANAGER
# ============================================
class GitHubManager:
    def __init__(self, token: str, owner: str, repo: str, branch: str = 'main'):
        self.token = token
        self.owner = owner
        self.repo = repo
        self.branch = branch
        self.base_url = f"https://api.github.com/repos/{owner}/{repo}/contents"
        self.headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }

    def upload_file(self, file_path: str, file_name: str, user_id: int) -> tuple:
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            
            encoded = base64.b64encode(content).decode('utf-8')
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
                "content": encoded,
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

    def delete_file(self, file_name: str, user_id: int) -> tuple:
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
                return True, "File deleted"
            else:
                return False, f"Delete failed: {response.text}"
                
        except Exception as e:
            return False, str(e)

    def download_file(self, file_name: str, user_id: int, save_path: str) -> bool:
        try:
            url = f"https://raw.githubusercontent.com/{self.owner}/{self.repo}/{self.branch}/user_files/{user_id}/{file_name}"
            response = requests.get(url)
            
            if response.status_code == 200:
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                return True
            else:
                return False
        except:
            return False


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
        self.github = GitHubManager(GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH)
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
        """Safely get user ID from update"""
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
        prefix = self.db.get_file_prefix(user_id)
        
        kb = [
            [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
            [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        update.message.reply_text(
            f"🌟 <b>Welcome {user.first_name}!</b>\n\n"
            f"📤 Upload files to GitHub storage\n"
            f"📁 Files are stored securely\n"
            f"📝 Prefix: {prefix if prefix else 'None'}\n\n"
            f"Choose an option:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
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
        
        if data == "help":
            query.edit_message_text(
                "❓ <b>Help</b>\n\n"
                "📤 <b>Upload Files</b>: Send files to store on GitHub\n"
                "📋 <b>My Files</b>: View and manage your files\n"
                "⚙️ <b>Settings</b>: Set file prefix\n\n"
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
        
        if data == "back_to_menu":
            prefix = self.db.get_file_prefix(user_id)
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
                f"📝 Prefix: {prefix if prefix else 'None'}\n\n"
                f"Choose an option:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "settings":
            prefix = self.db.get_file_prefix(user_id)
            
            kb = [
                [InlineKeyboardButton("📝 Set File Prefix", callback_data="set_prefix")],
                [InlineKeyboardButton("🗑️ Remove Prefix", callback_data="remove_prefix")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
            ]
            
            query.edit_message_text(
                f"⚙️ <b>Settings</b>\n\n"
                f"📝 <b>Current Prefix:</b> {prefix if prefix else 'None'}\n\n"
                f"Prefix format: PREFIX + ORIGINAL_NAME.extension",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "set_prefix":
            self.user_sessions[user_id] = {'step': 'waiting_prefix'}
            query.edit_message_text(
                "📝 <b>Set File Prefix</b>\n\n"
                "Send your desired prefix in the chat.\n"
                "Example: <code>MY_FILE_</code>\n\n"
                "Send /cancel to cancel",
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "remove_prefix":
            self.db.update_file_prefix(user_id, '')
            query.edit_message_text(
                "✅ Prefix removed!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="settings")]
                ])
            )
            return
        
        if data == "upload":
            self.user_sessions[user_id] = {'step': 'waiting_file'}
            query.edit_message_text(
                "📤 <b>Upload Files</b>\n\n"
                "Send any file(s) you want to store on GitHub.\n"
                "You can send multiple files.\n\n"
                "After uploading, click <b>✅ Done</b> to access the menu.\n\n"
                "Send /cancel to cancel",
                parse_mode=ParseMode.HTML
            )
            return
        
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
        
        if data.startswith("delete_"):
            file_id = data.replace("delete_", "")
            file_data = self.db.get_file(file_id)
            
            if file_data:
                self.github.delete_file(file_data['name'], user_id)
                self.db.delete_file(file_id)
            
            query.edit_message_text(
                "✅ File deleted!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )
            return
        
        if data.startswith("extract_"):
            file_id = data.replace("extract_", "")
            self.extract_file(update, context, user_id, file_id)
            return
        
        if data.startswith("compress_"):
            file_id = data.replace("compress_", "")
            self.compress_file(update, context, user_id, file_id)
            return
        
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
        
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        file_obj = context.bot.get_file(file_id)
        file_obj.download(temp_path)
        
        success, result = self.github.upload_file(temp_path, file_name, user_id)
        
        if success:
            unique_id = secrets.token_hex(16)
            self.db.add_file(unique_id, user_id, file_name, file_size, file_id, result)
            
            msg.reply_text(
                f"✅ <b>File Uploaded!</b>\n\n"
                f"📄 {file_name}\n"
                f"📦 {self.format_size(file_size)}\n"
                f"🔒 Stored on GitHub\n\n"
                f"📤 Upload more files or click the menu below.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📤 Upload More", callback_data="upload")],
                    [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                    [InlineKeyboardButton("✅ Done", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
        else:
            msg.reply_text(f"❌ Upload failed: {result}")
        
        if os.path.exists(temp_path):
            os.remove(temp_path)

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
        
        if session and session.get('step') == 'waiting_rename':
            file_id = session.get('file_id')
            file_data = self.db.get_file(file_id)
            
            if file_data:
                temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_data['name']}")
                if self.github.download_file(file_data['name'], user_id, temp_path):
                    success, result = self.github.upload_file(temp_path, text, user_id)
                    if success:
                        self.github.delete_file(file_data['name'], user_id)
                        self.db.delete_file(file_id)
                        unique_id = secrets.token_hex(16)
                        self.db.add_file(unique_id, user_id, text, file_data['size'], file_data['file_id'], result)
                        
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
                    
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                else:
                    update.message.reply_text("❌ Could not download file from GitHub")
            else:
                update.message.reply_text("❌ File not found")
            
            self.user_sessions.pop(user_id, None)
            return
        
        self.file_handler(update, context)

    # ============================================
    # EXTRACT FILE
    # ============================================
    def extract_file(self, update, context, user_id, file_id):
        query = update.callback_query
        file_data = self.db.get_file(file_id)
        
        if not file_data:
            query.edit_message_text("❌ File not found")
            return
        
        query.edit_message_text("📦 Downloading file from GitHub...")
        
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_data['name']}")
        if not self.github.download_file(file_data['name'], user_id, temp_path):
            query.edit_message_text("❌ Could not download file from GitHub")
            return
        
        ext = os.path.splitext(file_data['name'])[1].lower()
        if ext not in ['.zip', '.rar', '.7z']:
            query.edit_message_text("❌ Not an archive file. Supported: ZIP, RAR, 7z")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return
        
        query.edit_message_text(f"📦 Extracting {file_data['name']}...\n\n{ProgressBar.circular(0)}")
        
        try:
            extract_dir = os.path.join(TEMP_DIR, f"{user_id}_extracted")
            os.makedirs(extract_dir, exist_ok=True)
            
            if ext == '.zip':
                with zipfile.ZipFile(temp_path, 'r') as zip_ref:
                    total = len(zip_ref.namelist())
                    for i, name in enumerate(zip_ref.namelist()):
                        zip_ref.extract(name, extract_dir)
                        if i % 5 == 0:
                            progress = (i / total) * 100 if total > 0 else 0
                            query.edit_message_text(
                                f"📦 Extracting... {i+1}/{total}\n\n{ProgressBar.circular(progress)}"
                            )
                            
            elif ext == '.rar':
                with rarfile.RarFile(temp_path) as rar_ref:
                    total = len(rar_ref.namelist())
                    for i, name in enumerate(rar_ref.namelist()):
                        rar_ref.extract(name, extract_dir)
                        if i % 5 == 0:
                            progress = (i / total) * 100 if total > 0 else 0
                            query.edit_message_text(
                                f"📦 Extracting... {i+1}/{total}\n\n{ProgressBar.circular(progress)}"
                            )
                            
            elif ext == '.7z':
                with py7zr.SevenZipFile(temp_path, mode='r') as sz_ref:
                    files = sz_ref.getnames()
                    total = len(files)
                    for i, name in enumerate(files):
                        sz_ref.extract(targets=[name], path=extract_dir)
                        if i % 2 == 0:
                            progress = (i / total) * 100 if total > 0 else 0
                            query.edit_message_text(
                                f"📦 Extracting... {i+1}/{total}\n\n{ProgressBar.circular(progress)}"
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
                
                for f_path in extracted:
                    if os.path.getsize(f_path) < MAX_FILE_SIZE:
                        with open(f_path, 'rb') as doc:
                            context.bot.send_document(
                                chat_id=query.message.chat_id,
                                document=doc,
                                filename=os.path.basename(f_path)
                            )
            
            shutil.rmtree(extract_dir, ignore_errors=True)
            
        except Exception as e:
            query.edit_message_text(f"❌ Extraction error: {str(e)}")
        
        if os.path.exists(temp_path):
            os.remove(temp_path)

    # ============================================
    # COMPRESS FILE
    # ============================================
    def compress_file(self, update, context, user_id, file_id):
        query = update.callback_query
        file_data = self.db.get_file(file_id)
        
        if not file_data:
            query.edit_message_text("❌ File not found")
            return
        
        kb = [
            [InlineKeyboardButton("📦 ZIP", callback_data=f"compress_zip_{file_id}")],
            [InlineKeyboardButton("📦 7Z", callback_data=f"compress_7z_{file_id}")],
            [InlineKeyboardButton("🔙 Back", callback_data="my_files")]
        ]
        
        query.edit_message_text(
            f"🗜️ <b>Compress: {file_data['name']}</b>\n\n"
            f"Choose compression format:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    # ============================================
    # PHOTO HANDLER
    # ============================================
    def photo_handler(self, update: Update, context: CallbackContext):
        self.file_handler(update, context)

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