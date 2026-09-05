#!/usr/bin/env python3
# ============================================
# TELEGRAM ARCHIVE BOT - FULLY FIXED
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
import requests
import json
import threading
import traceback
import urllib.parse
import re
import html
from datetime import datetime
from typing import Optional, Dict, List
from bs4 import BeautifulSoup

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler, MessageHandler, Filters, CallbackContext
from dotenv import load_dotenv
from flask import Flask, request

load_dotenv()

# ============================================
# ESCAPE FUNCTION
# ============================================
def esc(text) -> str:
    return html.escape(str(text), quote=False)


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

MAX_FILE_SIZE = 70 * 1024 * 1024  # 70MB — GitHub's Contents API hard-caps at 100MB,
                                    # and base64 encoding inflates the upload ~33%.
GITHUB_API_LIMIT = 100 * 1024 * 1024  # 100MB (GitHub's actual ceiling, kept for reference)
TEMP_DIR = os.getenv('TEMP_DIR', 'temp_downloads')
PORT = int(os.getenv('PORT', 8080))

os.makedirs(TEMP_DIR, exist_ok=True)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================
# FLASK HEALTH CHECK APP
# ============================================
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
        self.raw_base_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}"
        logger.info(f"📁 GitHub: {owner}/{repo}")

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

    def download_file_content(self, user_id: int, file_name: str) -> Optional[bytes]:
        """Download file content from GitHub"""
        try:
            encoded_name = urllib.parse.quote(file_name)

            # Raw CDN first (faster) — needs the auth header too, since the
            # repo is private and raw.githubusercontent.com 404s without one.
            raw_url = f"{self.raw_base_url}/user_files/{user_id}/{encoded_name}"
            logger.info(f"📥 Downloading from: {raw_url}")

            response = requests.get(raw_url, headers={"Authorization": f"token {self.token}"})
            if response.status_code == 200:
                logger.info(f"✅ Downloaded: {file_name} ({len(response.content)} bytes)")
                return response.content

            # Fall back to the Contents API. Files over 1MB only come back
            # as raw bytes if we explicitly request the .raw media type —
            # otherwise the 'content' field is empty for anything >1MB.
            path = f"user_files/{user_id}/{encoded_name}"
            url = f"{self.base_url}/{path}"
            logger.info(f"📥 Trying API: {url}")

            api_headers = dict(self.headers)
            api_headers["Accept"] = "application/vnd.github.raw"
            response = requests.get(url, headers=api_headers, params={"ref": self.branch})

            if response.status_code == 200:
                content_type = response.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    content_b64 = response.json().get("content", "")
                    if content_b64:
                        content = base64.b64decode(content_b64)
                        logger.info(f"✅ Downloaded via API: {file_name} ({len(content)} bytes)")
                        return content
                else:
                    logger.info(f"✅ Downloaded via API (raw): {file_name} ({len(response.content)} bytes)")
                    return response.content

            logger.error(f"❌ Could not download file: {file_name} (raw={response.status_code})")
            return None

        except Exception as e:
            logger.error(f"Download error: {e}")
            return None

    def upload_file_content(self, file_path: str, file_name: str, user_id: int, file_size: int) -> tuple:
        """Upload file to GitHub"""
        try:
            with open(file_path, 'rb') as f:
                content = f.read()
            
            encoded = base64.b64encode(content).decode('utf-8')
            encoded_name = urllib.parse.quote(file_name)
            path = f"user_files/{user_id}/{encoded_name}"
            url = f"{self.base_url}/{path}"
            
            sha = None
            try:
                check_response = requests.get(url, headers=self.headers)
                if check_response.status_code == 200:
                    sha = check_response.json().get('sha')
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
                logger.info(f"✅ Uploaded: {file_name}")
                return True, "Success"
            else:
                logger.error(f"❌ Upload failed: {response.text}")
                return False, f"Upload failed: {response.status_code}"
                
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False, str(e)

    def delete_file_content(self, user_id: int, file_name: str) -> bool:
        """Delete file from GitHub"""
        encoded_name = urllib.parse.quote(file_name)
        path = f"user_files/{user_id}/{encoded_name}"
        return self._delete_file(path, f"Delete {file_name} by user {user_id}")

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
# LINK DOWNLOAD SUPPORT
# ============================================
MEDIAFIRE_RE = re.compile(r"(https?://(www\.)?mediafire\.com/\S+)")
GDRIVE_RE = re.compile(r"(https?://(drive|docs)\.google\.com/\S+)")
LINK_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def resolve_mediafire_url(share_url: str) -> str:
    resp = requests.get(share_url, headers=LINK_HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    btn = soup.find(id="downloadButton")
    if btn and btn.get("href"):
        return btn["href"]

    match = re.search(r'href="(https://download\d+\.mediafire\.com/[^"]+)"', resp.text)
    if match:
        return match.group(1)

    raise ValueError("Couldn't find a download link on that Mediafire page.")


def download_from_mediafire(share_url: str, dest_dir: str, max_size: int = None) -> str:
    direct_url = resolve_mediafire_url(share_url)
    filename = direct_url.split("/")[-1].split("?")[0]
    dest_path = os.path.join(dest_dir, filename)

    with requests.get(direct_url, headers=LINK_HEADERS, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        if max_size:
            content_length = r.headers.get("Content-Length")
            if content_length and int(content_length) > max_size:
                raise ValueError(
                    f"File is {int(content_length) / (1024*1024):.1f}MB, "
                    f"over the {max_size / (1024*1024):.0f}MB limit."
                )
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if max_size and downloaded > max_size:
                    f.close()
                    os.remove(dest_path)
                    raise ValueError(f"File exceeds the {max_size / (1024*1024):.0f}MB limit.")

    return dest_path


def download_from_gdrive(share_url: str, dest_dir: str) -> str:
    import gdown
    patterns = [r"/file/d/([a-zA-Z0-9_-]+)", r"[?&]id=([a-zA-Z0-9_-]+)", r"/d/([a-zA-Z0-9_-]+)"]
    file_id = None
    for p in patterns:
        m = re.search(p, share_url)
        if m:
            file_id = m.group(1)
            break
    if not file_id:
        raise ValueError("Couldn't extract a file ID from that Google Drive link.")

    os.makedirs(dest_dir, exist_ok=True)
    output = gdown.download(id=file_id, output=dest_dir + "/", quiet=False)
    if not output:
        raise ValueError("Google Drive download failed — file may be private or restricted.")
    return output


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
        self.file_id_cache = {}

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
            [InlineKeyboardButton("🔗 Link Download", callback_data="link_download")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        update.message.reply_text(
            f"🌟 <b>Welcome {esc(user.first_name)}!</b>\n\n"
            f"📤 Upload files directly to GitHub (up to 2GB)\n"
            f"🔗 Download from Mediafire or Google Drive\n"
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
                    [InlineKeyboardButton("🔗 Link Download", callback_data="link_download")],
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
                "📤 <b>Upload Files</b>: Send files directly to GitHub\n"
                "🔗 <b>Link Download</b>: Download from Mediafire/Google Drive\n"
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
                f"📢 Required: {FORCE_CHANNEL}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        if data == "link_download":
            query.edit_message_text(
                "🔗 <b>Link Download</b>\n\n"
                "Send me a Mediafire or Google Drive link.\n\n"
                "📁 <b>Supported:</b>\n"
                "• Mediafire: https://www.mediafire.com/...\n"
                "• Google Drive: https://drive.google.com/...\n\n"
                "The file will be downloaded and added to your files.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ])
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
                
                self.file_id_cache[file_name] = file_id
                
                download_success = DirectCDNDownloader.download_file(
                    BOT_TOKEN, file_id, temp_path, download_progress
                )
                
                if not download_success:
                    continue
                
                actual_size = os.path.getsize(temp_path)
                logger.info(f"📊 File size: {self.format_size(actual_size)}")
                
                success, result = self.github_data.upload_file_content(
                    temp_path, file_name, user_id, actual_size
                )
                
                if success:
                    unique_id = secrets.token_hex(16)
                    self.github_data.add_file(unique_id, user_id, file_name, actual_size, file_id)
                    uploaded_count += 1
                    logger.info(f"✅ Uploaded: {file_name} ({self.format_size(actual_size)})")
                else:
                    logger.error(f"❌ Failed to upload: {file_name} - {result}")
                    query.edit_message_text(f"❌ Failed to upload {file_name}: {result}")
                
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
                        [InlineKeyboardButton("🔗 Link Download", callback_data="link_download")],
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
            btns.append([InlineKeyboardButton("🔗 Link Download", callback_data="link_download")])
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
                file_name = file_data['name']
                self.github_data.delete_file_content(user_id, file_name)
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
            [InlineKeyboardButton("🔗 Link Download", callback_data="link_download")],
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
            msg.reply_text(f"❌ Too large ({self.format_size(file_size)}). Max: 2GB")
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
        
        # ============================================
        # LINK DOWNLOAD - Mediafire / Google Drive
        # ============================================
        mediafire_match = MEDIAFIRE_RE.search(text)
        gdrive_match = GDRIVE_RE.search(text)

        if mediafire_match or gdrive_match:
            msg = update.message.reply_text(
                "🔗 <b>Link Detected!</b>\n\n"
                "📥 Downloading file...\n"
                "⏳ Please wait...",
                parse_mode=ParseMode.HTML
            )
            
            local_dir = os.path.join(TEMP_DIR, f"link_{user_id}_{int(time.time())}")
            os.makedirs(local_dir, exist_ok=True)

            try:
                if mediafire_match:
                    msg.edit_text(
                        "🔗 <b>Mediafire Link Detected</b>\n\n"
                        "📥 Downloading from Mediafire...\n"
                        "⏳ This may take a moment.",
                        parse_mode=ParseMode.HTML
                    )
                    local_path = download_from_mediafire(mediafire_match.group(1), local_dir, max_size=MAX_FILE_SIZE)
                else:
                    msg.edit_text(
                        "🔗 <b>Google Drive Link Detected</b>\n\n"
                        "📥 Downloading from Google Drive...\n"
                        "⏳ This may take a moment.",
                        parse_mode=ParseMode.HTML
                    )
                    local_path = download_from_gdrive(gdrive_match.group(1), local_dir)
            except Exception as e:
                logger.error(f"❌ Link download failed: {e}")
                msg.edit_text(f"❌ Couldn't download that link: {e}")
                shutil.rmtree(local_dir, ignore_errors=True)
                return

            file_name = os.path.basename(local_path)
            file_size = os.path.getsize(local_path)

            if file_size > MAX_FILE_SIZE:
                msg.edit_text(
                    f"❌ That file is {self.format_size(file_size)}, which is over the "
                    f"{self.format_size(MAX_FILE_SIZE)} limit GitHub storage can handle."
                )
                shutil.rmtree(local_dir, ignore_errors=True)
                return

            msg.edit_text(
                f"📤 <b>Uploading to GitHub...</b>\n\n"
                f"📄 {esc(file_name)}\n"
                f"📦 {self.format_size(file_size)}\n"
                f"⏳ Please wait...",
                parse_mode=ParseMode.HTML
            )

            success, result = self.github_data.upload_file_content(local_path, file_name, user_id, file_size)
            shutil.rmtree(local_dir, ignore_errors=True)

            if not success:
                msg.edit_text(f"❌ Couldn't save that file: {result}")
                return

            unique_id = secrets.token_hex(16)
            self.github_data.add_file(unique_id, user_id, file_name, file_size, "")

            # Show file with download button
            msg.edit_text(
                f"✅ <b>File Ready!</b>\n\n"
                f"📄 {esc(file_name)}\n"
                f"📦 {self.format_size(file_size)}\n"
                f"🔒 Stored on GitHub\n\n"
                f"<b>What would you like to do?</b>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📥 Download File", callback_data=f"download_{unique_id}")],
                    [InlineKeyboardButton("📂 My Files", callback_data="my_files")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        session = self.get_session(user_id)
        
        if session and session.get('step') == 'waiting_prefix':
            self.github_data.update_user(user_id, 'file_prefix', text)
            self.clear_session(user_id)
            update.message.reply_text(
                f"✅ Prefix set to: <b>{esc(text)}</b>",
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
                f"✅ Archive password set!",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
                    [InlineKeyboardButton("🔙 Back", callback_data="back_to_menu")]
                ]),
                parse_mode=ParseMode.HTML
            )
            return
        
        # ============================================
        # RENAME
        # ============================================
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
                f"✏️ <b>Renaming file...</b>\n\n"
                f"📄 {old_disp} → {new_disp}\n"
                f"{ProgressBar.circular(0)}",
                parse_mode=ParseMode.HTML
            )
            
            try:
                content = self.github_data.download_file_content(user_id, old_name)
                
                if content is None:
                    msg.edit_text(f"❌ Could not download file from GitHub")
                    self.clear_session(user_id)
                    return
                
                msg.edit_text(
                    f"✏️ <b>Renaming file...</b>\n\n"
                    f"📄 Downloading...\n"
                    f"{ProgressBar.circular(30)}",
                    parse_mode=ParseMode.HTML
                )
                
                encoded = base64.b64encode(content).decode('utf-8')
                encoded_new_name = urllib.parse.quote(new_name)
                new_path = f"user_files/{user_id}/{encoded_new_name}"
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
                    "message": f"Rename {old_name} to {new_name} by user {user_id}",
                    "content": encoded,
                    "branch": GITHUB_BRANCH
                }
                if sha:
                    data["sha"] = sha
                
                upload_response = requests.put(new_url, headers=headers, json=data)
                
                if upload_response.status_code not in [200, 201]:
                    msg.edit_text(f"❌ Upload failed")
                    self.clear_session(user_id)
                    return
                
                msg.edit_text(
                    f"✏️ <b>Renaming file...</b>\n\n"
                    f"📄 Deleting old file...\n"
                    f"{ProgressBar.circular(80)}",
                    parse_mode=ParseMode.HTML
                )
                
                self.github_data.delete_file_content(user_id, old_name)
                self.github_data.delete_user_file(user_id, file_id)
                self.github_data.add_file(
                    secrets.token_hex(16),
                    user_id,
                    new_name,
                    file_data['size'],
                    file_data['file_id']
                )
                
                msg.edit_text(
                    f"✏️ <b>Renaming file...</b>\n\n"
                    f"📄 Sending renamed file...\n"
                    f"{ProgressBar.circular(90)}",
                    parse_mode=ParseMode.HTML
                )
                
                content_bytes = self.github_data.download_file_content(user_id, new_name)
                
                if content_bytes is not None:
                    temp_path = os.path.join(TEMP_DIR, f"{user_id}_{new_name}")
                    with open(temp_path, 'wb') as f:
                        f.write(content_bytes)
                    
                    with open(temp_path, 'rb') as doc:
                        context.bot.send_document(
                            chat_id=update.message.chat_id,
                            document=doc,
                            filename=new_name,
                            caption=f"✅ <b>File renamed successfully!</b>\n\n📄 {old_disp} → {new_disp}",
                            parse_mode=ParseMode.HTML
                        )
                    
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                    
                    self.github_data.delete_file_content(user_id, new_name)
                    
                    files = self.github_data.get_user_files(user_id)
                    for f in files:
                        if f.get('name') == new_name:
                            self.github_data.delete_user_file(user_id, f['id'])
                            break
                    
                    msg.edit_text(
                        f"✅ <b>File renamed and sent!</b>\n\n"
                        f"📄 {old_disp} → {new_disp}\n"
                        f"🗑️ Deleted from GitHub after sending\n\n"
                        f"{ProgressBar.circular(100)}",
                        parse_mode=ParseMode.HTML
                    )
                else:
                    msg.edit_text("❌ Could not download renamed file")
                
            except Exception as e:
                logger.error(f"❌ Error during rename: {e}")
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
    # EXTRACT AND SEND
    # ============================================
    def extract_and_send(self, update, context, user_id, file_data):
        query = update.callback_query
        file_name = file_data['name']
        
        query.edit_message_text(f"📦 Extracting {esc(file_name)}...\n\n{ProgressBar.circular(0)}")
        
        content_bytes = self.github_data.download_file_content(user_id, file_name)
        
        if content_bytes is None:
            query.edit_message_text(f"❌ Could not download file from GitHub")
            return
        
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        with open(temp_path, 'wb') as f:
            f.write(content_bytes)
        
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in ['.zip', '.rar', '.7z']:
            query.edit_message_text("❌ Not an archive file. Supported: ZIP, RAR, 7z")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return
        
        try:
            extract_dir = os.path.join(TEMP_DIR, f"{user_id}_extracted")
            os.makedirs(extract_dir, exist_ok=True)
            
            password = self.github_data.get_user_field(user_id, 'archive_password') or None
            
            if ext == '.zip':
                # pyzipper reads both plain and AES-encrypted zips; stdlib
                # zipfile can't open the AES ones we now create for passwords.
                with pyzipper.AESZipFile(temp_path, 'r') as zip_ref:
                    if password:
                        zip_ref.setpassword(password.encode())
                    total = len(zip_ref.namelist())
                    for i, name in enumerate(zip_ref.namelist()):
                        zip_ref.extract(name, extract_dir)
                        if i % 5 == 0:
                            progress = (i / total) * 100 if total > 0 else 0
                            query.edit_message_text(
                                f"📦 Extracting {esc(file_name)}...\n\n{ProgressBar.circular(progress)}"
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
                                f"📦 Extracting {esc(file_name)}...\n\n{ProgressBar.circular(progress)}"
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
                                f"📦 Extracting {esc(file_name)}...\n\n{ProgressBar.circular(progress)}"
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
            query.edit_message_text(f"❌ Extraction error: {str(e)}")
        
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        self.github_data.delete_file_content(user_id, file_name)
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
    # COMPRESS AND SEND
    # ============================================
    def compress_and_send(self, update, context, user_id, file_data, format_type):
        query = update.callback_query
        file_name = file_data['name']
        
        query.edit_message_text(f"🗜️ Compressing {esc(file_name)} to {format_type.upper()}...\n\n{ProgressBar.circular(0)}")
        
        content_bytes = self.github_data.download_file_content(user_id, file_name)
        
        if content_bytes is None:
            query.edit_message_text(f"❌ Could not download file from GitHub")
            return
        
        temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_name}")
        with open(temp_path, 'wb') as f:
            f.write(content_bytes)
        
        base_name = os.path.splitext(file_name)[0]
        archive_name = f"{base_name}.{format_type}"
        archive_path = os.path.join(TEMP_DIR, f"{user_id}_{archive_name}")
        
        password = self.github_data.get_user_field(user_id, 'archive_password') or None
        
        try:
            if format_type == 'zip':
                if password:
                    with pyzipper.AESZipFile(
                        archive_path, 'w',
                        compression=pyzipper.ZIP_LZMA,
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
            
            prefix = self.github_data.get_user_field(user_id, 'file_prefix')
            thumb = self.github_data.get_user_field(user_id, 'thumbnail_path')
            if prefix:
                archive_name = f"{prefix}{archive_name}"
            
            with open(archive_path, 'rb') as doc:
                context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=archive_name,
                    thumbnail=open(thumb, 'rb') if thumb and os.path.exists(thumb) else None
                )
            
            query.edit_message_text(f"✅ Compression complete!\n\n{ProgressBar.circular(100)}")
            
        except Exception as e:
            logger.error(f"❌ Compression error: {e}")
            query.edit_message_text(f"❌ Compression error: {str(e)}")
        
        if os.path.exists(temp_path):
            os.remove(temp_path)
        if os.path.exists(archive_path):
            os.remove(archive_path)
        
        self.github_data.delete_file_content(user_id, file_name)
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
    # DOWNLOAD FILE HANDLER
    # ============================================
    def download_file_handler(self, update: Update, context: CallbackContext):
        query = update.callback_query
        user_id = self.get_user_id(update)
        if not user_id:
            return
        
        data = query.data
        if data.startswith("download_"):
            file_id = data.replace("download_", "")
            file_data = self.github_data.get_file(user_id, file_id)
            
            if not file_data:
                query.edit_message_text("❌ File not found")
                return
            
            query.edit_message_text(f"📥 Downloading {esc(file_data['name'])}...")
            
            content = self.github_data.download_file_content(user_id, file_data['name'])
            
            if content is None:
                query.edit_message_text("❌ Could not download file")
                return
            
            temp_path = os.path.join(TEMP_DIR, f"{user_id}_{file_data['name']}")
            with open(temp_path, 'wb') as f:
                f.write(content)
            
            with open(temp_path, 'rb') as doc:
                context.bot.send_document(
                    chat_id=query.message.chat_id,
                    document=doc,
                    filename=file_data['name']
                )
            
            os.remove(temp_path)
            query.edit_message_text("✅ File sent!")

    # ============================================
    # RUN BOT
    # ============================================
    def run(self):
        logger.info('🚀 Starting Archive Bot...')
        logger.info(f'📁 Data stored in: {GITHUB_OWNER}/{GITHUB_REPO}')
        logger.info(f'📦 Max file size: {self.format_size(MAX_FILE_SIZE)}')
        logger.info('🔗 Link support: Mediafire, Google Drive')
        
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
            dp.add_handler(CallbackQueryHandler(self.download_file_handler, pattern=r'^download_'))
            
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