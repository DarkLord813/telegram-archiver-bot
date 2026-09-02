#!/usr/bin/env python3
# ============================================
# TELEGRAM ARCHIVE BOT - COMPLETE VERSION
# All data stored in GitHub - Auto-delete after send
# ============================================

import os
import sys
import secrets
import logging
import shutil
import zipfile
import rarfile
import py7zr
import time
import base64
import requests
import json
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

os.makedirs(TEMP_DIR, exist_ok=True)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ============================================
# GITHUB DATA MANAGER
# ============================================
class GitHubDataManager:
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

    def _get_file_content(self, path: str) -> Optional[dict]:
        try:
            url = f"{self.base_url}/{path}"
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                content = response.json()
                if content.get('content'):
                    decoded = base64.b64decode(content['content']).decode('utf-8')
                    return json.loads(decoded)
            return None
        except Exception as e:
            logger.error(f"Error getting file content: {e}")
            return None

    def _update_file(self, path: str, data: dict, message: str) -> bool:
        try:
            url = f"{self.base_url}/{path}"
            content = json.dumps(data, indent=2)
            encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            
            sha = None
            try:
                response = requests.get(url, headers=self.headers)
                if response.status_code == 200:
                    sha = response.json().get('sha')
            except:
                pass
            
            upload_data = {
                "message": message,
                "content": encoded,
                "branch": self.branch
            }
            if sha:
                upload_data["sha"] = sha
            
            response = requests.put(url, headers=self.headers, json=upload_data)
            return response.status_code in [200, 201]
        except Exception as e:
            logger.error(f"Error updating file: {e}")
            return False

    def _delete_file(self, path: str, message: str) -> bool:
        try:
            url = f"{self.base_url}/{path}"
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                return False
            
            sha = response.json().get('sha')
            delete_data = {
                "message": message,
                "sha": sha,
                "branch": self.branch
            }
            response = requests.delete(url, headers=self.headers, json=delete_data)
            return response.status_code in [200, 204]
        except:
            return False

    def get_user_files(self, user_id: int) -> List[Dict]:
        data = self._get_file_content(f"data/files/{user_id}.json")
        if data and data.get('files'):
            return [f for f in data['files'] if f.get('is_active', 1) == 1]
        return []

    def get_file(self, user_id: int, file_id: str) -> Optional[Dict]:
        files = self.get_user_files(user_id)
        for f in files:
            if f.get('id') == file_id:
                return f
        return None

    def delete_file_from_github(self, user_id: int, file_name: str) -> bool:
        path = f"user_files/{user_id}/{file_name}"
        return self._delete_file(path, f"Delete {file_name} by user {user_id}")

    def delete_user_file(self, user_id: int, file_id: str):
        files = self.get_user_files(user_id)
        for f in files:
            if f.get('id') == file_id:
                f['is_active'] = 0
                self._update_file(
                    f"data/files/{user_id}.json",
                    {"files": files},
                    f"Delete file {file_id} for user {user_id}"
                )
                return True
        return False

    def add_file(self, file_id: str, user_id: int, name: str, size: int, telegram_file_id: str, github_path: str):
        files = self.get_user_files(user_id)
        file_data = {
            "id": file_id,
            "user_id": user_id,
            "name": name,
            "size": size,
            "file_id": telegram_file_id,
            "github_path": github_path,
            "created_at": datetime.now().isoformat(),
            "is_active": 1
        }
        files.append(file_data)
        self._update_file(
            f"data/files/{user_id}.json",
            {"files": files},
            f"Add file {name} for user {user_id}"
        )

    def get_user_field(self, user_id: int, field: str) -> str:
        data = self._get_file_content(f"data/users/{user_id}.json")
        if data:
            return data.get(field, '')
        return ''

    def update_user(self, user_id: int, field: str, value: str):
        data = self._get_file_content(f"data/users/{user_id}.json")
        if data:
            data[field] = value
            self._update_file(
                f"data/users/{user_id}.json",
                data,
                f"Update user {user_id} - {field}"
            )


# ============================================
# FAST GITHUB UPLOADER
# ============================================
class FastGitHubUploader:
    @staticmethod
    def upload_file_directly(bot_token: str, file_id: str, github_token: str, github_owner: str,
                           github_repo: str, github_branch: str, file_name: str, user_id: int,
                           progress_callback=None) -> tuple:
        try:
            telegram_url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
            response = requests.get(telegram_url)
            response.raise_for_status()
            file_info = response.json()
            
            if not file_info.get('ok'):
                return False, f"Failed to get file info: {file_info}"
            
            file_path = file_info['result']['file_path']
            download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            
            if progress_callback:
                progress_callback(10, "Starting upload...")
            
            response = requests.get(download_url, stream=True)
            response.raise_for_status()
            
            content_parts = []
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            for chunk in response.iter_content(chunk_size=131072):
                if chunk:
                    content_parts.append(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total_size > 0:
                        progress = 10 + (downloaded / total_size) * 60
                        progress_callback(progress, f"Downloading... {int(progress)}%")
            
            content = b''.join(content_parts)
            encoded = base64.b64encode(content).decode('utf-8')
            
            if progress_callback:
                progress_callback(70, "Uploading to GitHub...")
            
            github_path = f"user_files/{user_id}/{file_name}"
            github_url = f"https://api.github.com/repos/{github_owner}/{github_repo}/contents/{github_path}"
            
            headers = {
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github.v3+json"
            }
            
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
# MAIN BOT CLASS
# ============================================
class ArchiveBot:
    def __init__(self):
        self.github_data = GitHubDataManager(GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH)
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
        existing_user = self.github_data.get_user(user_id)
        if not existing_user:
            self.github_data.create_user(user_id, user.username or '', user.first_name or 'User')
        
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
                kb = [
                    [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
                    [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("❓ Help", callback_data="help")]
                ]
                query.edit_message_text(
                    f"✅ <b>Success!</b> You've joined the channel!\n\n"
                    f"🌟 Welcome!",
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
                "📤 <b>Upload Files</b>: Send files to GitHub\n"
                "📋 <b>My Files</b>: View your files\n"
                "⚙️ <b>Settings</b>: Set prefix & password\n\n"
                "<b>File Actions:</b>\n"
                "📦 Extract: Unpack ZIP/RAR/7z\n"
                "🗜️ Compress: Create ZIP/7z\n"
                "✏️ Rename: Rename files\n"
                "🗑️ Delete: Remove from storage\n\n"
                f"📢 Required: {FORCE_CHANNEL}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "back_to_menu":
            kb = [
                [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
                [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                [InlineKeyboardButton("❓ Help", callback_data="help")]
            ]
            query.edit_message_text(
                "🏠 <b>Main Menu</b>\n\nChoose an option:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "settings":
            prefix = self.github_data.get_user_field(user_id, 'file_prefix')
            password = self.github_data.get_user_field(user_id, 'archive_password')
            
            kb = [
                [InlineKeyboardButton("📝 Set Prefix", callback_data="set_prefix")],
                [InlineKeyboardButton("🔑 Set Password", callback_data="set_password")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
            ]
            
            query.edit_message_text(
                f"⚙️ <b>Settings</b>\n\n"
                f"📝 Prefix: {prefix if prefix else 'None'}\n"
                f"🔑 Password: {'✅' if password else '❌'}\n",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "set_prefix":
            kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
            query.edit_message_text(
                "📝 <b>Send your prefix</b>\n\nExample: <code>MY_FILE_</code>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            self.user_sessions[user_id] = {'step': 'waiting_prefix'}
            return
        
        if data == "set_password":
            kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
            query.edit_message_text(
                "🔑 <b>Send your password</b>\n\nExample: <code>mysecret123</code>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            self.user_sessions[user_id] = {'step': 'waiting_password'}
            return
        
        if data == "upload":
            kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
            query.edit_message_text(
                "📤 <b>Send your file(s)</b>\n\nAfter uploading all, click <b>✅ Done</b>.",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            self.user_sessions[user_id] = {'step': 'waiting_file', 'files': []}
            return
        
        if data == "done_upload":
            session = self.user_sessions.get(user_id, {})
            files = session.get('files', [])
            
            if not files:
                query.edit_message_text(
                    "❌ No files uploaded!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📤 Upload", callback_data="upload")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                    ])
                )
                return
            
            query.edit_message_text(
                f"📤 Uploading {len(files)} files...\n\n{ProgressBar.circular(0)}",
                parse_mode=ParseMode.HTML
            )
            
            uploaded = 0
            total = len(files)
            
            for i, (fid, fname, fsize) in enumerate(files):
                def progress_cb(p, msg):
                    overall = ((i + (p / 100)) / total) * 100
                    try:
                        query.edit_message_text(
                            f"📤 Uploading...\n📄 {fname}\n\n{ProgressBar.circular(overall)}",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                
                success, result = FastGitHubUploader.upload_file_directly(
                    BOT_TOKEN, fid, GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO,
                    GITHUB_BRANCH, fname, user_id, progress_cb
                )
                
                if success:
                    unique_id = secrets.token_hex(16)
                    self.github_data.add_file(unique_id, user_id, fname, fsize, fid, result)
                    uploaded += 1
                else:
                    query.edit_message_text(f"❌ Failed: {fname}")
                    time.sleep(1)
            
            self.user_sessions[user_id] = {}
            
            kb = [
                [InlineKeyboardButton("📦 Extract All", callback_data="extract_all")],
                [InlineKeyboardButton("🗜️ Compress All", callback_data="compress_all")],
                [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
            ]
            
            query.edit_message_text(
                f"✅ {uploaded}/{total} uploaded!\n\nWhat now?",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "my_files":
            files = self.github_data.get_user_files(user_id)
            
            if not files:
                query.edit_message_text(
                    "📂 No files found.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📤 Upload", callback_data="upload")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                    ])
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
        
        # --- DELETE ---
        if data.startswith("delete_"):
            file_id = data.replace("delete_", "")
            file_data = self.github_data.get_file(user_id, file_id)
            
            if file_data:
                self.github_data.delete_file_from_github(user_id, file_data['name'])
                self.github_data.delete_user_file(user_id, file_id)
            
            query.edit_message_text(
                "✅ File deleted!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )
            return
        
        # --- EXTRACT ---
        if data == "extract_all":
            self.extract_all_files(update, context, user_id)
            return
        
        if data.startswith("extract_"):
            file_id = data.replace("extract_", "")
            self.extract_single_file(update, context, user_id, file_id)
            return
        
        # --- COMPRESS ---
        if data == "compress_all":
            kb = [
                [InlineKeyboardButton("📦 ZIP", callback_data="compress_all_zip")],
                [InlineKeyboardButton("📦 7Z", callback_data="compress_all_7z")],
                [InlineKeyboardButton("🔙 Back", callback_data="my_files")]
            ]
            query.edit_message_text(
                "🗜️ <b>Compress All</b>\n\nChoose format:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "compress_all_zip":
            self.compress_all_files(update, context, user_id, "zip")
            return
        
        if data == "compress_all_7z":
            self.compress_all_files(update, context, user_id, "7z")
            return
        
        if data.startswith("compress_"):
            # Single file compression - show format options
            file_id = data.replace("compress_", "")
            file_data = self.github_data.get_file(user_id, file_id)
            
            if not file_data:
                query.edit_message_text("❌ File not found")
                return
            
            kb = [
                [InlineKeyboardButton("📦 ZIP", callback_data=f"compress_single_zip_{file_id}")],
                [InlineKeyboardButton("📦 7Z", callback_data=f"compress_single_7z_{file_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data="my_files")]
            ]
            
            query.edit_message_text(
                f"🗜️ <b>Compress: {file_data['name']}</b>\n\nChoose format:",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        # Single file compression with format
        if data.startswith("compress_single_zip_"):
            file_id = data.replace("compress_single_zip_", "")
            self.compress_single_file(update, context, user_id, file_id, "zip")
            return
        
        if data.startswith("compress_single_7z_"):
            file_id = data.replace("compress_single_7z_", "")
            self.compress_single_file(update, context, user_id, file_id, "7z")
            return
        
        # --- RENAME ---
        if data.startswith("rename_"):
            file_id = data.replace("rename_", "")
            file_data = self.github_data.get_file(user_id, file_id)
            
            if not file_data:
                query.edit_message_text("❌ File not found")
                return
            
            kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
            
            query.edit_message_text(
                f"✏️ <b>Rename: {file_data['name']}</b>\n\n"
                f"Send new name.\nExample: <code>new_name.txt</code>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            
            self.user_sessions[user_id] = {'step': 'waiting_rename', 'file_id': file_id}
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
    # EXTRACT FUNCTIONS
    # ============================================
    def extract_single_file(self, update, context, user_id, file_id):
        query = update.callback_query
        file_data = self.github_data.get_file(user_id, file_id)
        
        if not file_data:
            query.edit_message_text("❌ File not found")
            return
        
        self.extract_and_send(update, context, user_id, file_data)

    def extract_all_files(self, update, context, user_id):
        query = update.callback_query
        files = self.github_data.get_user_files(user_id)
        
        if not files:
            query.edit_message_text("❌ No files to extract.")
            return
        
        for file_data in files:
            self.extract_and_send(update, context, user_id, file_data)
        
        query.edit_message_text("✅ All files extracted and sent!")

    def extract_and_send(self, update, context, user_id, file_data):
        query = update.callback_query
        file_name = file_data['name']
        
        query.edit_message_text(f"📦 Extracting {file_name}...\n\n{ProgressBar.circular(0)}")
        
        # Download from GitHub
        github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/user_files/{user_id}/{file_name}"
        response = requests.get(github_url)
        
        if response.status_code != 200:
            query.edit_message_text("❌ Could not download file")
            return
        
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        with open(temp_path, 'wb') as f:
            f.write(response.content)
        
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in ['.zip', '.rar', '.7z']:
            query.edit_message_text("❌ Not an archive file. Supported: ZIP, RAR, 7z")
            os.remove(temp_path)
            return
        
        try:
            extract_dir = os.path.join(TEMP_DIR, f"{user_id}_extracted")
            os.makedirs(extract_dir, exist_ok=True)
            
            password = self.github_data.get_user_field(user_id, 'archive_password') or None
            
            if ext == '.zip':
                with zipfile.ZipFile(temp_path, 'r') as zip_ref:
                    if password:
                        zip_ref.setpassword(password.encode())
                    total = len(zip_ref.namelist())
                    for i, name in enumerate(zip_ref.namelist()):
                        zip_ref.extract(name, extract_dir)
                        if i % 5 == 0:
                            progress = (i / total) * 100
                            query.edit_message_text(
                                f"📦 Extracting... {i+1}/{total}\n\n{ProgressBar.circular(progress)}"
                            )
                            
            elif ext == '.rar':
                with rarfile.RarFile(temp_path) as rar_ref:
                    if password:
                        rar_ref.setpassword(password)
                    total = len(rar_ref.namelist())
                    for i, name in enumerate(rar_ref.namelist()):
                        rar_ref.extract(name, extract_dir)
                        if i % 5 == 0:
                            progress = (i / total) * 100
                            query.edit_message_text(
                                f"📦 Extracting... {i+1}/{total}\n\n{ProgressBar.circular(progress)}"
                            )
                            
            elif ext == '.7z':
                with py7zr.SevenZipFile(temp_path, mode='r', password=password) as sz_ref:
                    files = sz_ref.getnames()
                    total = len(files)
                    for i, name in enumerate(files):
                        sz_ref.extract(targets=[name], path=extract_dir)
                        if i % 2 == 0:
                            progress = (i / total) * 100
                            query.edit_message_text(
                                f"📦 Extracting... {i+1}/{total}\n\n{ProgressBar.circular(progress)}"
                            )
            
            query.edit_message_text(f"✅ Extraction complete!\n\n{ProgressBar.circular(100)}")
            
            # Send extracted files
            extracted = []
            for root, dirs, files in os.walk(extract_dir):
                for f in files:
                    extracted.append(os.path.join(root, f))
            
            if extracted:
                context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"📤 Sending {len(extracted)} files..."
                )
                
                for f_path in extracted:
                    with open(f_path, 'rb') as doc:
                        context.bot.send_document(
                            chat_id=query.message.chat_id,
                            document=doc,
                            filename=os.path.basename(f_path)
                        )
            
            shutil.rmtree(extract_dir, ignore_errors=True)
            
        except Exception as e:
            query.edit_message_text(f"❌ Error: {str(e)}")
        
        os.remove(temp_path)
        
        # Delete from GitHub after sending
        self.github_data.delete_file_from_github(user_id, file_name)
        self.github_data.delete_user_file(user_id, file_data['id'])

    # ============================================
    # COMPRESS FUNCTIONS
    # ============================================
    def compress_single_file(self, update, context, user_id, file_id, format_type):
        query = update.callback_query
        file_data = self.github_data.get_file(user_id, file_id)
        
        if not file_data:
            query.edit_message_text("❌ File not found")
            return
        
        self.compress_and_send(update, context, user_id, file_data, format_type)

    def compress_all_files(self, update, context, user_id, format_type):
        query = update.callback_query
        files = self.github_data.get_user_files(user_id)
        
        if not files:
            query.edit_message_text("❌ No files to compress.")
            return
        
        for file_data in files:
            self.compress_and_send(update, context, user_id, file_data, format_type)
        
        query.edit_message_text("✅ All files compressed and sent!")

    def compress_and_send(self, update, context, user_id, file_data, format_type):
        query = update.callback_query
        file_name = file_data['name']
        
        query.edit_message_text(f"🗜️ Compressing {file_name}...\n\n{ProgressBar.circular(0)}")
        
        # Download from GitHub
        github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/user_files/{user_id}/{file_name}"
        response = requests.get(github_url)
        
        if response.status_code != 200:
            query.edit_message_text("❌ Could not download file")
            return
        
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        with open(temp_path, 'wb') as f:
            f.write(response.content)
        
        # Create archive
        base_name = os.path.splitext(file_name)[0]
        archive_name = f"{base_name}.{format_type}"
        archive_path = os.path.join(TEMP_DIR, f"{user_id}_{archive_name}")
        
        password = self.github_data.get_user_field(user_id, 'archive_password') or None
        
        try:
            if format_type == 'zip':
                with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    if password:
                        zipf.setpassword(password.encode())
                    zipf.write(temp_path, os.path.basename(file_name))
                    
            elif format_type == '7z':
                with py7zr.SevenZipFile(archive_path, 'w', password=password) as szf:
                    szf.write(temp_path, os.path.basename(file_name))
            
            with open(archive_path, 'rb') as doc:
                context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=archive_name
                )
            
            query.edit_message_text(f"✅ Compression complete!\n\n{ProgressBar.circular(100)}")
            
        except Exception as e:
            query.edit_message_text(f"❌ Error: {str(e)}")
        
        os.remove(temp_path)
        if os.path.exists(archive_path):
            os.remove(archive_path)
        
        # Delete from GitHub after sending
        self.github_data.delete_file_from_github(user_id, file_name)
        self.github_data.delete_user_file(user_id, file_data['id'])

    # ============================================
    # TEXT AND FILE HANDLERS
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
        
        session = self.user_sessions.get(user_id, {})
        step = session.get('step')
        
        if step == 'waiting_prefix':
            self.github_data.update_user(user_id, 'file_prefix', text)
            self.user_sessions.pop(user_id, None)
            update.message.reply_text(
                f"✅ Prefix set: <b>{text}</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        if step == 'waiting_password':
            self.github_data.update_user(user_id, 'archive_password', text)
            self.user_sessions.pop(user_id, None)
            update.message.reply_text(
                "✅ Password set!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        if step == 'waiting_rename':
            file_id = session.get('file_id')
            file_data = self.github_data.get_file(user_id, file_id)
            
            if not file_data:
                update.message.reply_text("❌ File not found")
                self.user_sessions.pop(user_id, None)
                return
            
            new_name = text
            old_name = file_data['name']
            
            msg = update.message.reply_text(
                f"✏️ Renaming...\n\n{old_name} → {new_name}\n{ProgressBar.circular(0)}",
                parse_mode=ParseMode.HTML
            )
            
            try:
                github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/user_files/{user_id}/{old_name}"
                response = requests.get(github_url)
                
                if response.status_code != 200:
                    msg.edit_text("❌ Could not download file")
                    self.user_sessions.pop(user_id, None)
                    return
                
                msg.edit_text(
                    f"✏️ Renaming...\n\n{old_name} → {new_name}\n{ProgressBar.circular(30)}",
                    parse_mode=ParseMode.HTML
                )
                
                content = response.content
                encoded = base64.b64encode(content).decode('utf-8')
                new_path = f"user_files/{user_id}/{new_name}"
                new_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{new_path}"
                headers = {
                    "Authorization": f"token {GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3+json"
                }
                
                sha = None
                try:
                    check_response = requests.get(new_url, headers=headers)
                    if check_response.status_code == 200:
                        sha = check_response.json().get('sha')
                except:
                    pass
                
                data = {
                    "message": f"Rename {old_name} to {new_name}",
                    "content": encoded,
                    "branch": GITHUB_BRANCH
                }
                if sha:
                    data["sha"] = sha
                
                upload_response = requests.put(new_url, headers=headers, json=data)
                
                if upload_response.status_code not in [200, 201]:
                    msg.edit_text("❌ Upload failed")
                    self.user_sessions.pop(user_id, None)
                    return
                
                msg.edit_text(
                    f"✏️ Renaming...\n\n{old_name} → {new_name}\n{ProgressBar.circular(80)}",
                    parse_mode=ParseMode.HTML
                )
                
                self.github_data.delete_file_from_github(user_id, old_name)
                self.github_data.delete_user_file(user_id, file_id)
                self.github_data.add_file(
                    secrets.token_hex(16),
                    user_id,
                    new_name,
                    file_data['size'],
                    file_data['file_id'],
                    new_url
                )
                
                msg.edit_text(
                    f"✏️ Renaming...\n\n{old_name} → {new_name}\n{ProgressBar.circular(90)}",
                    parse_mode=ParseMode.HTML
                )
                
                download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{new_path}"
                download_response = requests.get(download_url)
                
                if download_response.status_code == 200:
                    temp_path = os.path.join(TEMP_DIR, f"{user_id}_{new_name}")
                    with open(temp_path, 'wb') as f:
                        f.write(download_response.content)
                    
                    with open(temp_path, 'rb') as doc:
                        context.bot.send_document(
                            chat_id=update.message.chat_id,
                            document=doc,
                            filename=new_name,
                            caption=f"✅ Renamed: {old_name} → {new_name}"
                        )
                    
                    os.remove(temp_path)
                    
                    self.github_data.delete_file_from_github(user_id, new_name)
                    
                    files = self.github_data.get_user_files(user_id)
                    for f in files:
                        if f.get('name') == new_name:
                            self.github_data.delete_user_file(user_id, f['id'])
                            break
                    
                    msg.edit_text(
                        f"✅ Done!\n\n{old_name} → {new_name}\n{ProgressBar.circular(100)}",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    msg.edit_text("❌ Could not download renamed file")
                
            except Exception as e:
                msg.edit_text(f"❌ Error: {str(e)}")
            
            self.user_sessions.pop(user_id, None)
            return
        
        self.file_handler(update, context)

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
        
        session = self.user_sessions.get(user_id, {})
        if session.get('step') != 'waiting_file':
            msg.reply_text(
                "⚠️ Use 'Upload Files' button first.",
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
            msg.reply_text("❌ Send a document, photo, or video.")
            return
        
        if file_size > MAX_FILE_SIZE:
            msg.reply_text(f"❌ File too large ({self.format_size(file_size)})")
            return
        
        if 'files' not in session:
            session['files'] = []
        session['files'].append((file_id, file_name, file_size))
        self.user_sessions[user_id] = session
        
        file_list = ""
        for _, name, size in session['files']:
            file_list += f"• {name} ({self.format_size(size)})\n"
        
        kb = [
            [InlineKeyboardButton("✅ Done", callback_data="done_upload")],
            [InlineKeyboardButton("➕ Upload More", callback_data="upload")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
        ]
        
        msg.reply_text(
            f"✅ Uploaded!\n\n📄 Files ({len(session['files'])}):\n{file_list}",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

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
            logger.info(f'✅ Bot running: @{self.bot_username}')
            
            dp.add_handler(CommandHandler('start', self.start_command))
            dp.add_handler(MessageHandler(Filters.document, self.file_handler))
            dp.add_handler(MessageHandler(Filters.photo, self.photo_handler))
            dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.text_handler))
            dp.add_handler(CallbackQueryHandler(self.callback_handler))
            
            logger.info('✅ Bot is ready!')
            updater.start_polling()
            updater.idle()
            
        except Exception as e:
            logger.error(f'❌ Error: {e}')
            raise


if __name__ == '__main__':
    try:
        bot = ArchiveBot()
        bot.run()
    except KeyboardInterrupt:
        logger.info('🛑 Bot stopped')
    except Exception as e:
        logger.error(f'❌ Fatal error: {e}')
        sys.exit(1)