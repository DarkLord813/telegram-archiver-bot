#!/usr/bin/env python3
# ============================================
# TELEGRAM ARCHIVE BOT - WITH TOKEN TESTING
# ============================================

import os
import sys
import secrets
import logging
import shutil
import zipfile
import rarfile
import py7zr
import pyzipper
import time
import base64
import html as html_lib
import requests
import json
import threading
import traceback
import urllib.parse
from datetime import datetime
from typing import Optional, Dict, List

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
from dotenv import load_dotenv
from flask import Flask, request

load_dotenv()


def esc(text) -> str:
    """Escape user-controlled text before it goes into an HTML-parsed Telegram message."""
    return html_lib.escape(str(text), quote=False)

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

# GitHub's Contents API hard-caps files at 100MB, and base64 encoding inflates
# the upload payload by ~33%, so we stay well under that ceiling.
MAX_FILE_SIZE = 70 * 1024 * 1024  # 70MB
TEMP_DIR = os.getenv('TEMP_DIR', 'temp_downloads')
PORT = int(os.getenv('PORT', 8080))

os.makedirs(TEMP_DIR, exist_ok=True)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
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
        logger.info(f"📁 GitHub: {owner}/{repo}")
        
        # Test the token on initialization
        self.test_token()

    def test_token(self):
        """Test if the GitHub token has proper permissions"""
        try:
            url = f"https://api.github.com/repos/{self.owner}/{self.repo}"
            response = requests.get(url, headers=self.headers)
            logger.info(f"🔑 Token test: {response.status_code}")
            
            if response.status_code == 200:
                logger.info("✅ GitHub token is valid and has read access")
                return True
            elif response.status_code == 401:
                logger.error("❌ Invalid GitHub token! Please regenerate your token.")
                return False
            elif response.status_code == 403:
                logger.error("❌ GitHub token lacks permissions! Please add 'repo' permission.")
                return False
            elif response.status_code == 404:
                logger.error(f"❌ Repository {self.owner}/{self.repo} not found!")
                return False
            else:
                logger.error(f"❌ Unexpected response: {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"❌ Token test failed: {e}")
            return False

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
        except:
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

    def download_file_from_github(self, user_id: int, file_name: str, save_path: str) -> tuple:
        """Download a file from GitHub using the API with token.

        Files under 1MB come back as base64 JSON; the Contents API only
        returns raw bytes for files between 1-100MB if we explicitly ask
        for the .raw media type, so we always ask for raw and handle both
        JSON and raw-bytes responses.
        """
        try:
            encoded_name = urllib.parse.quote(file_name)
            path = f"user_files/{user_id}/{encoded_name}"
            url = f"{self.base_url}/{path}"

            raw_headers = dict(self.headers)
            raw_headers["Accept"] = "application/vnd.github.raw"

            logger.info(f"📥 Downloading: {url}")
            response = requests.get(url, headers=raw_headers)

            logger.info(f"📥 Status: {response.status_code}")

            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    # Small file: GitHub still wrapped it in JSON with base64 content
                    content = response.json()
                    if content.get('content'):
                        decoded = base64.b64decode(content['content'])
                    else:
                        return False, "No content in response"
                else:
                    # Raw bytes, for files between 1MB and 100MB
                    decoded = response.content

                with open(save_path, 'wb') as f:
                    f.write(decoded)
                logger.info(f"✅ Downloaded: {file_name} ({len(decoded)} bytes)")
                return True, "Success"
            elif response.status_code == 401:
                return False, "Invalid GitHub token - please regenerate your token"
            elif response.status_code == 403:
                return False, "GitHub token lacks permission - please add 'repo' permission"
            elif response.status_code == 404:
                return False, f"File not found on GitHub: {file_name}"
            else:
                return False, f"GitHub API error: {response.status_code}"
                
        except Exception as e:
            logger.error(f"Download error: {e}")
            return False, str(e)

    def upload_file_to_github(self, file_path: str, file_name: str, user_id: int, message: str = "") -> tuple:
        """Upload a file to GitHub using the API with token.

        The Contents API's create/update endpoint supports base64 bodies up
        to 100MB, so this works as-is as long as MAX_FILE_SIZE stays under
        that ceiling (accounting for base64 overhead).
        """
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            
            encoded = base64.b64encode(content).decode('utf-8')
            encoded_name = urllib.parse.quote(file_name)
            path = f"user_files/{user_id}/{encoded_name}"
            url = f"{self.base_url}/{path}"
            
            # Check if file exists
            sha = None
            try:
                check_response = requests.get(url, headers=self.headers)
                if check_response.status_code == 200:
                    sha = check_response.json().get('sha')
            except:
                pass
            
            data = {
                "message": message or f"Upload {file_name} by user {user_id}",
                "content": encoded,
                "branch": self.branch
            }
            if sha:
                data["sha"] = sha
            
            response = requests.put(url, headers=self.headers, json=data)
            
            if response.status_code in [200, 201]:
                logger.info(f"✅ Uploaded: {file_name}")
                return True, "Success"
            else:
                logger.error(f"❌ Upload failed: {response.text}")
                return False, f"Upload failed: {response.status_code}"
                
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False, str(e)

    def get_user(self, user_id: int) -> Optional[Dict]:
        return self._get_file_content(f"data/users/{user_id}.json")

    def create_user(self, user_id: int, username: str, first_name: str):
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
        data = self._get_file_content(f"data/sessions/{user_id}.json")
        return data if data else {}

    def save_session(self, user_id: int, session_data: dict):
        self._update_file(
            f"data/sessions/{user_id}.json",
            session_data,
            f"Save session for user {user_id}"
        )

    def delete_session(self, user_id: int):
        self._delete_file(
            f"data/sessions/{user_id}.json",
            f"Delete session for user {user_id}"
        )

    def add_file(self, file_id: str, user_id: int, name: str, size: int, telegram_file_id: str):
        user_files = self.get_user_files(user_id)
        
        file_data = {
            "id": file_id,
            "user_id": user_id,
            "name": name,
            "size": size,
            "file_id": telegram_file_id,
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
        data = self._get_file_content(f"data/files/{user_id}.json")
        if data and data.get('files'):
            return [f for f in data['files'] if f.get('is_active', 1) == 1]
        return []

    def get_file(self, user_id: int, file_id: str) -> Optional[Dict]:
        files = self.get_user_files(user_id)
        for f in files:
            if f.get('id') == file_id:
                logger.info(f"✅ Found file: {f.get('name')}")
                return f
        logger.warning(f"❌ File not found: {file_id}")
        return None

    def delete_user_file(self, user_id: int, file_id: str):
        user_files = self.get_user_files(user_id)
        for f in user_files:
            if f.get('id') == file_id:
                f['is_active'] = 0
                self._update_file(
                    f"data/files/{user_id}.json",
                    {"files": user_files},
                    f"Delete file {file_id} for user {user_id}"
                )
                return True
        return False

    def delete_file_from_github(self, user_id: int, file_name: str) -> bool:
        encoded_name = urllib.parse.quote(file_name)
        path = f"user_files/{user_id}/{encoded_name}"
        return self._delete_file(path, f"Delete {file_name} by user {user_id}")


# ============================================
# DIRECT CDN DOWNLOADER
# ============================================
class DirectCDNDownloader:
    @staticmethod
    def get_download_url(bot_token: str, file_id: str) -> Optional[str]:
        try:
            url = f"https://api.telegram.org/bot{bot_token}/getFile?file_id={file_id}"
            response = requests.get(url)
            response.raise_for_status()
            data = response.json()
            
            if data.get('ok') and data.get('result'):
                file_path = data['result']['file_path']
                return f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
            return None
        except Exception as e:
            logger.error(f"Error getting download URL: {e}")
            return None

    @staticmethod
    def download_file(bot_token: str, file_id: str, save_path: str, progress_callback=None) -> bool:
        try:
            download_url = DirectCDNDownloader.get_download_url(bot_token, file_id)
            if not download_url:
                return False
            
            if progress_callback:
                progress_callback(10, "Starting download...")
            
            response = requests.get(download_url, stream=True)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
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
            
            if progress_callback:
                progress_callback(90, "Download complete!")
            return True
            
        except Exception as e:
            logger.error(f"Download error: {e}")
            if os.path.exists(save_path):
                os.remove(save_path)
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
        
        self.clear_session(user_id)
        
        kb = [
            [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
            [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        update.message.reply_text(
            f"🌟 <b>Welcome {esc(user.first_name)}!</b>\n\n"
            f"📤 Upload files directly to GitHub (up to {self.format_size(MAX_FILE_SIZE)})\n"
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
            f"📝 <b>File Prefix:</b> {esc(prefix) if prefix else 'None'}\n"
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
            return
        
        data = query.data
        logger.info(f"📨 Callback: {data}")
        
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
                    "✅ Joined! Welcome!",
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode=ParseMode.HTML
                )
            else:
                query.edit_message_text(
                    f"🔒 Please join {FORCE_CHANNEL}",
                    reply_markup=self.get_force_join_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            return
        
        if data == "help":
            query.edit_message_text(
                "❓ <b>Help</b>\n\n"
                "📤 Upload Files\n"
                "📋 My Files\n"
                "⚙️ Settings\n\n"
                "📦 Extract: Unpack ZIP/RAR/7z\n"
                "🗜️ Compress: Create ZIP/7z\n"
                "✏️ Rename: Rename & send\n"
                "🗑️ Delete: Remove from storage\n\n"
                f"📢 Required: {FORCE_CHANNEL}",
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
                "📤 <b>Send file(s)</b>\n\nClick ✅ Done when finished.",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "done_upload":
            session = self.get_session(user_id)
            files = session.get('files', [])
            
            if not files:
                query.edit_message_text("❌ No files!")
                return
            
            query.edit_message_text(
                f"📤 Uploading {len(files)} files...\n\n{ProgressBar.circular(0)}",
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
                            f"📥 Downloading...\n{esc(file_name)}\n\n{ProgressBar.circular(overall)}",
                            parse_mode=ParseMode.HTML
                        )
                    except:
                        pass
                
                download_success = DirectCDNDownloader.download_file(
                    BOT_TOKEN, file_id, temp_path, download_progress
                )
                
                if not download_success:
                    continue
                
                success, result = self.github_data.upload_file_to_github(
                    temp_path, file_name, user_id, f"Upload {file_name} by user {user_id}"
                )
                
                if success:
                    unique_id = secrets.token_hex(16)
                    self.github_data.add_file(unique_id, user_id, file_name, file_size, file_id)
                    uploaded_count += 1
                
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            
            session['files'] = []
            self.save_session(user_id, session)
            
            kb = [
                [InlineKeyboardButton("📦 Extract All", callback_data="extract_all")],
                [InlineKeyboardButton("🗜️ Compress All", callback_data="compress_all")],
                [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
            ]
            
            query.edit_message_text(
                f"✅ {uploaded_count}/{total_files} uploaded!\n\nWhat now?",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "my_files":
            files = self.github_data.get_user_files(user_id)
            
            if not files:
                query.edit_message_text(
                    "📂 No files.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("📤 Upload", callback_data="upload")],
                        [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                    ])
                )
                return
            
            text = f"📂 <b>My Files</b> ({len(files)})\n\n"
            btns = []
            
            for f in files[:5]:
                text += f"📄 {esc(f['name'])}\n"
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
        
        # ---- DELETE ----
        if data.startswith("delete_"):
            file_id = data.replace("delete_", "")
            file_data = self.github_data.get_file(user_id, file_id)
            
            if file_data:
                self.github_data.delete_file_from_github(user_id, file_data['name'])
                self.github_data.delete_user_file(user_id, file_id)
            
            query.edit_message_text(
                "✅ Deleted!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
            )
            return
        
        # ---- COMPRESS ----
        if data.startswith("compress_single_zip_"):
            file_id = data.replace("compress_single_zip_", "")
            self.compress_single_with_format(update, context, user_id, file_id, "zip")
            return
        
        if data.startswith("compress_single_7z_"):
            file_id = data.replace("compress_single_7z_", "")
            self.compress_single_with_format(update, context, user_id, file_id, "7z")
            return
        
        if data.startswith("compress_all_"):
            format_type = data.replace("compress_all_", "")
            self.compress_all_with_format(update, context, user_id, format_type)
            return
        
        if data.startswith("compress_") and data != "compress_all":
            file_id = data.replace("compress_", "")
            self.compress_file(update, context, user_id, file_id)
            return
        
        if data == "compress_all":
            self.compress_all_files(update, context, user_id)
            return
        
        # ---- EXTRACT ----
        if data.startswith("extract_") and data != "extract_all":
            file_id = data.replace("extract_", "")
            self.extract_file(update, context, user_id, file_id)
            return
        
        if data == "extract_all":
            self.extract_all_files(update, context, user_id)
            return
        
        # ---- RENAME ----
        if data.startswith("rename_"):
            file_id = data.replace("rename_", "")
            session = self.get_session(user_id)
            session['step'] = 'waiting_rename'
            session['rename_file_id'] = file_id
            self.save_session(user_id, session)
            
            kb = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
            
            query.edit_message_text(
                f"✏️ Send new name:\nExample: <code>new_name.txt</code>",
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "cancel":
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
        
        kb = [
            [InlineKeyboardButton("📤 Upload Files", callback_data="upload")],
            [InlineKeyboardButton("📋 My Files", callback_data="my_files")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        query.edit_message_text(
            f"🌟 <b>Welcome {esc(name)}!</b>\n\nChoose an option:",
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
            msg.reply_text(f"❌ Too large ({self.format_size(file_size)})")
            return
        
        if 'files' not in session:
            session['files'] = []
        session['files'].append((file_id, file_name, file_size))
        self.save_session(user_id, session)
        
        file_list = ""
        for _, name, size in session['files']:
            file_list += f"• {esc(name)} ({self.format_size(size)})\n"
        
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
            self.github_data.update_user(user_id, 'file_prefix', text)
            self.clear_session(user_id)
            update.message.reply_text(
                f"✅ Prefix: <b>{esc(text)}</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        if session and session.get('step') == 'waiting_password':
            self.github_data.update_user(user_id, 'archive_password', text)
            self.clear_session(user_id)
            update.message.reply_text(
                "✅ Password set!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        # ---- RENAME ----
        if session and session.get('step') == 'waiting_rename':
            file_id = session.get('rename_file_id')
            file_data = self.github_data.get_file(user_id, file_id)
            
            if not file_data:
                update.message.reply_text("❌ File not found")
                self.clear_session(user_id)
                return
            
            new_name = text
            old_name = file_data['name']
            old_disp, new_disp = esc(old_name), esc(new_name)

            msg = update.message.reply_text(
                f"✏️ Renaming...\n\n{old_disp} → {new_disp}\n{ProgressBar.circular(0)}",
                parse_mode=ParseMode.HTML
            )
            
            try:
                # Download from GitHub using API
                temp_path = os.path.join(TEMP_DIR, f"{user_id}_{old_name}")
                success, result = self.github_data.download_file_from_github(user_id, old_name, temp_path)
                
                if not success:
                    msg.edit_text(f"❌ {result}")
                    self.clear_session(user_id)
                    return
                
                msg.edit_text(
                    f"✏️ Renaming...\n\n{old_disp} → {new_disp}\n{ProgressBar.circular(30)}",
                    parse_mode=ParseMode.HTML
                )
                
                # Upload with new name
                msg.edit_text(
                    f"✏️ Renaming...\n\n{old_disp} → {new_disp}\n{ProgressBar.circular(60)}",
                    parse_mode=ParseMode.HTML
                )
                
                success, result = self.github_data.upload_file_to_github(
                    temp_path, new_name, user_id, f"Rename {old_name} to {new_name}"
                )
                
                if not success:
                    msg.edit_text(f"❌ {result}")
                    self.clear_session(user_id)
                    os.remove(temp_path)
                    return
                
                # Delete old file
                self.github_data.delete_file_from_github(user_id, old_name)
                self.github_data.delete_user_file(user_id, file_id)
                self.github_data.add_file(
                    secrets.token_hex(16),
                    user_id,
                    new_name,
                    file_data['size'],
                    file_data['file_id']
                )
                
                # Send renamed file
                msg.edit_text(
                    f"✏️ Renaming...\n\n{old_disp} → {new_disp}\n{ProgressBar.circular(90)}",
                    parse_mode=ParseMode.HTML
                )
                
                # Download the renamed file and send to user
                success, result = self.github_data.download_file_from_github(user_id, new_name, temp_path)
                
                if success:
                    with open(temp_path, 'rb') as doc:
                        context.bot.send_document(
                            chat_id=update.message.chat_id,
                            document=doc,
                            filename=new_name,
                            caption=f"✅ Renamed: {old_name} → {new_name}"
                        )
                    
                    os.remove(temp_path)
                    
                    # Delete from GitHub after sending
                    self.github_data.delete_file_from_github(user_id, new_name)
                    
                    # Remove from database
                    files = self.github_data.get_user_files(user_id)
                    for f in files:
                        if f.get('name') == new_name:
                            self.github_data.delete_user_file(user_id, f['id'])
                            break
                    
                    msg.edit_text(
                        f"✅ Done!\n\n{old_disp} → {new_disp}\n{ProgressBar.circular(100)}",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    msg.edit_text(f"❌ {result}")
                    os.remove(temp_path)
                
            except Exception as e:
                msg.edit_text(f"❌ Error: {str(e)}")
            
            self.clear_session(user_id)
            return
        
        self.file_handler(update, context)

    # ============================================
    # PHOTO HANDLER
    # ============================================
    def photo_handler(self, update: Update, context: CallbackContext):
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        session = self.get_session(user_id)
        
        if session and session.get('step') == 'waiting_thumb':
            photo = update.message.photo[-1]
            file_obj = context.bot.get_file(photo.file_id)
            thumb_path = os.path.join(TEMP_DIR, f"{user_id}_thumb.jpg")
            file_obj.download(thumb_path)
            
            self.github_data.update_user(user_id, 'thumbnail_path', thumb_path)
            self.clear_session(user_id)
            
            update.message.reply_text(
                f"✅ Thumbnail set!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        self.file_handler(update, context)

    # ============================================
    # EXTRACT FUNCTIONS
    # ============================================
    def extract_and_send(self, update, context, user_id, file_data):
        query = update.callback_query
        file_name = file_data['name']
        
        query.edit_message_text(f"📦 Extracting {file_name}...\n\n{ProgressBar.circular(0)}")
        
        # Download from GitHub using API
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        success, result = self.github_data.download_file_from_github(user_id, file_name, temp_path)
        
        if not success:
            query.edit_message_text(f"❌ {result}")
            return
        
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
                # pyzipper transparently reads both plain and AES-encrypted zips
                with pyzipper.AESZipFile(temp_path, 'r') as zip_ref:
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

    def extract_file(self, update, context, user_id, file_id):
        file_data = self.github_data.get_file(user_id, file_id)
        if not file_data:
            update.callback_query.edit_message_text("❌ File not found")
            return
        self.extract_and_send(update, context, user_id, file_data)

    def extract_all_files(self, update, context, user_id):
        query = update.callback_query
        files = self.github_data.get_user_files(user_id)
        
        if not files:
            query.edit_message_text("❌ No files.")
            return
        
        for file_data in files:
            self.extract_and_send(update, context, user_id, file_data)
        
        query.edit_message_text("✅ All files extracted!")

    # ============================================
    # COMPRESS FUNCTIONS
    # ============================================
    def compress_and_send(self, update, context, user_id, file_data, format_type):
        query = update.callback_query
        file_name = file_data['name']
        
        query.edit_message_text(f"🗜️ Compressing {file_name} to {format_type.upper()}...\n\n{ProgressBar.circular(0)}")
        
        # Download from GitHub using API
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        success, result = self.github_data.download_file_from_github(user_id, file_name, temp_path)
        
        if not success:
            query.edit_message_text(f"❌ {result}")
            return
        
        # Create archive
        base_name = os.path.splitext(file_name)[0]
        archive_name = f"{base_name}.{format_type}"
        archive_path = os.path.join(TEMP_DIR, f"{user_id}_{archive_name}")
        
        password = self.github_data.get_user_field(user_id, 'archive_password') or None
        
        try:
            if format_type == 'zip':
                if password:
                    # Stdlib zipfile can only READ encrypted zips, not write them.
                    # pyzipper adds real AES-256 encryption for writing.
                    with pyzipper.AESZipFile(
                        archive_path, 'w',
                        compression=pyzipper.ZIP_DEFLATED,
                        encryption=pyzipper.WZ_AES
                    ) as zipf:
                        zipf.setpassword(password.encode())
                        zipf.write(temp_path, os.path.basename(file_name))
                else:
                    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
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
            
            query.edit_message_text(f"✅ Compression complete!\n\n{ProgressBar.circular(100)}")
            
        except Exception as e:
            query.edit_message_text(f"❌ Error: {str(e)}")
        
        os.remove(temp_path)
        if os.path.exists(archive_path):
            os.remove(archive_path)
        
        # Delete from GitHub after sending
        self.github_data.delete_file_from_github(user_id, file_name)
        self.github_data.delete_user_file(user_id, file_data['id'])

    def compress_single_with_format(self, update, context, user_id, file_id, format_type):
        file_data = self.github_data.get_file(user_id, file_id)
        if not file_data:
            update.callback_query.edit_message_text("❌ File not found")
            return
        self.compress_and_send(update, context, user_id, file_data, format_type)

    def compress_file(self, update, context, user_id, file_id):
        query = update.callback_query
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
            f"🗜️ <b>Compress: {esc(file_data['name'])}</b>\n\nChoose format:",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.HTML
        )

    def compress_all_with_format(self, update, context, user_id, format_type):
        query = update.callback_query
        files = self.github_data.get_user_files(user_id)
        
        if not files:
            query.edit_message_text("❌ No files.")
            return
        
        for file_data in files:
            self.compress_and_send(update, context, user_id, file_data, format_type)
        
        query.edit_message_text("✅ All files compressed!")

    def compress_all_files(self, update, context, user_id):
        query = update.callback_query
        
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

    # ============================================
    # RUN BOT
    # ============================================
    def run(self):
        logger.info('🚀 Starting Archive Bot...')
        logger.info(f'📁 Data stored in: {GITHUB_OWNER}/{GITHUB_REPO}')
        
        try:
            def run_health_server():
                health_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False, threaded=True)
            
            health_thread = threading.Thread(target=run_health_server, daemon=True)
            health_thread.start()
            logger.info(f'✅ Health check on port {PORT}')
            
            updater = Updater(BOT_TOKEN, use_context=True)
            dp = updater.dispatcher
            
            bot_info = updater.bot.get_me()
            self.bot_username = bot_info.username
            self.bot_id = bot_info.id
            logger.info(f'✅ Bot: @{self.bot_username}')
            
            updater.bot.set_my_commands([
                ('start', '🚀 Start'),
            ])
            
            dp.add_handler(CommandHandler('start', self.start_command))
            dp.add_handler(MessageHandler(Filters.document, self.file_handler))
            dp.add_handler(MessageHandler(Filters.photo, self.photo_handler))
            dp.add_handler(MessageHandler(Filters.text & ~Filters.command, self.text_handler))
            dp.add_handler(CallbackQueryHandler(self.callback_handler))
            
            logger.info('✅ Bot ready!')
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
        logger.info('🛑 Stopped')
    except Exception as e:
        logger.error(f'❌ Fatal: {e}')
        sys.exit(1)