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

# Enable logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"  # Get from @BotFather only
MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2GB
TEMP_DIR = "temp_downloads"

# GitHub Configuration
GITHUB_TOKEN = "YOUR_GITHUB_TOKEN_HERE"  # Get from GitHub Settings -> Developer Settings -> Personal Access Tokens
GITHUB_REPO = "YOUR_USERNAME/YOUR_REPO_NAME"  # e.g., "username/repo"
GITHUB_BRANCH = "main"  # or "master"

# Force Join Configuration
FORCE_CHANNEL = "@NCK_Dev"  # Your channel username
FORCE_CHANNEL_ID = -1002583286874  # Your channel ID

# Create temp directory
os.makedirs(TEMP_DIR, exist_ok=True)

# User sessions storage
user_data = {}

class Progress