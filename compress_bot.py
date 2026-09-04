#!/usr/bin/env python3
# ============================================
# TELEGRAM ARCHIVE BOT - WITH DEBUG LOGGING
# Compatible with Render.com Web Service
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
import threading
import traceback
from datetime import datetime
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
from dotenv import load_dotenv
from flask import Flask, request

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
PORT = int(os.getenv('PORT', 8080))

os.makedirs(TEMP_DIR, exist_ok=True)

# Configure detailed logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG  # Set to DEBUG for more details
)
logger = logging.getLogger(__name__)

# Create Flask app for health checks
health_app = Flask(__name__)

@health_app.route('/')
@health_app.route('/health')
@health_app.route('/health/')
@health_app.route('/health%20')
@health_app.route('/health%20/')
def health_check():
    return "OK", 200

@health_app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/health'):
        return "OK", 200
    return "Not Found", 404


# ============================================
# GITHUB DATA MANAGER WITH DEBUG LOGGING
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
        logger.info(f"📁 GitHub initialized: {owner}/{repo} (branch: {branch})")

    def _get_file_content(self, path: str) -> Optional[dict]:
        """Get file content from GitHub with debug logging"""
        try:
            url = f"{self.base_url}/{path}"
            logger.debug(f"📥 GitHub GET: {url}")
            response = requests.get(url, headers=self.headers)
            logger.debug(f"📥 Response status: {response.status_code}")
            
            if response.status_code == 200:
                content = response.json()
                if content.get('content'):
                    decoded = base64.b64decode(content['content']).decode('utf-8')
                    logger.debug(f"✅ Successfully fetched content from {path}")
                    return json.loads(decoded)
                else:
                    logger.warning(f"⚠️ No content in response from {path}")
            else:
                logger.warning(f"⚠️ Failed to get {path}: Status {response.status_code}")
                logger.debug(f"Response: {response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"❌ Error getting file content from {path}: {e}")
            logger.error(traceback.format_exc())
            return None

    def _update_file(self, path: str, data: dict, message: str) -> bool:
        """Update or create a file on GitHub with debug logging"""
        try:
            url = f"{self.base_url}/{path}"
            logger.debug(f"📤 GitHub PUT: {url}")
            
            content = json.dumps(data, indent=2)
            encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
            
            sha = None
            try:
                response = requests.get(url, headers=self.headers)
                if response.status_code == 200:
                    sha = response.json().get('sha')
                    logger.debug(f"📤 File exists, SHA: {sha[:8]}...")
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
            logger.debug(f"📤 PUT response: {response.status_code}")
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Successfully updated {path}")
                return True
            else:
                logger.error(f"❌ Failed to update {path}: {response.status_code}")
                logger.debug(f"Response: {response.text[:200]}")
                return False
        except Exception as e:
            logger.error(f"❌ Error updating file: {e}")
            logger.error(traceback.format_exc())
            return False

    def _delete_file(self, path: str, message: str) -> bool:
        """Delete a file from GitHub with debug logging"""
        try:
            url = f"{self.base_url}/{path}"
            logger.debug(f"🗑️ GitHub DELETE: {url}")
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                logger.warning(f"⚠️ File not found: {path}")
                return False
            
            sha = response.json().get('sha')
            delete_data = {
                "message": message,
                "sha": sha,
                "branch": self.branch
            }
            response = requests.delete(url, headers=self.headers, json=delete_data)
            logger.debug(f"🗑️ DELETE response: {response.status_code}")
            
            if response.status_code in [200, 204]:
                logger.info(f"✅ Successfully deleted {path}")
                return True
            else:
                logger.error(f"❌ Failed to delete {path}: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Error deleting file: {e}")
            logger.error(traceback.format_exc())
            return False

    def get_user(self, user_id: int) -> Optional[Dict]:
        logger.debug(f"👤 Getting user: {user_id}")
        return self._get_file_content(f"data/users/{user_id}.json")

    def create_user(self, user_id: int, username: str, first_name: str):
        logger.info(f"👤 Creating user: {user_id} ({username})")
        user_data = {
            "user_id": user_id,
            "username": username or '',
            "first_name": first_name,
            "file_prefix": "",
            "archive_password": "",
            "thumbnail_path": "",
            "created_at": datetime.now().isoformat()
        }
        self._update_file(
            f"data/users/{user_id}.json",
            user_data,
            f"Create user {user_id}"
        )

    def update_user(self, user_id: int, field: str, value: str):
        logger.debug(f"👤 Updating user {user_id}: {field}={value}")
        user_data = self.get_user(user_id)
        if user_data:
            user_data[field] = value
            self._update_file(
                f"data/users/{user_id}.json",
                user_data,
                f"Update user {user_id} - {field}"
            )

    def get_user_field(self, user_id: int, field: str) -> str:
        user_data = self.get_user(user_id)
        if user_data:
            return user_data.get(field, '')
        return ''

    def get_session(self, user_id: int) -> dict:
        logger.debug(f"📋 Getting session for user: {user_id}")
        data = self._get_file_content(f"data/sessions/{user_id}.json")
        return data if data else {}

    def save_session(self, user_id: int, session_data: dict):
        logger.debug(f"💾 Saving session for user: {user_id}")
        self._update_file(
            f"data/sessions/{user_id}.json",
            session_data,
            f"Save session for user {user_id}"
        )

    def delete_session(self, user_id: int):
        logger.debug(f"🗑️ Deleting session for user: {user_id}")
        self._delete_file(
            f"data/sessions/{user_id}.json",
            f"Delete session for user {user_id}"
        )

    def add_file(self, file_id: str, user_id: int, name: str, size: int, telegram_file_id: str, github_path: str):
        logger.info(f"📄 Adding file: {name} (ID: {file_id}) for user {user_id}")
        logger.debug(f"📄 File details: size={size}, telegram_file_id={telegram_file_id[:20]}...")
        
        user_files = self.get_user_files(user_id)
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
        user_files.append(file_data)
        self._update_file(
            f"data/files/{user_id}.json",
            {"files": user_files},
            f"Add file {name} for user {user_id}"
        )
        logger.info(f"✅ File added: {name} (ID: {file_id})")

    def get_user_files(self, user_id: int) -> List[Dict]:
        logger.debug(f"📋 Getting files for user: {user_id}")
        data = self._get_file_content(f"data/files/{user_id}.json")
        if data and data.get('files'):
            active_files = [f for f in data['files'] if f.get('is_active', 1) == 1]
            logger.debug(f"📋 Found {len(active_files)} active files for user {user_id}")
            return active_files
        logger.debug(f"📋 No files found for user {user_id}")
        return []

    def get_file(self, user_id: int, file_id: str) -> Optional[Dict]:
        logger.debug(f"🔍 Looking for file: {file_id} for user {user_id}")
        files = self.get_user_files(user_id)
        for f in files:
            if f.get('id') == file_id:
                logger.info(f"✅ Found file: {f.get('name')} (ID: {file_id})")
                logger.debug(f"📄 File data: {json.dumps(f, indent=2)}")
                return f
        logger.warning(f"❌ File not found: {file_id} for user {user_id}")
        logger.debug(f"📋 Available files: {[f.get('id') for f in files]}")
        return None

    def delete_user_file(self, user_id: int, file_id: str):
        logger.info(f"🗑️ Deleting file: {file_id} for user {user_id}")
        user_files = self.get_user_files(user_id)
        for f in user_files:
            if f.get('id') == file_id:
                f['is_active'] = 0
                self._update_file(
                    f"data/files/{user_id}.json",
                    {"files": user_files},
                    f"Delete file {file_id} for user {user_id}"
                )
                logger.info(f"✅ File deleted: {f.get('name')} (ID: {file_id})")
                return True
        logger.warning(f"❌ File not found to delete: {file_id}")
        return False

    def delete_file_from_github(self, user_id: int, file_name: str) -> bool:
        logger.info(f"🗑️ Deleting file from GitHub: {file_name} for user {user_id}")
        path = f"user_files/{user_id}/{file_name}"
        return self._delete_file(path, f"Delete {file_name} by user {user_id}")


# ============================================
# DIRECT CDN DOWNLOADER WITH DEBUG LOGGING
# ============================================
class DirectCDNDownloader:
    @staticmethod
    def get_download_url(bot_token: str, file_id: str) -> Optional[str]:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
            logger.debug(f"🌐 Getting download URL: {url[:50]}...")
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            if data.get('ok') and data.get('result'):
                file_path = data['result']['file_path']
                download_url = f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
                logger.debug(f"🌐 Download URL: {download_url[:80]}...")
                return download_url
            else:
                logger.error(f"❌ Failed to get file info: {data}")
                return None
        except Exception as e:
            logger.error(f"❌ Error getting download URL: {e}")
            logger.error(traceback.format_exc())
            return None

    @staticmethod
    def download_file(bot_token: str, file_id: str, save_path: str, progress_callback=None) -> bool:
        try:
            download_url = DirectCDNDownloader.get_download_url(bot_token, file_id)
            if not download_url:
                logger.error(f"❌ No download URL for file: {file_id}")
                return False
            
            logger.info(f"📥 Downloading file: {file_id} to {save_path}")
            if progress_callback:
                progress_callback(10, "Starting download...")
            
            response = requests.get(download_url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            logger.info(f"📥 File size: {total_size} bytes")
            downloaded = 0
            last_progress = 0
            
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=131072):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            progress = 10 + (downloaded / total_size) * 80
                            if int(progress) > last_progress + 2:
                                last_progress = int(progress)
                                progress_callback(progress, f"Downloading... {int(progress)}%")
            
            logger.info(f"✅ Download complete: {save_path}")
            if progress_callback:
                progress_callback(90, "Download complete!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Download error: {e}")
            logger.error(traceback.format_exc())
            if os.path.exists(save_path):
                os.remove(save_path)
            return False


# ============================================
# FAST GITHUB UPLOADER WITH DEBUG LOGGING
# ============================================
class FastGitHubUploader:
    @staticmethod
    def upload_file(file_path: str, file_name: str, user_id: int, progress_callback=None) -> tuple:
        try:
            logger.info(f"📤 Uploading file: {file_name} for user {user_id}")
            
            if progress_callback:
                progress_callback(10, "Reading file...")
            
            with open(file_path, 'rb') as f:
                content = f.read()
            
            logger.debug(f"📤 File size: {len(content)} bytes")
            encoded = base64.b64encode(content).decode('utf-8')
            
            if progress_callback:
                progress_callback(50, "Uploading to GitHub...")
            
            github_path = f"user_files/{user_id}/{file_name}"
            github_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"
            
            headers = {
                "Authorization": f"token {GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            sha = None
            try:
                check_response = requests.get(github_url, headers=headers)
                if check_response.status_code == 200:
                    sha = check_response.json().get('sha')
                    logger.debug(f"📤 File exists, SHA: {sha[:8]}...")
            except:
                pass
            
            data = {
                "message": f"Upload {file_name} by user {user_id}",
                "content": encoded,
                "branch": GITHUB_BRANCH
            }
            if sha:
                data["sha"] = sha
            
            upload_response = requests.put(github_url, headers=headers, json=data)
            logger.debug(f"📤 Upload response: {upload_response.status_code}")
            
            if upload_response.status_code in [200, 201]:
                if progress_callback:
                    progress_callback(100, "Upload complete!")
                logger.info(f"✅ Upload successful: {file_name}")
                return True, f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{github_path}"
            else:
                logger.error(f"❌ Upload failed: {upload_response.text}")
                return False, f"GitHub upload failed: {upload_response.text}"
                
        except Exception as e:
            logger.error(f"❌ Upload error: {e}")
            logger.error(traceback.format_exc())
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
# BOT HANDLERS WITH DEBUG LOGGING
# ============================================
class ArchiveBot:
    def __init__(self):
        logger.debug("🤖 Initializing ArchiveBot...")
        self.github_data = GitHubDataManager(GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO, GITHUB_BRANCH)
        self.bot_username = ''
        self.bot_id = 0
        self.session_cache = {}
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

    def get_session(self, user_id: int) -> dict:
        if user_id in self.session_cache:
            return self.session_cache[user_id]
        session = self.github_data.get_session(user_id)
        if session:
            self.session_cache[user_id] = session
            return session
        return {}

    def save_session(self, user_id: int, session_data: dict):
        self.session_cache[user_id] = session_data
        self.github_data.save_session(user_id, session_data)

    def clear_session(self, user_id: int):
        if user_id in self.session_cache:
            del self.session_cache[user_id]
        self.github_data.delete_session(user_id)

    # ============================================
    # START COMMAND
    # ============================================
    def start_command(self, update: Update, context: CallbackContext):
        user_id = self.get_user_id(update)
        if not user_id:
            logger.warning("⚠️ No user_id found in update")
            return
        
        logger.info(f"🚀 /start from user: {user_id}")
        
        if not self.check_force_join(context, user_id):
            logger.warning(f"🔒 User {user_id} not joined force channel")
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
            logger.info(f"👤 Creating new user: {user_id}")
            self.github_data.create_user(user_id, user.username or '', user.first_name or 'User')
        
        self.clear_session(user_id)
        
        kb = [
            [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
            [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        update.message.reply_text(
            f"🌟 <b>Welcome {user.first_name}!</b>\n\n"
            f"📤 Upload files directly to GitHub (up to 2GB)\n"
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
        
        logger.debug(f"⚙️ Settings menu for user: {user_id}")
        
        prefix = self.github_data.get_user_field(user_id, 'file_prefix')
        password = self.github_data.get_user_field(user_id, 'archive_password')
        thumb = self.github_data.get_user_field(user_id, 'thumbnail_path')
        
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
        
        logger.debug(f"📝 Setting prefix for user: {user_id}")
        
        session = self.get_session(user_id)
        session['step'] = 'waiting_prefix'
        self.save_session(user_id, session)
        
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        
        query.edit_message_text(
            "📝 <b>Set File Prefix</b>\n\n"
            "Send your desired prefix in the chat.\n"
            "Example: <code>MY_FILE_</code>",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    def handle_set_password(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        logger.debug(f"🔑 Setting password for user: {user_id}")
        
        session = self.get_session(user_id)
        session['step'] = 'waiting_password'
        self.save_session(user_id, session)
        
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        
        query.edit_message_text(
            "🔑 <b>Set Archive Password</b>\n\n"
            "Send your desired password in the chat.\n"
            "Example: <code>mysecret123</code>\n\n"
            "This password will be used for all archives you create.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    def handle_set_thumb(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        logger.debug(f"🖼️ Setting thumbnail for user: {user_id}")
        
        session = self.get_session(user_id)
        session['step'] = 'waiting_thumb'
        self.save_session(user_id, session)
        
        kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
        
        query.edit_message_text(
            "🖼️ <b>Set Thumbnail</b>\n\n"
            "Send a photo to use as thumbnail.\n\n"
            "📸 <b>Supported:</b> JPG, PNG, WEBP\n"
            "📏 <b>Recommended:</b> 320x320 pixels",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    def handle_remove_thumb(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        logger.debug(f"🗑️ Removing thumbnail for user: {user_id}")
        
        self.github_data.update_user(user_id, 'thumbnail_path', '')
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
            logger.warning("⚠️ No user_id in callback")
            return
        
        data = query.data
        logger.info(f"📨 Callback: {data} from user {user_id}")
        
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
                "✏️ Rename: Rename & send files\n"
                "🗑️ Delete: Remove from storage\n\n"
                f"📢 Required Channel: {FORCE_CHANNEL}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "back_to_menu":
            self.show_main_menu(update, context, user_id)
            return
        
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
        
        if data == "upload":
            session = self.get_session(user_id)
            session['step'] = 'waiting_file'
            if 'files' not in session:
                session['files'] = []
            self.save_session(user_id, session)
            
            kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
            
            query.edit_message_text(
                "📤 <b>Upload Files</b>\n\n"
                "Send any file(s) you want to store on GitHub.\n"
                "You can send multiple files.\n\n"
                "After uploading all files, click <b>✅ Done</b>.\n"
                "📦 <b>Max file size:</b> 2GB",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "done_upload":
            session = self.get_session(user_id)
            files = session.get('files', [])
            
            if not files:
                logger.warning(f"⚠️ No files to upload for user {user_id}")
                query.edit_message_text(
                    "❌ No files uploaded yet!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                    ])
                )
                return
            
            logger.info(f"📤 Uploading {len(files)} files for user {user_id}")
            
            query.edit_message_text(
                f"📤 <b>Uploading {len(files)} files to GitHub...</b>\n\n"
                f"{ProgressBar.circular(0)}\n\n"
                f"<i>Please wait...</i>",
                parse_mode=ParseMode.HTML
            )
            
            uploaded_count = 0
            total_files = len(files)
            
            for i, (file_id, file_name, file_size) in enumerate(files):
                temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
                
                def download_progress(progress, message):
                    overall = ((i + (progress / 100)) / total_files) * 100
                    try:
                        query.edit_message_text(
                            f"📥 <b>Downloading...</b>\n\n"
                            f"📄 {file_name}\n"
                            f"{ProgressBar.circular(overall)}\n\n"
                            f"<i>{message}</i>",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                
                logger.info(f"📥 Downloading file {file_name} (ID: {file_id})")
                download_success = DirectCDNDownloader.download_file(
                    BOT_TOKEN, 
                    file_id, 
                    temp_path,
                    download_progress
                )
                
                if not download_success:
                    logger.error(f"❌ Failed to download {file_name}")
                    query.edit_message_text(f"❌ Failed to download {file_name}")
                    continue
                
                def upload_progress(progress, message):
                    overall = ((i + 0.6 + (progress / 100 * 0.4)) / total_files) * 100
                    try:
                        query.edit_message_text(
                            f"📤 <b>Uploading to GitHub...</b>\n\n"
                            f"📄 {file_name}\n"
                            f"{ProgressBar.circular(overall)}\n\n"
                            f"<i>{message}</i>",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                
                success, result = FastGitHubUploader.upload_file(
                    temp_path, file_name, user_id, upload_progress
                )
                
                if success:
                    unique_id = secrets.token_hex(16)
                    self.github_data.add_file(unique_id, user_id, file_name, file_size, file_id, result)
                    uploaded_count += 1
                    logger.info(f"✅ Uploaded: {file_name}")
                else:
                    logger.error(f"❌ Failed to upload {file_name}: {result}")
                    query.edit_message_text(f"❌ Failed to upload {file_name}: {result}")
                
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            
            session['files'] = []
            self.save_session(user_id, session)
            
            logger.info(f"✅ Upload complete: {uploaded_count}/{total_files} files")
            
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
        
        if data == "my_files":
            files = self.github_data.get_user_files(user_id)
            
            if not files:
                logger.info(f"📂 No files for user {user_id}")
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
            
            logger.info(f"📂 Showing {len(files)} files for user {user_id}")
            
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
            logger.info(f"🗑️ Delete file: {file_id} for user {user_id}")
            file_data = self.github_data.get_file(user_id, file_id)
            
            if file_data:
                logger.info(f"🗑️ Deleting {file_data['name']} from GitHub")
                self.github_data.delete_file_from_github(user_id, file_data['name'])
                self.github_data.delete_user_file(user_id, file_id)
                logger.info(f"✅ File deleted: {file_data['name']}")
            else:
                logger.warning(f"❌ File not found for deletion: {file_id}")
            
            query.edit_message_text(
                "✅ File deleted!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )
            return
        
        # ---- COMPRESS SINGLE WITH FORMAT ----
        if data.startswith("compress_single_zip_"):
            file_id = data.replace("compress_single_zip_", "")
            logger.info(f"🗜️ Compress single file to ZIP: {file_id}")
            self.compress_single_with_format(update, context, user_id, file_id, "zip")
            return
        
        if data.startswith("compress_single_7z_"):
            file_id = data.replace("compress_single_7z_", "")
            logger.info(f"🗜️ Compress single file to 7Z: {file_id}")
            self.compress_single_with_format(update, context, user_id, file_id, "7z")
            return
        
        # ---- COMPRESS ALL WITH FORMAT ----
        if data.startswith("compress_all_"):
            format_type = data.replace("compress_all_", "")
            logger.info(f"🗜️ Compress all files to {format_type.upper()}")
            self.compress_all_with_format(update, context, user_id, format_type)
            return
        
        # ---- EXTRACT SINGLE FILE ----
        if data.startswith("extract_") and data != "extract_all":
            file_id = data.replace("extract_", "")
            logger.info(f"📦 Extract file: {file_id}")
            self.extract_file(update, context, user_id, file_id)
            return
        
        # ---- EXTRACT ALL FILES ----
        if data == "extract_all":
            logger.info(f"📦 Extract all files for user {user_id}")
            self.extract_all_files(update, context, user_id)
            return
        
        # ---- COMPRESS SINGLE FILE ----
        if data.startswith("compress_") and data != "compress_all":
            file_id = data.replace("compress_", "")
            logger.info(f"🗜️ Compress file: {file_id}")
            self.compress_file(update, context, user_id, file_id)
            return
        
        # ---- COMPRESS ALL FILES ----
        if data == "compress_all":
            logger.info(f"🗜️ Compress all files for user {user_id}")
            self.compress_all_files(update, context, user_id)
            return
        
        # ---- RENAME FILE ----
        if data.startswith("rename_"):
            file_id = data.replace("rename_", "")
            logger.info(f"✏️ Rename file: {file_id}")
            session = self.get_session(user_id)
            session['step'] = 'waiting_rename'
            session['rename_file_id'] = file_id
            self.save_session(user_id, session)
            
            kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
            
            query.edit_message_text(
                f"✏️ <b>Rename File</b>\n\n"
                f"Send the new name for this file.\n"
                f"Example: <code>new_name.txt</code>\n\n"
                f"<i>The renamed file will be sent to you and deleted from GitHub.</i>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "cancel":
            logger.info(f"❌ Cancelled operation for user {user_id}")
            self.clear_session(user_id)
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
        user = self.github_data.get_user(user_id)
        name = user['first_name'] if user else 'User'
        
        logger.debug(f"📋 Showing main menu for user {user_id}")
        
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
        
        session = self.get_session(user_id)
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
        
        logger.info(f"📄 File received: {file_name} ({self.format_size(file_size)}) for user {user_id}")
        
        if file_size > MAX_FILE_SIZE:
            logger.warning(f"⚠️ File too large: {file_name} ({file_size} bytes)")
            msg.reply_text(f"❌ File too large ({self.format_size(file_size)}). Max: 2GB")
            return
        
        if 'files' not in session:
            session['files'] = []
        session['files'].append((file_id, file_name, file_size))
        self.save_session(user_id, session)
        
        file_list = ""
        for _, name, size in session['files']:
            file_list += f"• {name} ({self.format_size(size)})\n"
        
        kb = [
            [InlineKeyboardButton("✅ Done", callback_data="done_upload")],
            [InlineKeyboardButton("➕ Upload More", callback_data="upload")],
            [InlineKeyboardButton("❌ Cancel", callback_data="cancel")]
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
        
        session = self.get_session(user_id)
        
        if session and session.get('step') == 'waiting_prefix':
            logger.info(f"📝 Setting prefix for user {user_id}: {text}")
            self.github_data.update_user(user_id, 'file_prefix', text)
            self.clear_session(user_id)
            update.message.reply_text(
                f"✅ Prefix set to: <b>{text}</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        if session and session.get('step') == 'waiting_password':
            logger.info(f"🔑 Setting password for user {user_id}")
            self.github_data.update_user(user_id, 'archive_password', text)
            self.clear_session(user_id)
            update.message.reply_text(
                f"✅ Archive password set!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        # ============================================
        # RENAME - Download from GitHub, Rename, Send, Delete
        # ============================================
        if session and session.get('step') == 'waiting_rename':
            file_id = session.get('rename_file_id')
            logger.info(f"✏️ Renaming file {file_id} to {text}")
            
            file_data = self.github_data.get_file(user_id, file_id)
            
            if not file_data:
                logger.error(f"❌ File not found for rename: {file_id}")
                update.message.reply_text("❌ File not found")
                self.clear_session(user_id)
                return
            
            new_name = text
            old_name = file_data['name']
            
            msg = update.message.reply_text(
                f"✏️ <b>Renaming file...</b>\n\n"
                f"📄 {old_name} → {new_name}\n"
                f"{ProgressBar.circular(0)}",
                parse_mode=ParseMode.HTML
            )
            
            try:
                # Download from GitHub
                github_path = f"user_files/{user_id}/{old_name}"
                github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{github_path}"
                logger.info(f"📥 Downloading from GitHub: {github_url}")
                response = requests.get(github_url)
                logger.debug(f"📥 Download response: {response.status_code}")
                
                if response.status_code != 200:
                    logger.error(f"❌ Could not download file from GitHub: {response.status_code}")
                    msg.edit_text("❌ Could not download file from GitHub")
                    self.clear_session(user_id)
                    return
                
                msg.edit_text(
                    f"✏️ <b>Renaming file...</b>\n\n"
                    f"📄 Downloading...\n"
                    f"{ProgressBar.circular(30)}",
                    parse_mode=ParseMode.HTML
                )
                
                content = response.content
                logger.debug(f"📥 Downloaded {len(content)} bytes")
                
                # Upload with new name
                msg.edit_text(
                    f"✏️ <b>Renaming file...</b>\n\n"
                    f"📄 Uploading renamed file...\n"
                    f"{ProgressBar.circular(60)}",
                    parse_mode=ParseMode.HTML
                )
                
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
                        logger.debug(f"📤 File exists, SHA: {sha[:8]}...")
                except:
                    pass
                
                data = {
                    "message": f"Rename {old_name} to {new_name} by user {user_id}",
                    "content": encoded,
                    "branch": GITHUB_BRANCH
                }
                if sha:
                    data["sha"] = sha
                
                upload_response = requests.put(new_url, headers=headers, json=data)
                logger.debug(f"📤 Upload response: {upload_response.status_code}")
                
                if upload_response.status_code not in [200, 201]:
                    logger.error(f"❌ Upload failed: {upload_response.text}")
                    msg.edit_text(f"❌ Upload failed: {upload_response.text}")
                    self.clear_session(user_id)
                    return
                
                # Delete old file
                msg.edit_text(
                    f"✏️ <b>Renaming file...</b>\n\n"
                    f"📄 Deleting old file...\n"
                    f"{ProgressBar.circular(80)}",
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
                
                # Send renamed file
                msg.edit_text(
                    f"✏️ <b>Renaming file...</b>\n\n"
                    f"📄 Sending renamed file...\n"
                    f"{ProgressBar.circular(90)}",
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
                            caption=f"✅ <b>File renamed successfully!</b>\n\n📄 {old_name} → {new_name}",
                            parse_mode=ParseMode.HTML
                        )
                    
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    
                    # Delete from GitHub after sending
                    msg.edit_text(
                        f"✏️ <b>Renaming file...</b>\n\n"
                        f"📄 Deleting from GitHub...\n"
                        f"{ProgressBar.circular(95)}",
                        parse_mode=ParseMode.HTML
                    )
                    
                    self.github_data.delete_file_from_github(user_id, new_name)
                    
                    # Remove from database
                    files = self.github_data.get_user_files(user_id)
                    for f in files:
                        if f.get('name') == new_name:
                            self.github_data.delete_user_file(user_id, f['id'])
                            break
                    
                    logger.info(f"✅ Rename complete: {old_name} → {new_name}")
                    msg.edit_text(
                        f"✅ <b>File renamed and sent!</b>\n\n"
                        f"📄 {old_name} → {new_name}\n"
                        f"🗑️ Deleted from GitHub after sending\n\n"
                        f"{ProgressBar.circular(100)}",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    logger.error(f"❌ Could not download renamed file: {download_response.status_code}")
                    msg.edit_text("❌ Could not download renamed file")
                
            except Exception as e:
                logger.error(f"❌ Error during rename: {e}")
                logger.error(traceback.format_exc())
                msg.edit_text(f"❌ Error during rename: {str(e)}")
            
            self.clear_session(user_id)
            return
        
        self.file_handler(update, context)

    # ============================================
    # PHOTO HANDLER (for thumbnail)
    # ============================================
    def photo_handler(self, update: Update, context: CallbackContext):
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        session = self.get_session(user_id)
        
        if session and session.get('step') == 'waiting_thumb':
            photo = update.message.photo[-1]
            logger.info(f"🖼️ Setting thumbnail for user {user_id}")
            
            file_obj = context.bot.get_file(photo.file_id)
            thumb_path = os.path.join(TEMP_DIR, f"{user_id}_thumb.jpg")
            file_obj.download(thumb_path)
            
            self.github_data.update_user(user_id, 'thumbnail_path', thumb_path)
            self.clear_session(user_id)
            
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
        
        self.file_handler(update, context)

    # ============================================
    # EXTRACT AND SEND - DELETE AFTER SEND
    # ============================================
    def extract_and_send(self, update, context, user_id, file_data):
        query = update.callback_query
        file_name = file_data['name']
        
        logger.info(f"📦 Extracting file: {file_name} for user {user_id}")
        
        query.edit_message_text(f"📦 Extracting {file_name}...\n\n{ProgressBar.circular(0)}")
        
        # Download from GitHub
        github_path = f"user_files/{user_id}/{file_name}"
        github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{github_path}"
        logger.info(f"📥 Downloading from GitHub: {github_url}")
        
        response = requests.get(github_url)
        logger.debug(f"📥 Download response: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"❌ Could not download file from GitHub: {response.status_code}")
            query.edit_message_text("❌ Could not download file from GitHub")
            return
        
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        with open(temp_path, 'wb') as f:
            f.write(response.content)
        
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in ['.zip', '.rar', '.7z']:
            logger.warning(f"⚠️ Not an archive file: {file_name}")
            query.edit_message_text("❌ Not an archive file. Supported: ZIP, RAR, 7z")
            if os.path.exists(temp_path):
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
                            progress = (i / total) * 100 if total > 0 else 0
                            query.edit_message_text(
                                f"📦 Extracting {file_name}...\n\n{ProgressBar.circular(progress)}"
                            )
                            
            elif ext == '.rar':
                with rarfile.RarFile(temp_path) as rar_ref:
                    if password:
                        rar_ref.setpassword(password)
                    total = len(rar_ref.namelist())
                    for i, name in enumerate(rar_ref.namelist()):
                        rar_ref.extract(name, extract_dir)
                        if i % 5 == 0:
                            progress = (i / total) * 100 if total > 0 else 0
                            query.edit_message_text(
                                f"📦 Extracting {file_name}...\n\n{ProgressBar.circular(progress)}"
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
                                f"📦 Extracting {file_name}...\n\n{ProgressBar.circular(progress)}"
                            )
            
            logger.info(f"✅ Extraction complete: {file_name}")
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
                
                thumb = self.github_data.get_user_field(user_id, 'thumbnail_path')
                prefix = self.github_data.get_user_field(user_id, 'file_prefix')
                
                for f_path in extracted:
                    if os.path.getsize(f_path) < MAX_FILE_SIZE:
                        file_name_out = os.path.basename(f_path)
                        if prefix:
                            file_name_out = f"{prefix}{file_name_out}"
                        
                        with open(f_path, 'rb') as doc:
                            context.bot.send_document(
                                chat_id=query.message.chat_id,
                                document=doc,
                                filename=file_name_out,
                                thumbnail=open(thumb, 'rb') if thumb and os.path.exists(thumb) else None
                            )
            
            shutil.rmtree(extract_dir, ignore_errors=True)
            
        except Exception as e:
            logger.error(f"❌ Extraction error: {e}")
            logger.error(traceback.format_exc())
            query.edit_message_text(f"❌ Extraction error: {str(e)}")
        
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        # DELETE FILE FROM GITHUB AFTER SENDING
        logger.info(f"🗑️ Deleting {file_name} from GitHub after sending")
        self.github_data.delete_file_from_github(user_id, file_name)
        self.github_data.delete_user_file(user_id, file_data['id'])

    # ============================================
    # EXTRACT SINGLE FILE
    # ============================================
    def extract_file(self, update, context, user_id, file_id):
        file_data = self.github_data.get_file(user_id, file_id)
        if not file_data:
            logger.error(f"❌ File not found for extraction: {file_id}")
            update.callback_query.edit_message_text("❌ File not found")
            return
        self.extract_and_send(update, context, user_id, file_data)

    # ============================================
    # EXTRACT ALL FILES
    # ============================================
    def extract_all_files(self, update, context, user_id):
        query = update.callback_query
        files = self.github_data.get_user_files(user_id)
        
        if not files:
            logger.warning(f"⚠️ No files to extract for user {user_id}")
            query.edit_message_text("❌ No files to extract.")
            return
        
        logger.info(f"📦 Extracting {len(files)} files for user {user_id}")
        
        for file_data in files:
            self.extract_and_send(update, context, user_id, file_data)
        
        query.edit_message_text("✅ All files extracted and sent!")

    # ============================================
    # COMPRESS AND SEND - DELETE AFTER SEND
    # ============================================
    def compress_and_send(self, update, context, user_id, file_data, format_type):
        query = update.callback_query
        file_name = file_data['name']
        
        logger.info(f"🗜️ Compressing file: {file_name} to {format_type.upper()} for user {user_id}")
        
        query.edit_message_text(f"🗜️ Compressing {file_name} to {format_type.upper()}...\n\n{ProgressBar.circular(0)}")
        
        # Download from GitHub
        github_path = f"user_files/{user_id}/{file_name}"
        github_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{github_path}"
        logger.info(f"📥 Downloading from GitHub: {github_url}")
        
        response = requests.get(github_url)
        logger.debug(f"📥 Download response: {response.status_code}")
        
        if response.status_code != 200:
            logger.error(f"❌ Could not download file from GitHub: {response.status_code}")
            query.edit_message_text("❌ Could not download file from GitHub")
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
            
            # Send compressed file
            prefix = self.github_data.get_user_field(user_id, 'file_prefix')
            if prefix:
                archive_name = f"{prefix}{archive_name}"
            
            with open(archive_path, 'rb') as doc:
                context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=archive_name
                )
            
            logger.info(f"✅ Compression complete: {file_name} → {archive_name}")
            query.edit_message_text(f"✅ Compression complete!\n\n{ProgressBar.circular(100)}")
            
        except Exception as e:
            logger.error(f"❌ Compression error: {e}")
            logger.error(traceback.format_exc())
            query.edit_message_text(f"❌ Compression error: {str(e)}")
        
        # Cleanup temp files
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if os.path.exists(archive_path):
            os.remove(archive_path)
        
        # DELETE ORIGINAL FILE FROM GITHUB AFTER SENDING
        logger.info(f"🗑️ Deleting {file_name} from GitHub after compression")
        self.github_data.delete_file_from_github(user_id, file_name)
        self.github_data.delete_user_file(user_id, file_data['id'])

    # ============================================
    # COMPRESS SINGLE WITH FORMAT
    # ============================================
    def compress_single_with_format(self, update, context, user_id, file_id, format_type):
        file_data = self.github_data.get_file(user_id, file_id)
        if not file_data:
            logger.error(f"❌ File not found for compression: {file_id}")
            update.callback_query.edit_message_text("❌ File not found")
            return
        self.compress_and_send(update, context, user_id, file_data, format_type)

    # ============================================
    # COMPRESS SINGLE FILE
    # ============================================
    def compress_file(self, update, context, user_id, file_id):
        query = update.callback_query
        file_data = self.github_data.get_file(user_id, file_id)
        
        if not file_data:
            logger.error(f"❌ File not found for compression: {file_id}")
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
    # COMPRESS ALL WITH FORMAT
    # ============================================
    def compress_all_with_format(self, update, context, user_id, format_type):
        query = update.callback_query
        files = self.github_data.get_user_files(user_id)
        
        if not files:
            logger.warning(f"⚠️ No files to compress for user {user_id}")
            query.edit_message_text("❌ No files to compress.")
            return
        
        logger.info(f"🗜️ Compressing {len(files)} files to {format_type.upper()} for user {user_id}")
        
        for file_data in files:
            self.compress_and_send(update, context, user_id, file_data, format_type)
        
        query.edit_message_text("✅ All files compressed and sent!")

    # ============================================
    # COMPRESS ALL FILES
    # ============================================
    def compress_all_files(self, update, context, user_id):
        query = update.callback_query
        
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
    # RUN BOT
    # ============================================
    def run(self):
        logger.info('🚀 Starting Archive Bot with CDN streaming...')
        logger.info(f'📁 Data stored in: {GITHUB_OWNER}/{GITHUB_REPO}')
        logger.info('📦 Max file size: 2GB (using CDN streaming)')
        logger.info('🔑 No API ID/Hash required - uses bot token only')
        
        try:
            def run_health_server():
                health_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False, threaded=True)
            
            health_thread = threading.Thread(target=run_health_server, daemon=True)
            health_thread.start()
            logger.info(f'✅ Health check server running on port {PORT}')
            
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
            logger.error(traceback.format_exc())
            raise


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
        logger.error(traceback.format_exc())
        sys.exit(1)