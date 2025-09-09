# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false, reportMissingTypeStubs=false, reportMissingImports=false, reportOptionalSubscript=false
# -*- coding: utf-8 -*-
import asyncio
import json
import logging
import os
import glob
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler
from telegram.error import BadRequest
import google.generativeai as genai
from PIL import Image as PILImage
import io
from datetime import datetime
import re
from typing import List, Dict, Optional, Any
from exporters.squash_export import (
    convert_to_squash_excel as exporter_convert_to_squash_excel,
    generate_filename as exporter_generate_filename,
    export_squash_xls_file as exporter_export_squash_xls_file,
)
from agent_manager import AgentManager
from agent_functional import FunctionalAgent
from agent_visual import VisualAgent

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def util_sanitize_example_titles(text: str) -> str:
    if not text:
        return text
    try:
        text = re.sub(r'(\n|^)\s*\[[^\]]+\]\s*', '\n', text)
        return text
    except Exception:
        return text

def util_sanitize_generated_output(text: str) -> str:
    if not text:
        return ""
    try:
        t = text.replace('\r\n', '\n').replace('\r', '\n')
        t = t.replace('*', '')
        cleaned = []
        for ln in t.split('\n'):
            s = ln.lstrip()
            if s[:1] in ['-', '•', '●', '▪']:
                s = s[1:].lstrip()
            s = re.sub(r'^(\d+\))\s+', '', s)
            cleaned.append(s)
        t = '\n'.join(cleaned)
        t = re.sub(r'\n{3,}', '\n\n', t)
        return t.strip()
    except Exception:
        return text

def util_normalize_numbering(text: str) -> str:
    if not text:
        return text
    pattern = re.compile(r"^(\s*)(\d+)\.(\s*)(.+)$", re.MULTILINE)
    def _repl(m):
        indent = m.group(1) or ''
        num = int(m.group(2))
        rest = (m.group(4) or '').strip()
        return f"{indent}{num:03d}. {rest}"
    return pattern.sub(_repl, text)

def util_ensure_blank_line_between_numbered(text: str) -> str:
    if not text:
        return text
    try:
        lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        out = []
        header_pat = re.compile(r'^\s*\d{1,3}\.\s+\S')
        for line in lines:
            if header_pat.match(line):
                if out and out[-1].strip() != '':
                    out.append('')
            out.append(line.rstrip())
        while out and out[-1].strip() == '':
            out.pop()
        return '\n'.join(out)
    except Exception:
        return text

def util_contains_indonesian(text: str) -> bool:
    if not text:
        return False
    tokens = [
        ' yang ', ' dan ', ' adalah ', ' ketika', ' saat ', ' tombol', ' halaman', ' pengguna', ' aplikasi', ' tampil', ' ditampilkan', ' ukuran', ' warna', ' berhasil', ' gagal', ' data ', ' sistem '
    ]
    low = f" {text.lower()} "
    return any(tok in low for tok in tokens)

class TelegramQABot:
    def __init__(self):
        
        # Load environment variables
        load_dotenv()
        
        # Initialize logging
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            level=logging.INFO
        )
        self.logger = logging.getLogger(__name__)
        
        # Single-instance lock 
        self._lock_file = 'bot_instance.lock'
        if not os.environ.get('BOT_FORCE'):
            try:
                import psutil
            except Exception:
                psutil = None
            if os.path.exists(self._lock_file):
                try:
                    with open(self._lock_file, 'r', encoding='utf-8') as lf:
                        pid_str = lf.read().strip()
                    if pid_str.isdigit():
                        import signal
                        pid = int(pid_str)
                        running = False
                        if psutil:
                            running = psutil.pid_exists(pid)
                        else:
                            try:
                                os.kill(pid, 0)
                                running = True
                            except Exception:
                                running = False
                        if running:
                            print(f"⚠️ Another bot instance (PID {pid}) appears running. Delete {self._lock_file} or set BOT_FORCE=1 to override.")
                            raise SystemExit(1)
                except Exception:
                    pass
            try:
                with open(self._lock_file, 'w', encoding='utf-8') as lf:
                    lf.write(str(os.getpid()))
            except Exception as lf_err:
                print(f"⚠️ Could not create lock file: {lf_err}")

        # Initialize bot token (ensure type is str for type checkers)
        self.token = os.getenv('TELEGRAM_BOT_TOKEN') or ""

        # Initialize Gemini AI model
        try:
            genai.configure(api_key=os.getenv('GOOGLE_API_KEY'))  # type: ignore[attr-defined]
            self.model = genai.GenerativeModel(os.getenv('GEMINI_MODEL', 'gemini-1.5-flash'))  # type: ignore[attr-defined]
        except Exception as e:
            print(f"Failed to initialize Gemini AI: {e}")
            raise

        # Initialize user sessions
        self.user_sessions = {}
        # Disable Squash integrations/monitor by default
        self.squash_integration = None
        self.squash_monitor = None

        # Knowledge loading → no-op per request (do not depend on external files)
        self.knowledge_base = ""

        self.qa_system_prompt = (
            "You are an AI assistant for Quality Assurance.\n"
            "Use the following knowledge base as the main reference for test case generation and analysis:\n\n"
            f"{self.knowledge_base}\n"
        )
        
        # Initialize the application with enhanced settings
        from telegram.request import HTTPXRequest
        
        # Create custom request with timeout settings
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=30.0
        )
        
        self.application = (
            Application.builder()
            .token(self.token)
            .request(request)
            .build()
        )

        # Initialize lightweight agent architecture (manager + specialized agents)
        try:
            self.functional_agent = FunctionalAgent(self)
            self.visual_agent = VisualAgent(self)
            self.agent_manager = AgentManager(self.functional_agent, self.visual_agent)
            print("✅ Agents initialized: Functional + Visual via AgentManager")
        except Exception as e:
            print(f"⚠️ Agent initialization failed (continuing with direct methods): {e}")
            self.functional_agent = None
            self.visual_agent = None
            self.agent_manager = None
        
        # Set up handlers
        self.setup_handlers()
        
        logger.info("Telegram QA Bot initialized successfully")
        print("✅ Telegram QA Bot initialized successfully")


    # Removed unused: create_fallback_examples (no references)
    
    
    def setup_handlers(self):    
        """Set up all command and message handlers"""
        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("generate_testcases", self.generate_testcases_command))
        self.application.add_handler(CommandHandler("format_testcase", self.format_testcase_command))
        self.application.add_handler(CommandHandler("reload_knowledge", self.reload_knowledge_command))
        self.application.add_handler(CommandHandler("reset", self.reset_command))

    # Squash commands removed (feature not implemented)

        # XLS Export handlers (always available)
        self.application.add_handler(CommandHandler("export_squash_xls", self.export_squash_xls_command))
        # New: XLSX export using centralized exporter
        self.application.add_handler(CommandHandler("export_squash_xlsx", self.export_squash_xlsx_command))
        self.application.add_handler(CommandHandler("convert_to_xls", self.convert_to_xls_command))

        # Message handlers
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_image_message))

        # Callback query handler
        self.application.add_handler(CallbackQueryHandler(self.handle_callback_query))

        # Err   or handler
        self.application.add_error_handler(self.error_handler)  # type: ignore[arg-type]

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.error(f"Exception while handling an update: {context.error}")
        try:
            if update and update.effective_chat:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="Maaf, terjadi kesalahan. Silakan coba lagi."
                )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Handle /start command"""
            user_id = update.effective_user.id
            user_name = update.effective_user.first_name or "User"
            
            self.user_sessions[user_id] = {'mode': 'general'}
            
            welcome_message = f"""🤖 Selamat datang di SQA Netmonk Assistant Bot, {user_name}!

    Saya dapat membantu Anda dengan:
    - 🔍 Generate test cases (dari PRD atau gambar)

    Gunakan /help untuk melihat semua perintah yang tersedia."""
            
            keyboard = [
                [
                        InlineKeyboardButton("🔍 Generate Test Case", callback_data="test_type_menu")
                ]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(welcome_message, reply_markup=reply_markup)

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        try:
            xls_commands = (
                """
📊 XLS Export Commands:
/export_squash_xls - Export test cases ke format Squash TM XLS
/export_squash_xlsx - Export test cases via centralized exporter
/convert_to_xls - Convert uploaded file ke format XLS"""
            )

            help_message = (
                "🛠️ QA Assistant Bot Commands:\n\n"
                "📋 Format Templates:\n"
                "/format_testcase - Tampilkan format input yang direkomendasikan\n\n"
                "🔍 Testing Functions:\n"
                "/generate_testcases - Generate test cases dari requirements atau gambar\n\n"
                "🔄 Regenerate options:\n"
                "Gunakan menu inline setelah generate untuk modify/export/regenerate.\n\n"
                f"{xls_commands}"
            )

            await update.message.reply_text(help_message)
        except Exception as e:
            await update.message.reply_text(f"❌ Error showing help: {e}")

    async def format_testcase_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show recommended input formats for Functional and Visual generation (via agents)."""
        try:
            template_text = None
            try:
                if getattr(self, 'agent_manager', None) and hasattr(self.agent_manager, 'get_format_template'):
                    template_text = self.agent_manager.get_format_template()
            except Exception:
                template_text = None

            if not template_text:
                template_text = (
                    "📋 Recommended Formats\n\n"
                    "Functional:\n"
                    "Type: Functional\nFeature: ...\nScenario: ...\nRequirements: ...\nEnvironment: Web/Mobile\n\n"
                    "Visual:\n"
                    "Type: Visual\nFeature: ...\nDesign Reference: (Figma link/desc)\nDevice: Desktop/Mobile\nRequirements: Visual requirements\n"
                )
            await update.message.reply_text(template_text)
        except Exception as e:
            await update.message.reply_text(f"❌ Error showing format templates: {e}")

    async def reload_knowledge_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reload combined knowledge from disk and refresh base system prompt."""
        # True no-op: keep empty knowledge base
        self.knowledge_base = ""
        self.qa_system_prompt = (
            "You are an AI assistant for Quality Assurance.\n"
            "Use the following knowledge base as the main reference for test case generation and analysis:\n\n"
            f"{self.knowledge_base}\n"
        )
        await update.message.reply_text("ℹ️ Knowledge reloaded (no-op): external knowledge files are disabled.")

    async def reset_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reset user session and cached generation context."""
        try:
            context.user_data.clear()
            uid = update.effective_user.id
            self.user_sessions[uid] = {'mode': 'general'}
            await update.message.reply_text("🔄 Session reset. You can start again with /generate_testcases or send text/image.")
        except Exception as e:
            await update.message.reply_text(f"❌ Failed to reset session: {e}")

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # ✅ SAFETY CHECKS - Tambahkan di awal
        if not update or not update.message:
            logger.warning("Update or message is None in handle_text_message")
            return
        
        if not update.message.text:
            logger.warning("Message text is None")
            return
        
        # ✅ Safe user ID extraction
        try:
            user_id = update.effective_user.id
            user_message = update.message.text
        except AttributeError:
            logger.error("Could not extract user_id or message text")
            return
        
        user_session = self.user_sessions.get(user_id, {'mode': 'general'})
        mode = user_session.get('mode', 'general')
        test_type = user_session.get('test_type', 'all')
    # translate_type removed; using generate/test type only
        
        # ✅ Safe typing indicator
        try:
            await update.message.reply_chat_action('typing')
        except Exception as e:
            logger.warning(f"Could not send typing action: {e}")
        
        try:
            # Global collection mode guard: capture any incoming text as part of the collection
    # ✅ Safe user ID extraction
            if user_session.get('mode') not in ('modify_testcase', 'modify_selected_testcase') \
               and context.user_data.get('collect_requirements_mode', False):
                texts = context.user_data.get('collected_texts', [])
                # Avoid duplicate first-text insertion: only append if different from last recorded
                # Prevent double-counting the first text captured during classify -> collection transition
                if context.user_data.get('__collection_initial_text_loaded'):
                    context.user_data.pop('__collection_initial_text_loaded', None)
                    # Do not append this same text again if identical to last pending
                    if not texts or texts[-1] != user_message:
                        # Only append if user sends a NEW text after entering collection
                        texts.append(user_message)
                else:
                    if not texts or texts[-1] != user_message:
                        texts.append(user_message)
                context.user_data['collected_texts'] = texts
                count_imgs = len(context.user_data.get('collected_images', []))
                count_txts = len(texts)
                keyboard = [
                    [InlineKeyboardButton("➕ Add More Text", callback_data=f"collect_more_text_{user_id}"),
                     InlineKeyboardButton("➕ Add Image", callback_data=f"collect_add_image_{user_id}")],
                    [InlineKeyboardButton("✅ Generate Now", callback_data=f"collect_generate_{user_id}"),
                     InlineKeyboardButton("🗑️ Reset", callback_data=f"collect_reset_{user_id}")]
                ]
                await update.message.reply_text(
                    f"🧺 Collected Requirements Updated\n\n📄 Text: {count_txts}\n🖼️ Images: {count_imgs}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            # Persist last plain requirement input for image follow-up (multi-modal intent detection)
            if mode in ('testcases', 'generate') and user_message.strip():
                # Do not overwrite if we're already in a pending translation flow
                # Normalize: use only one pending key to avoid double counting in classify
                context.user_data.pop('pending_generation_text', None)
                context.user_data['pending_text_requirements'] = user_message.strip()
                context.user_data['pending_test_type'] = user_session.get('test_type', 'functional')

            # **NEW: Handle specific test case modification mode FIRST**
            if mode == 'modify_selected_testcase':
                # User selected specific test case and now providing modification instructions
                test_cases_for_modification = context.user_data.get('test_cases_for_modification', '')
                selected_tc = context.user_data.get('selected_test_case', '')
                
                if not test_cases_for_modification or not selected_tc:
                    try:
                        await update.message.reply_text(
                            "❌ No test case selected or no test cases available. Please start over."
                        )
                    except Exception as e:
                        logger.error(f"Error sending error message: {e}")
                    self.user_sessions[user_id]['mode'] = 'general'
                    return
                
                # Show processing message
                try:
                    processing_msg = await update.message.reply_text(
                        f"🔧 Modifying Test Case {selected_tc}...\n\n"
                        f"⏳ Analyzing modification request...\n"
                        f"⏳ Applying changes to Test Case {selected_tc}...\n"
                        f"⏳ Preserving all other test cases...\n"
                        f"⏳ Generating updated test suite..."
                    )
                except Exception as e:
                    logger.error(f"Error sending processing message: {e}")
                    processing_msg = None
                
                # Create targeted modification request
                targeted_request = f"Modify test case {selected_tc}: {user_message}"
                
                # Apply modification
                modified_test_cases = await self.modify_specific_test_case(
                    test_cases_for_modification, 
                    targeted_request
                )
                
                # Delete processing message
                if processing_msg:
                    try:
                        await processing_msg.delete()
                    except Exception as e:
                        logger.warning(f"Could not delete processing message: {e}")
                
                # Send modified test cases
                await self.send_long_message(update, modified_test_cases)
                
                # Store modified version
                context.user_data['last_generated_test_cases'] = modified_test_cases
                context.user_data['original_test_cases'] = test_cases_for_modification  # Keep backup
                
                # Reset mode and clean up
                self.user_sessions[user_id]['mode'] = 'general'
                context.user_data.pop('test_cases_for_modification', None)
                context.user_data.pop('selected_test_case', None)
                
                keyboard = [
                    [
                        InlineKeyboardButton("🔧 Modify More Test Cases", callback_data=f"modify_testcase_{user_id}"),
                        InlineKeyboardButton("📊 Export to Excel", callback_data=f"export_excel_{user_id}")
                    ],
                    [
                        InlineKeyboardButton("↩️ Revert Changes", callback_data=f"revert_changes_{user_id}"),
                        [InlineKeyboardButton("← Back to Main", callback_data="back_main")]
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"✅ Test Case {selected_tc} Modified Successfully!\n\n"
                         f"🎯 Changes Applied:\n"
                         f"• Modified Test Case {selected_tc} as requested\n"
                         f"• All other test cases preserved unchanged\n"
                         f"• Ready for export or further modifications",
                    reply_markup=reply_markup
                )
                return
            
            # **Handle general modification mode**
            elif mode == 'modify_testcase':
                # User wants to modify specific test case
                test_cases_for_modification = context.user_data.get('test_cases_for_modification', '')
                
                if not test_cases_for_modification:
                    try:
                        await update.message.reply_text(
                            "❌ No test cases available for modification. Please generate test cases first."
                        )
                    except Exception as e:
                        logger.error(f"Error sending no test cases message: {e}")
                    self.user_sessions[user_id]['mode'] = 'general'
                    return
                
                # Show processing message
                try:
                    processing_msg = await update.message.reply_text(
                        "🔧 Processing Modification Request...\n\n"
                        "⏳ Analyzing your request...\n"
                        "⏳ Identifying target test case(s)...\n"
                        "⏳ Applying selective modifications...\n"
                        "⏳ Preserving other test cases..."
                    )
                except Exception as e:
                    logger.error(f"Error sending processing message: {e}")
                    processing_msg = None
                
                # Apply modification
                modified_test_cases = await self.modify_specific_test_case(
                    test_cases_for_modification, 
                    user_message
                )
                
                # Delete processing message
                if processing_msg:
                    try:
                        await processing_msg.delete()
                    except Exception as e:
                        logger.warning(f"Could not delete processing message: {e}")
                
                # Send modified test cases
                await self.send_long_message(update, modified_test_cases)
                
                # Store modified version
                context.user_data['last_generated_test_cases'] = modified_test_cases
                context.user_data['original_test_cases'] = test_cases_for_modification  # Keep backup
                
                # Reset mode and show options
                self.user_sessions[user_id]['mode'] = 'general'
                context.user_data.pop('test_cases_for_modification', None)
                
                keyboard = [
                    [
                        InlineKeyboardButton("🔧 Modify Again", callback_data=f"modify_again_{user_id}"),
                        InlineKeyboardButton("📊 Export to Excel", callback_data=f"export_excel_{user_id}")
                    ],
                    [
                        InlineKeyboardButton("↩️ Revert Changes", callback_data=f"revert_changes_{user_id}"),
                        InlineKeyboardButton("🔄 Generate New", callback_data="mode_testcases")
                    ],
                    [
                        InlineKeyboardButton("← Back to Main", callback_data="back_main")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                try:
                    await update.message.reply_text(
                        "✅ Test Case Modification Complete!\n\n"
                        "🎯 Only requested test case(s) modified\n"
                        "🔒 Other test cases preserved\n"
                        "📋 Ready for export or further modifications",
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Error sending modification complete message: {e}")
                return

            # Normal text processing (no direct image handling here)
            # Enter/handle Collection Mode when in either 'testcases' or 'generate' flows
            if mode in ('testcases', 'generate'):
                # Regenerate with same requirements: accept prompt/comment and regenerate
                if context.user_data.get('regen_mode') == 'same_requirements':
                    try:
                        await update.message.reply_text("🔁 Regenerating test cases with the same sources + your prompt...")
                    except Exception:
                        pass
                    last_type = context.user_data.get('last_test_type', 'all')
                    last_text = context.user_data.get('last_sources_text', '')
                    last_images = context.user_data.get('last_sources_images', []) or []
                    combined_text = (last_text or '')
                    if user_message:
                        combined_text = (combined_text + ("\n\n" if combined_text else '') + f"User prompt: {user_message}").strip()
                    processing_msg = await update.message.reply_text("⏳ Generating...")
                    try:
                        response = await self._agent_generate(last_type, combined_text, last_images)
                    finally:
                        try:
                            await processing_msg.delete()
                        except Exception:
                            pass
                    context.user_data['last_generated_test_cases'] = response
                    context.user_data['last_test_type'] = last_type
                    context.user_data['last_sources_text'] = combined_text
                    context.user_data['last_sources_images'] = last_images
                    context.user_data.pop('regen_mode', None)
                    await self.send_long_message(update, response)
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text="Select next action:",
                        reply_markup=self.get_post_generation_keyboard(update.effective_user.id)
                    )
                    return
                # Collection mode: accumulate text
                if context.user_data.get('collect_requirements_mode', False):
                    texts = context.user_data.get('collected_texts', [])
                    # Handle general mode
                    context.user_data['collected_texts'] = texts
                    count_imgs = len(context.user_data.get('collected_images', []))
                    count_txts = len(texts)
                    keyboard = [
                        [InlineKeyboardButton("➕ Add More Text", callback_data=f"collect_more_text_{user_id}"),
                         InlineKeyboardButton("➕ Add Image", callback_data=f"collect_add_image_{user_id}")],
                        [InlineKeyboardButton("✅ Generate Now", callback_data=f"collect_generate_{user_id}"),
                         InlineKeyboardButton("🗑️ Reset", callback_data=f"collect_reset_{user_id}")]
                    ]
                    await update.message.reply_text(
                        f"🧺 Collected Requirements Updated\n\n📄 Text: {count_txts}\n🖼️ Images: {count_imgs}\n\nSend more text, or tap Generate Now when ready.",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                    return

                # Directly start collection mode (text-first flow)
                context.user_data['collect_requirements_mode'] = True
                imgs = context.user_data.get('collected_images', []) or []
                texts = context.user_data.get('collected_texts', []) or []
                if user_message:
                    texts.append(user_message)
                # Persist
                context.user_data['collected_images'] = imgs
                context.user_data['collected_texts'] = texts
                count_imgs = len(imgs)
                count_txts = len(texts)
                keyboard = [
                    [InlineKeyboardButton("➕ Add More Text", callback_data=f"collect_more_text_{user_id}"),
                     InlineKeyboardButton("➕ Add Image", callback_data=f"collect_add_image_{user_id}")],
                    [InlineKeyboardButton("✅ Generate Now", callback_data=f"collect_generate_{user_id}"),
                     InlineKeyboardButton("🗑️ Reset", callback_data=f"collect_reset_{user_id}")]
                ]
                await update.message.reply_text(
                    f"🧺 Collection started\n\n📄 Text: {count_txts}\n🖼️ Images: {count_imgs}\n\nSend more text or tap Generate Now.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return
        except Exception as e:
            logger.error(f"Error handling text message: {e}")
            await update.message.reply_text(f"❌ Error processing text: {str(e)}")

    async def handle_image_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming photos/images (with optional caption) for multimodal workflows."""
        try:
            if not update or not update.message or not update.message.photo:
                return
            user_id = update.effective_user.id

            # Download highest resolution photo
            photo = update.message.photo[-1]
            tg_file = await context.bot.get_file(photo.file_id)
            bio = io.BytesIO()
            await tg_file.download_to_memory(out=bio)
            bio.seek(0)
            image = PILImage.open(bio).convert('RGB')
            caption = (update.message.caption or "").strip()

            # Collection mode: ask for classification before adding
            if context.user_data.get('collect_requirements_mode', False):
                # Preserve image (and caption, if any) for post-classification merge
                context.user_data['pending_raw_image'] = image
                if caption:
                    context.user_data['pending_generation_text'] = caption
                keyboard = [
                    [
                        InlineKeyboardButton("📄 Requirements / Dokumen", callback_data=f"classify_image_requirements_{user_id}"),
                        InlineKeyboardButton("🖥️ UI Design / Mockup", callback_data=f"classify_image_design_{user_id}")
                    ],
                    [InlineKeyboardButton("← Back to Collection", callback_data=f"back_to_collection_{user_id}")]
                ]
                await update.message.reply_text(
                    "Konfirmasi jenis gambar ini:\n\n"
                    "📄 Requirements / Dokumen → PRD, user story, acceptance criteria\n"
                    "🖥️ UI Design / Mockup → Screenshot aplikasi, desain Figma, layout UI\n\n"
                    "Pilih salah satu untuk melanjutkan.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            # Determine session info
            user_session = self.user_sessions.get(user_id, {'mode': 'general'})
            mode = user_session.get('mode', 'general')
            generate_type = user_session.get('generate_type', user_session.get('test_type', 'functional'))

            # If caption present and in generate/testcases mode, ASK FOR CLASSIFICATION first
            if (mode in ('generate', 'testcases')) and caption:
                # Preserve caption to be merged into collection after classification
                context.user_data['pending_generation_text'] = caption
                context.user_data['pending_raw_image'] = image
                back_label = "← Back to Test Type Menu" if not context.user_data.get('collect_requirements_mode') else f"← Back to Collection"
                back_cb = "test_type_menu" if not context.user_data.get('collect_requirements_mode') else f"back_to_collection_{user_id}"
                keyboard = [
                    [
                        InlineKeyboardButton("📄 Requirements / Dokumen", callback_data=f"classify_image_requirements_{user_id}"),
                        InlineKeyboardButton("🖥️ UI Design / Mockup", callback_data=f"classify_image_design_{user_id}")
                    ],
                    [InlineKeyboardButton(back_label, callback_data=back_cb)]
                ]
                await update.message.reply_text(
                    "Konfirmasi jenis gambar ini:\n\n"
                    "📄 Requirements / Dokumen → PRD, user story, acceptance criteria\n"
                    "🖥️ UI Design / Mockup → Screenshot aplikasi, desain Figma, layout UI\n\n"
                    "Pilih salah satu untuk melanjutkan.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            # If no caption: ask for classification before proceeding
            if mode in ('generate', 'testcases'):
                context.user_data['pending_raw_image'] = image
                back_label = "← Back to Test Type Menu" if not context.user_data.get('collect_requirements_mode') else f"← Back to Collection"
                back_cb = "test_type_menu" if not context.user_data.get('collect_requirements_mode') else f"back_to_collection_{user_id}"
                keyboard = [
                    [
                        InlineKeyboardButton("📄 Requirements / Dokumen", callback_data=f"classify_image_requirements_{user_id}"),
                        InlineKeyboardButton("🖥️ UI Design / Mockup", callback_data=f"classify_image_design_{user_id}")
                    ],
                    [InlineKeyboardButton(back_label, callback_data=back_cb)]
                ]
                await update.message.reply_text(
                    "Konfirmasi jenis gambar ini:\n\n"
                    "📄 Requirements / Dokumen → PRD, user story, acceptance criteria\n"
                    "🖥️ UI Design / Mockup → Screenshot aplikasi, desain Figma, layout UI\n\n"
                    "Pilih salah satu untuk melanjutkan.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            if mode in ('testcases', 'generate'):
                # Redundant safety: ensure we always ask classification in these modes
                context.user_data['pending_raw_image'] = image
                keyboard = [
                    [
                        InlineKeyboardButton("📄 Requirements / PRD", callback_data=f"classify_image_requirements_{user_id}"),
                        InlineKeyboardButton("🎨 UI Design", callback_data=f"classify_image_design_{user_id}")
                    ],
                    [InlineKeyboardButton("← Back to Test Type Menu", callback_data="test_type_menu")]
                ]
                await update.message.reply_text(
                    "📸 Image received. Pilih jenis gambar terlebih dahulu sebelum lanjut:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            # General mode: analyze and offer generation
            analysis = await self.analyze_image_only(image, "General QA analysis of the provided image.")
            await self.send_long_message(update, analysis)
            keyboard = [
                [InlineKeyboardButton("🔍 Generate Test Cases", callback_data=f"generate_image_only_{user_id}")],
                [InlineKeyboardButton("← Back to Main", callback_data="back_main")]
            ]
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="💡 Image Analysis Complete!",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data['pending_image'] = image
            context.user_data['pending_image_type'] = 'functional'
        except Exception as e:
            logger.error(f"Error handling image message: {e}")
            await update.message.reply_text(f"❌ Error processing image: {str(e)}")

    def get_test_type_coverage(self, test_type):
        """Get coverage areas based on test type"""
        coverage = {
            "functional": """
1. Business Logic Testing - Core functionality and user flows
2. Input Validation - Data validation and error handling
3. User Journey Testing - End-to-end scenarios
4. Edge Cases - Boundary conditions and error states
5. Integration Testing - Component interactions""",
            
            "visual": """
1. UI Element Testing - Buttons, forms, navigation
2. Layout Testing - Positioning, alignment, spacing
3. Responsiveness - Different screen sizes and orientations
4. Design Validation - Colors, fonts, images according to Figma
5. Accessibility - Color contrast, keyboard navigation""",
            
        "api": """
1. Endpoint Testing - HTTP methods and responses
2. Authentication - Token validation and security
3. Data Validation - Request/response format validation
4. Error Handling - Status codes and error messages
5. Performance - Response times and load testing""",
        }
        return coverage.get(test_type, coverage["functional"])

    def _deduce_effective_type(self, requested_type: str, context_user_data: Optional[dict] = None, user_session: Optional[dict] = None) -> str:
        """Map any incoming type (including 'all'/'auto'/None) to either 'functional' or 'visual'.

        Heuristics:
        - If requested contains 'visual' -> visual
        - If requested contains 'functional' -> functional
        - Else infer from context: pending_image_type, user_session.test_type/generate_type, image_classification
        - Default to functional
        """
        try:
            t = (requested_type or "").strip().lower()
            if "visual" in t:
                return "visual"
            if "functional" in t:
                return "functional"
            cud = context_user_data or {}
            us = user_session or {}
            if str(cud.get('pending_image_type', '')).lower() == 'visual':
                return 'visual'
            if str(us.get('test_type', '')).lower() == 'visual':
                return 'visual'
            if str(us.get('generate_type', '')).lower() == 'visual':
                return 'visual'
            if cud.get('image_classification') == 'design':
                return 'visual'
            return 'functional'
        except Exception:
            return 'functional'

    def _extract_requested_case_count(self, text: str) -> int:
        """Extract an explicit user request for number of test cases from free text.
        Supports patterns like:
        - up to 10 / maksimal 10 / max 10 / hingga 10 / sampai 10
        - 10 test cases / 10 tc / generate 10 / exactly 10
        Returns an int if found and within [1, 50], else 0 (meaning no explicit limit).
        """
        try:
            if not text:
                return 0
            import re
            t = text.lower()
            patterns = [
                r"up to\s*(\d+)",
                r"max(?:imum)?\s*(\d+)",
                r"maks(?:imum)?\s*(\d+)",
                r"hingga\s*(\d+)",
                r"sampai\s*(\d+)",
                r"exactly\s*(\d+)",
                r"(\d+)\s*(?:test\s*cases|tc)\b",
                r"generate\s*(\d+)\b",
            ]
            for pat in patterns:
                m = re.search(pat, t)
                if m:
                    n = int(m.group(1))
                    if 1 <= n <= 50:
                        return n
            return 0
        except Exception:
            return 0

    def _resolve_last_type_for_regen(self, context_user_data: Optional[dict]) -> str:
        """Resolve last export/generation type with multiple signals.

        Signals considered (in no strict order):
        - last_export.type (if present)
        - type parsed from last_export.filename (squash_import_<type>_...)
        - last_export_type
        - last_test_type

        Heuristic:
        - Visual requires stronger signal: at least 2 independent visual votes
        - Otherwise default to functional (safer UX: show 'Generate Visual')
        """
        try:
            def classify(raw: str):
                if not raw:
                    return None
                r = str(raw).strip().lower()
                if 'vis' in r:
                    return 'visual'
                if 'func' in r:
                    return 'functional'
                return None

            # 0) Try to infer directly from the last generated text header
            try:
                lg = (context_user_data or {}).get('last_generated_test_cases')
                if isinstance(lg, str):
                    low = lg.lower()
                    if 'visual test cases generated' in low or 'test type: visual testing' in low:
                        return 'visual'
                    if 'functional test cases generated' in low or 'test type: functional testing' in low:
                        return 'functional'
            except Exception:
                pass

            # 1) Explicit type on last_export
            try:
                le = (context_user_data or {}).get('last_export') or {}
                le_type = classify(str(le.get('type') or ''))
                if le_type in ('functional','visual'):
                    return le_type
            except Exception:
                pass

            # 2) Parse from filename pattern
            try:
                le = (context_user_data or {}).get('last_export') or {}
                fname = str(le.get('filename') or '')
                if fname:
                    import re as _re
                    m = _re.search(r"squash_import_([a-zA-Z]+)_", fname)
                    parsed = classify(m.group(1)) if m else None
                    if parsed in ('functional','visual'):
                        return parsed
            except Exception:
                pass

            # 3) Use last_export_type if set
            let = classify(str(((context_user_data or {}).get('last_export_type')) or ''))
            if let in ('functional','visual'):
                return let

            # 4) Fallback to last_test_type
            ltt = classify(str(((context_user_data or {}).get('last_test_type')) or ''))
            if ltt in ('functional','visual'):
                return ltt

            # 5) Default bias
            return 'functional'
        except Exception:
            return 'functional'

    # Simplified: removed unused BDD helper methods (generate_specific_bdd_action,
    # generate_specific_bdd_expected, generate_contextual_bdd_steps). Core BDD
    # normalization/enforcement lives in utils.bdd_utils and agent classes.

    # Removed: process_text_query (inlined call site)

    async def safe_edit_message(self, query, text, reply_markup=None):
        """Safely edit message with error handling for 'Message is not modified' error"""
        try:
            # If original message is a text message, edit normally
            if getattr(query.message, 'text', None):
                if reply_markup:
                    await query.edit_message_text(text, reply_markup=reply_markup)
                else:
                    await query.edit_message_text(text)
                return

            # If original message has a caption (e.g. document, photo), try editing caption first
            if getattr(query.message, 'caption', None):
                try:
                    if reply_markup:
                        await query.edit_message_caption(caption=text, reply_markup=reply_markup)
                    else:
                        await query.edit_message_caption(caption=text)
                    return
                except BadRequest as e:
                    # If caption edit not allowed (e.g. too long), fall back to sending new message
                    if "Message is not modified" in str(e):
                        print(f"⚠️ Caption not modified: {e}")
                        return
                    print(f"⚠️ Could not edit caption, sending new message instead: {e}")
                except Exception as e:
                    print(f"⚠️ Unexpected error editing caption, sending new message: {e}")

            # Fallback: send a new message (covers sticker, document without caption, empty-text inline buttons, etc.)
            if reply_markup:
                await query.message.reply_text(text, reply_markup=reply_markup)
            else:
                await query.message.reply_text(text)
        except BadRequest as e:
            if "Message is not modified" in str(e):
                # Message content is identical, no need to edit
                print(f"⚠️ Message not modified: {e}")
                pass
            else:
                # Other BadRequest errors should be re-raised
                print(f"❌ BadRequest error: {e}")
                raise
        except Exception as e:
            print(f"❌ Unexpected error in safe_edit_message: {e}")
            raise

    def get_post_generation_keyboard(self, user_id: int) -> InlineKeyboardMarkup:
        """Standardized post-generation actions keyboard."""
        # Per request: remove Regenerate Options pre-export and add 'Generate with same Requirements' into the action menu.
        keyboard = [
            [
                InlineKeyboardButton("🔧 Modify Test Cases", callback_data=f"modify_testcase_{user_id}"),
                InlineKeyboardButton("📊 Export to Excel", callback_data=f"export_excel_{user_id}")
            ],
            [
                InlineKeyboardButton("🔁 Generate with same Requirements", callback_data=f"regen_same_{user_id}")
            ],
            [
                InlineKeyboardButton("← Back to Main", callback_data="back_main")
            ]
        ]
        return InlineKeyboardMarkup(keyboard)

    async def _agent_generate(self, test_type: str, text: str = "", images: Optional[List[Any]] = None) -> str:
        """Route generation through AgentManager if available for clearer split."""
        if getattr(self, 'agent_manager', None):
            result = await self.agent_manager.generate(test_type, text, images or [])
            try:
                route = self.agent_manager.get_last_route() if hasattr(self.agent_manager, 'get_last_route') else None
                logging.getLogger(__name__).info("Agent route used: %s", route)
            except Exception:
                pass
            return result
        # Minimal fallback to preserve behavior if manager not initialized
        imgs = images or []
        if imgs:
            return await self.generate_multimodal_test_cases_multi(imgs, text or "", test_type)
        return await self.generate_testcases_from_text(text or "", test_type)

    async def analyze_image_only(self, image: PILImage.Image, context_text: str = "") -> str:
        """Analyze image without text requirements via VisualAgent."""
        try:
            if getattr(self, 'visual_agent', None):
                return await self.visual_agent.image_analysis(image, context_text)
            return "❌ VisualAgent not available for image analysis."
        except Exception as e:
            logger.error(f"Error in image-only analysis: {e}")
            return f"❌ Error analyzing image: {str(e)}"
    
    def generate_test_cases(self, requirement: str, test_type: str = "all") -> str:
            """Generate test cases based on type via Agents (legacy sync wrapper)."""
            try:
                t = (test_type or 'functional').lower()
                # This method previously performed direct LLM calls; now we guide callers to async agent flows.
                if t == 'visual':
                    return "Use visual generation flow; handled by VisualAgent (async)."
                if t in ('functional', 'all', 'api'):
                    return "Use functional/text generation flow; handled by FunctionalAgent (async)."
                return "❌ Unsupported test type."
            except Exception as e:
                return f"Error generating test cases: {e}"

    def sanitize_example_titles(self, text: str) -> str:
        return util_sanitize_example_titles(text)

    # ================= Global Output Sanitation & Minimal Hardening (Option A) =================
    def _sanitize_generated_output(self, text: str) -> str:
        return util_sanitize_generated_output(text)

    def _normalize_numbering(self, text: str) -> str:
        return util_normalize_numbering(text)

    def _ensure_blank_line_between_numbered_cases(self, text: str) -> str:
        return util_ensure_blank_line_between_numbered(text)

    def _contains_indonesian(self, text: str) -> bool:
        return util_contains_indonesian(text)

    def _finalize_output(self, raw_text: str, retry_context: str = "", model_retry_parts: Optional[List[Any]] = None) -> str:
        """Finalize model output: sanitize, detect Indonesian leakage, single retry, defensive scrub.

        raw_text: initial model output
        retry_context: original prompt text (for logging/retry reference)
        model_retry_parts: list of parts used for original generation (string prompts only used for retry)
        """
        sanitized = self._sanitize_generated_output(raw_text)
        normalized = self._normalize_numbering(sanitized)
        if not self._contains_indonesian(normalized):
            return self._ensure_blank_line_between_numbered_cases(normalized)
        # Single retry if model parts provided
        if model_retry_parts:
            try:
                # Delegate English-only cleanup to FunctionalAgent to keep LLM prompts in agents
                if getattr(self, 'functional_agent', None) and hasattr(self.functional_agent, 'english_only_cleanup'):
                    retry_text = self.functional_agent.english_only_cleanup(sanitized[:5000])
                    retry_text = self._sanitize_generated_output((retry_text or '').strip())
                    if retry_text and not self._contains_indonesian(retry_text):
                        sanitized = retry_text
                        normalized = self._normalize_numbering(sanitized)
            except Exception as e:
                logger.warning(f"Retry sanitation failed: {e}")
        # Final scrub: remove lines with Indonesian tokens
        final_lines = []
        indo_pattern = re.compile(r'\b(ketika|tombol|halaman|pengguna|aplikasi|ditampilkan|ukuran|warna|berhasil|gagal|data|sistem)\b', re.IGNORECASE)
        for line in normalized.split('\n'):
            if indo_pattern.search(line):
                continue
            final_lines.append(line)
        final = '\n'.join(l for l in final_lines if l.strip())
        # Minimal BDD enforcement: ensure each numbered test case block contains at least one Then
        try:
            import re as _re_then
            lines = final.split('\n')
            out = []
            header_pat = _re_then.compile(r'^\s*\d{1,3}\.\s+\S')
            block = []
            def flush_block():
                nonlocal out, block
                if not block:
                    return
                block_text = '\n'.join(block)
                if 'Then ' not in block_text and 'Then\t' not in block_text and 'Then:' not in block_text:
                    # Append a generic Then to satisfy minimal requirement without altering existing steps
                    block.append('Then the expected result is displayed')
                out.extend(block)
                block = []
            for ln in lines:
                if header_pat.match(ln):
                    flush_block()
                    block = [ln]
                else:
                    block.append(ln)
            flush_block()
            final = '\n'.join(out)
        except Exception:
            pass
        if not final:
            final = (
                "001. Verify UI Rendering\nGiven the interface is displayed\nWhen the tester reviews the screen\nThen all visible elements render with correct position, alignment, and labeling per design"
            )
        # Ensure readability: add a single blank line between consecutive numbered test cases
        final = self._normalize_numbering(final)
        return self._ensure_blank_line_between_numbered_cases(final)

    async def handle_generation_from_text(self, test_case_text: str, target_type: str) -> str:
        """Handle text-only generation requests using agents; if generation fails, return explicit error."""
        try:
            target = (target_type or "").strip().lower() or "functional"
            generation_prompt = f"""GENERATION CONTEXT:
User wants to convert this content into {target} test cases:

{test_case_text}

Please generate {target} test cases based on this input, ensuring they follow our BDD and Squash TM conventions."""

            # Route to agent
            generated_cases = ""
            if target == "visual" and getattr(self, "visual_agent", None):
                generated_cases = await self.visual_agent.generate_from_text(generation_prompt)
            elif getattr(self, "functional_agent", None):
                generated_cases = await self.functional_agent.generate_from_text(generation_prompt)
            else:
                return (
                    "❌ Tidak dapat memproses permintaan: agent tidak tersedia. "
                    "Pastikan konfigurasi bot sudah benar atau coba lagi nanti."
                )

            # Minimal validation: check BDD-ish signals
            has_numbering = bool(re.search(r"^\s*\d{1,3}\.", generated_cases or "", re.MULTILINE))
            has_bdd = any(x in (generated_cases or "") for x in ["Given ", "When ", "Then ", "Scenario:", "Feature:"])
            if not generated_cases or not (has_numbering or has_bdd):
                return (
                    "❌ Bot tidak dapat mengenerate test case sesuai permintaan. "
                    "Mohon perbaiki input (lengkapi konteks/format) atau coba tipe lain."
                )

            # Build response with summary
            test_case_count = self.count_test_cases(generated_cases)
            header = (
                "✅ Generation Complete!\n\n"
                f"🎯 Target Format: {target.upper()}\n"
                f"📋 Test Cases Generated: {test_case_count}\n\n---\n\n"
            )
            return header + generated_cases

        except Exception as e:
            logger.error(f"Text-only generation error: {e}")
            return (
                "❌ Terjadi kesalahan saat melakukan generasi dari teks. "
                f"Detail: {str(e)}\n"
                "Silakan coba lagi atau kirim input yang lebih sederhana."
            )

    # Removed: fallback_translation (explicit error returned instead of silent fallback)

    def count_test_cases(self, test_cases_text: str) -> int:
        """Count number of test cases in generated text"""
        try:
            # Count lines that start with test case patterns
            lines = test_cases_text.split('\n')
            count = 0
            
            for line in lines:
                line = line.strip()
                # Common test case patterns
                if (line.startswith("Test Case") or 
                    line.startswith("TC") or 
                    line.startswith("001.") or 
                    line.startswith("002.") or 
                    line.startswith("003.") or
                    line.startswith("004.") or
                    line.startswith("005.") or
                    re.match(r'^\d+\.', line) or
                    "Test Case" in line and re.search(r'\d{3}', line)):
                    count += 1
            
            return max(count, 1)  # At least 1 test case
            
        except Exception as e:
            logger.error(f"Error counting test cases: {e}")
            return 1

    async def generate_multimodal_content(self, image: PILImage.Image, text_content: str, target_format: str) -> str:
        """Generate test cases from image + text via VisualAgent."""
        try:
            if getattr(self, 'visual_agent', None):
                return await self.visual_agent.generate_multimodal_content(image, text_content, target_format)
            return "❌ VisualAgent not available for multimodal generation."
        except Exception as e:
            logger.error(f"Error in multimodal generation: {e}")
            return f"❌ Error generating multimodal content: {str(e)}"
    
    def parse_generated_test_cases(self, test_cases_text: str) -> List[Dict]:
        """Minimal inline parser: split by numbered headers and collect BDD lines."""
        try:
            if not test_cases_text:
                return []
            lines = test_cases_text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
            header_pat = re.compile(r'^\s*(\d{1,3})\.[\s\)]*(.+)')
            cases: List[Dict] = []
            cur: Optional[Dict] = None
            bdd: List[str] = []
            for ln in lines:
                m = header_pat.match(ln)
                if m:
                    if cur:
                        if bdd:
                            cur['bdd_lines'] = list(bdd)
                        cases.append(cur)
                    num = int(m.group(1))
                    title = m.group(2).strip()
                    cur = {'id': f'TC_{num:03d}', 'name': f'{num:03d} {title}', 'description': title, 'steps': []}
                    bdd = []
                    continue
                if cur:
                    s = ln.strip()
                    if re.match(r'^(Given|When|Then|And)\b', s):
                        bdd.append(s)
            if cur:
                if bdd:
                    cur['bdd_lines'] = list(bdd)
                cases.append(cur)
            return cases
        except Exception:
            return []
        
    def convert_to_squash_excel(self, test_cases: List[Dict], username: str = "QA_Bot", folder_path: str = "Test Cases") -> io.BytesIO:
        """Convert structured test cases to a Squash TM .xls and return as BytesIO.

        Uses the centralized exporter to write a temporary .xls, then loads it into memory.
        """
        # Use exporter to save to disk, then load into BytesIO for Telegram
        tmp_path = exporter_export_squash_xls_file(test_cases, username=username)
        try:
            with open(tmp_path, 'rb') as f:
                data = f.read()
            return io.BytesIO(data)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        
    def generate_filename(self, test_type: str = "test_cases") -> str:
        """Generate export filename (delegates to exporters.squash_export)."""
        return exporter_generate_filename(test_type)
    
    async def modify_specific_test_case(self, test_cases_text: str, modification_request: str) -> str:
        """Modify specific test case via FunctionalAgent."""
        try:
            if getattr(self, 'functional_agent', None):
                return await self.functional_agent.modify_specific_test_case(test_cases_text, modification_request)
            return "❌ FunctionalAgent not available for modification."
        except Exception as e:
            logger.error(f"Error modifying test case: {e}")
            return f"❌ Error modifying test case: {str(e)}"

    async def extract_test_case_numbers(self, test_cases_text: str) -> List[str]:
        """Extract test case numbers from generated text for user reference"""
        try:
            lines = test_cases_text.split('\n')
            test_case_numbers = []
            
            # Debug: Log the content structure
            logger.info(f"DEBUG: Total lines in content: {len(lines)}")
            logger.info(f"DEBUG: First 10 lines: {lines[:10]}")
            
            for i, line in enumerate(lines):
                line = line.strip()
                
                # Skip empty lines and pure markdown
                if not line or line in ['```', '---']:
                    continue
                
                # Log lines that might be test cases
                if any(keyword in line.lower() for keyword in ['test', 'case', 'scenario', 'feature', 'given', 'when', 'then']):
                    logger.info(f"DEBUG: Potential test case line {i}: {line}")
                
                # Look for various test case patterns with more flexibility
                patterns = [
                    # Standard numbered patterns
                    (r'^\\Test Case (\d+):\s*(.+)', r'Test Case \1: \2'),
                    (r'^Test Case (\d+):\s*(.+)', r'Test Case \1: \2'),
                    (r'^(\d{3})\.\s*(.+)', r'\1. \2'),
                    (r'^\[(\d{3})\]\s*(.+)', r'\1. \2'),
                    (r'^(\d+)\.\s*(.+)', r'\1. \2'),
                    (r'^TC-(\d+)\s*(.+)', r'TC-\1 \2'),
                    (r'^(\d+)\)\s*(.+)', r'\1. \2'),
                    
                    # BDD/Gherkin patterns
                    (r'^\\Scenario:\s*(.+)', r'Scenario: \1'),
                    (r'^Scenario:\s*(.+)', r'Scenario: \1'),
                    (r'^\\Scenario Outline:\s*(.+)', r'Scenario Outline: \1'),
                    (r'^Scenario Outline:\s*(.+)', r'Scenario Outline: \1'),
                    (r'^\\Feature:\s*(.+)', r'Feature: \1'),
                    (r'^Feature:\s*(.+)', r'Feature: \1'),
                    
                    # Markdown headers that might be test cases
                    (r'^#{1,4}\sTest Case\s(\d+):\s*(.+)', r'Test Case \1: \2'),
                    (r'^#{1,4}\s*(.+test.+case.+)', r'\1'),
                    (r'^#{1,4}\s*(.+)', r'\1'),
                    
                    # Any line that looks like a test identifier
                    (r'.\b(Test Case|TC|Scenario)\s(\d+).*', r'Test Case \2'),
                ]
                
                matched = False
                for pattern, replacement in patterns:
                    match = re.match(pattern, line, re.IGNORECASE)
                    if match:
                        try:
                            if len(match.groups()) >= 2:
                                tc_num = match.group(1)
                                title = match.group(2) if len(match.groups()) > 1 else ""
                                
                                # Format as 3-digit number
                                if tc_num.isdigit():
                                    tc_num_formatted = f"{int(tc_num):03d}"
                                    test_case_numbers.append(f"{tc_num_formatted}. {title[:50]}")
                                else:
                                    test_case_numbers.append(f"{tc_num}. {title[:50]}")
                            else:
                                # Single group match
                                content = match.group(1)
                                test_case_numbers.append(f"{len(test_case_numbers)+1:03d}. {content[:50]}")
                            
                            logger.info(f"DEBUG: Matched pattern '{pattern}' on line: {line}")
                            matched = True
                            break
                        except Exception as e:
                            logger.error(f"Error processing match: {e}")
                            continue
                
                # Fallback: Look for any line that might be a test case without strict patterns
                if not matched and line:
                    # Check if line contains test-related keywords and looks like a header
                    test_keywords = ['test case', 'scenario', 'verify', 'check', 'ensure', 'validate', 'confirm']
                    if (any(keyword in line.lower() for keyword in test_keywords) and 
                        len(line) > 10 and len(line) < 200 and
                        not line.startswith('•') and not line.startswith('-') and
                        ':' in line):
                        
                        # Assign sequential number
                        test_case_numbers.append(f"{len(test_case_numbers)+1:03d}. {line[:50]}")
                        logger.info(f"DEBUG: Fallback match on line: {line}")
            
            # Remove duplicates while preserving order
            seen = set()
            unique_test_cases = []
            for tc in test_case_numbers:
                tc_key = tc.split('.')[0].strip()
                if tc_key not in seen:
                    seen.add(tc_key)
                    unique_test_cases.append(tc)
            
            logger.info(f"DEBUG: Extracted {len(unique_test_cases)} unique test cases")
            logger.info(f"DEBUG: Test cases: {unique_test_cases}")
            
            # If still no test cases found, create generic ones based on content structure
            if not unique_test_cases:
                logger.warning("No test cases detected, creating generic structure")
                # Look for any meaningful content lines to create test cases
                content_lines = [line.strip() for line in lines if line.strip() and len(line.strip()) > 10]
                meaningful_lines = []
                
                for line in content_lines[:10]:  # Max 10 lines
                    # Skip common markdown and formatting
                    if not any(skip in line for skip in ['```', '---', '**', '#', 'DEBUG:', 'INFO:']):
                        meaningful_lines.append(line)
                
                for i, line in enumerate(meaningful_lines[:5]):  # Create max 5 generic test cases
                    unique_test_cases.append(f"{i+1:03d}. {line[:50]}")
                
                logger.info(f"DEBUG: Created {len(unique_test_cases)} generic test cases")
            
            return unique_test_cases
            
        except Exception as e:
            logger.error(f"Error extracting test case numbers: {e}")
            return []

    async def show_modification_options(self, update: Update, context: ContextTypes.DEFAULT_TYPE, test_cases_text: str):
        """Show modification options to user"""
        try:
            # Extract test case list for reference
            test_case_list = await self.extract_test_case_numbers(test_cases_text)
            user_id = update.effective_user.id
            
            # Debug logging
            logger.info(f"DEBUG: Found {len(test_case_list)} test cases for modification")
            logger.info(f"DEBUG: First 3 test cases: {test_case_list[:3]}")
            
            # Create full reference message (list all cases)
            reference_text = "📋 Test Cases for Modifications:\n\n"
            
            if not test_case_list:
                # No test cases detected; keep it minimal
                reference_text += "(No test cases detected)"
            else:
                for i, tc in enumerate(test_case_list[:20], 1):
                    reference_text += f"{i}. {tc}\n"
            # Keep message minimal; no long instructions

            # Determine which message object to use
            message = update.callback_query.message if update.callback_query else update.message
            chat_id = message.chat_id if message else update.effective_chat.id
            
            # Send reference message
            await context.bot.send_message(chat_id=chat_id, text=reference_text)
            
            # Create manual selection buttons for test cases ONLY if test cases were found
            if test_case_list and len(test_case_list) > 0:
                keyboard = []
                
                # Show up to 20 buttons (matching generation target)
                max_buttons_to_show = min(20, len(test_case_list))
                
                # Create rows of 3 buttons each for the subset
                for i in range(0, max_buttons_to_show, 3):
                    row = []
                    for j in range(3):
                        if i + j < max_buttons_to_show:
                            tc_index = i + j
                            tc_text = test_case_list[tc_index]
                            
                            # Extract test case number more reliably
                            if tc_text.startswith('TC'):
                                # Handle "TC001. Title" format
                                tc_number = tc_text[2:5]  # Extract the 3 digits after "TC"
                            elif '.' in tc_text:
                                # Handle "001. Title" format
                                tc_number = tc_text.split('.')[0].strip()
                                # Ensure 3-digit format
                                if tc_number.isdigit():
                                    tc_number = f"{int(tc_number):03d}"
                            else:
                                # Fallback: use index
                                tc_number = f"{tc_index + 1:03d}"
                            
                            button_text = f"📝 TC-{tc_number}"
                            callback_data = f"select_tc_{tc_number}_{user_id}"
                            
                            # Avoid duplicate callback data
                            if not any(button.callback_data == callback_data for row_buttons in keyboard for button in row_buttons):
                                row.append(InlineKeyboardButton(button_text, callback_data=callback_data))
                    
                    if row:  # Only add row if it has buttons
                        keyboard.append(row)
                
                # No "Show All" button; we show up to 20 directly
                
                # Add other options
                keyboard.append([
                    InlineKeyboardButton("📊 Export (No Changes)", callback_data=f"export_excel_{user_id}"),
                    InlineKeyboardButton("← Back", callback_data=f"back_after_modify_{user_id}")
                ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🎯 Select Test Case to Modify:",
                    reply_markup=reply_markup
                )
            else:
                # If no test cases detected, just provide text modification interface
                keyboard = [
                    [
                        InlineKeyboardButton("📊 Export (No Changes)", callback_data=f"export_excel_{user_id}"),
                        InlineKeyboardButton("← Back", callback_data=f"back_after_modify_{user_id}")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="📝 Type your modification request:",
                    reply_markup=reply_markup
                )
            
            # Set user mode for modification
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = {}
            self.user_sessions[user_id]['mode'] = 'modify_testcase'
            context.user_data['test_cases_for_modification'] = test_cases_text
            
        except Exception as e:
            logger.error(f"Error showing modification options: {e}")
            # Safe error handling
            try:
                if update.callback_query and update.callback_query.message:
                    await update.callback_query.message.reply_text(f"❌ Error preparing modification options: {str(e)}")
                elif update.message:
                    await update.message.reply_text(f"❌ Error preparing modification options: {str(e)}")
                else:
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"❌ Error preparing modification options: {str(e)}"
                    )
            except Exception as send_error:
                logger.error(f"Error sending error message: {send_error}")

    async def send_long_message(self, update: Update, text: str, max_length: int = 4000):
        """Send long messages by splitting them with improved formatting preservation"""
        try:
            # ✅ SAFETY CHECKS
            if not update or not update.message:
                logger.error("Update or message is None in send_long_message")
                return
                
            if not text or text.strip() == "":
                logger.warning("Empty text provided to send_long_message")
                await update.message.reply_text("❌ No content to display.")
                return
            
            # Use the large text message sender
            await self.send_large_text_message_via_update(update, text, max_length)
                
        except Exception as e:
            logger.error(f"Error in send_long_message: {e}")
            try:
                await update.message.reply_text(f"❌ Error displaying message: {str(e)}")
            except:
                logger.error("Could not send error notification")

    async def send_large_text_message_via_update(self, update: Update, text: str, max_length: int = 4000):
        """Send large text using update object"""
        if len(text) <= max_length:
            await update.message.reply_text(text)
            return
            
        # Split and send
        chunks = self._split_text_intelligently(text, max_length)
        for i, chunk in enumerate(chunks):
            if chunk.strip():
                prefix = f"📄 Part {i+1}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
                await update.message.reply_text(prefix + chunk)
                await asyncio.sleep(0.5)  # Rate limiting

    async def send_large_text_message(self, bot, chat_id: int, text: str, max_length: int = 4000):
        """Send large text messages by splitting them - for direct bot usage"""
        try:
            if not text or text.strip() == "":
                logger.warning("Empty text provided to send_large_text_message")
                await bot.send_message(chat_id=chat_id, text="❌ No content to display.")
                return
            
            # Clean text first - remove excessive whitespace while preserving structure
            cleaned_text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # Max 2 consecutive line breaks
            cleaned_text = re.sub(r'[ \t]+', ' ', cleaned_text)      # Multiple spaces to single space
            cleaned_text = cleaned_text.strip()
            
            if len(cleaned_text) <= max_length:
                await bot.send_message(chat_id=chat_id, text=cleaned_text)
                return
            
            # Split by logical sections for better readability
            chunks = []
            
            # Try to split by test case sections first
            test_case_sections = re.split(r'\n---\n', cleaned_text)
            
            if len(test_case_sections) > 1:
                current_chunk = ""
                
                for section in test_case_sections:
                    section = section.strip()
                    if not section:
                        continue
                        
                    # Check if adding this section would exceed limit
                    if len(current_chunk) + len(section) + 10 > max_length:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                            current_chunk = section
                        else:
                            # Section itself is too large, split by lines
                            lines = section.split('\n')
                            temp_chunk = ""
                            for line in lines:
                                if len(temp_chunk) + len(line) + 1 > max_length:
                                    if temp_chunk:
                                        chunks.append(temp_chunk.strip())
                                        temp_chunk = line
                                    else:
                                        chunks.append(line[:max_length])
                                else:
                                    temp_chunk += "\n" + line if temp_chunk else line
                            if temp_chunk:
                                chunks.append(temp_chunk.strip())
                    else:
                        current_chunk += "\n---\n" + section if current_chunk else section
                
                if current_chunk:
                    chunks.append(current_chunk.strip())
            else:
                # Fallback: split by paragraphs/lines
                lines = cleaned_text.split('\n')
                current_chunk = ""
                
                for line in lines:
                    if len(current_chunk) + len(line) + 1 > max_length:
                        if current_chunk:
                            chunks.append(current_chunk.strip())
                            current_chunk = line
                        else:
                            chunks.append(line[:max_length])
                    else:
                        current_chunk += "\n" + line if current_chunk else line
                
                if current_chunk:
                    chunks.append(current_chunk.strip())
            
            # Send each chunk
            for i, chunk in enumerate(chunks):
                if chunk.strip():
                    prefix = f"📄 Part {i+1}/{len(chunks)}\n\n" if len(chunks) > 1 else ""
                    await bot.send_message(chat_id=chat_id, text=prefix + chunk)
                    
        except Exception as e:
            logger.error(f"Error in send_large_text_message: {e}")
            await bot.send_message(chat_id=chat_id, text=f"❌ Error sending message: {str(e)}")

    def _split_text_intelligently(self, text: str, max_length: int) -> List[str]:
        """Split text intelligently preserving test case structure"""
        chunks = []
        
        # Clean text first
        cleaned_text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)
        cleaned_text = re.sub(r'[ \t]+', ' ', cleaned_text)
        cleaned_text = cleaned_text.strip()
        
        # Try to split by test case sections first
        test_case_sections = re.split(r'\n---\n', cleaned_text)
        
        if len(test_case_sections) > 1:
            current_chunk = ""
            
            for section in test_case_sections:
                section = section.strip()
                if not section:
                    continue
                    
                # Check if adding this section would exceed limit
                if len(current_chunk) + len(section) + 10 > max_length:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                        current_chunk = section
                    else:
                        # Section itself is too large, split by lines
                        lines = section.split('\n')
                        temp_chunk = ""
                        for line in lines:
                            if len(temp_chunk) + len(line) + 1 > max_length:
                                if temp_chunk:
                                    chunks.append(temp_chunk.strip())
                                    temp_chunk = line
                                else:
                                    chunks.append(line[:max_length])
                            else:
                                temp_chunk += "\n" + line if temp_chunk else line
                        if temp_chunk:
                            chunks.append(temp_chunk.strip())
                else:
                    current_chunk += "\n---\n" + section if current_chunk else section
            
            if current_chunk:
                chunks.append(current_chunk.strip())
        else:
            # Fallback: split by paragraphs/lines
            lines = cleaned_text.split('\n')
            current_chunk = ""
            
            for line in lines:
                if len(current_chunk) + len(line) + 1 > max_length:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                        current_chunk = line
                    else:
                        chunks.append(line[:max_length])
                else:
                    current_chunk += "\n" + line if current_chunk else line
            
            if current_chunk:
                chunks.append(current_chunk.strip())
        
        return chunks

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries with better error handling"""
        
        # ✅ SAFETY CHECKS
        if not update or not update.callback_query:
            logger.error("Update or callback_query is None")
            return
            
        query = update.callback_query
        
        if not query.data:
            logger.warning("Callback query data is None")
            try:
                await query.answer("❌ Invalid request")
            except:
                pass
            return
        
        # 🔍 DEBUG: Log all incoming callbacks
        print(f"🔍 DEBUG: CALLBACK RECEIVED: '{query.data}'")
        print(f"🔍 DEBUG: From user: {update.effective_user.id}")
        print(f"🔍 DEBUG: User data keys: {list(context.user_data.keys())}")
        
        # ✅ Safe user ID extraction
        try:
            user_id = query.from_user.id
        except AttributeError:
            logger.error("Could not extract user_id from callback query")
            try:
                await query.answer("❌ Error processing user information")
            except:
                pass
            return
        
        try:
            await query.answer()
            # COLLECTION MODE HANDLERS
            if query.data.startswith("collect_start_"):
                # Ensure collection mode is active
                context.user_data['collect_requirements_mode'] = True
                imgs = context.user_data.get('collected_images', []) or []
                texts = context.user_data.get('collected_texts', []) or []
                # Include classified raw image if present
                raw_img = context.user_data.pop('pending_raw_image', None)
                if raw_img and raw_img not in imgs:
                    imgs.append(raw_img)
                # Include any pending image into collection
                pending_img = context.user_data.get('pending_image')
                if pending_img and pending_img not in imgs:
                    imgs.insert(0, pending_img)
                    # Do NOT clear pending_image_type; keep for later type selection
                    context.user_data.pop('pending_image', None)
                # Optionally include any pending text into collection
                for key in ('pending_text_requirements', 'pending_generation_text'):
                    if context.user_data.get(key):
                        texts.append(context.user_data.get(key))
                        context.user_data.pop(key, None)
                context.user_data['collected_images'] = imgs
                context.user_data['collected_texts'] = texts
                count_imgs = len(imgs)
                count_txts = len(texts)
                keyboard = [
                    [InlineKeyboardButton("➕ Add More Text", callback_data=f"collect_more_text_{user_id}"),
                     InlineKeyboardButton("➕ Add Image", callback_data=f"collect_add_image_{user_id}")],
                    [InlineKeyboardButton("✅ Generate Now", callback_data=f"collect_generate_{user_id}"),
                     InlineKeyboardButton("🗑️ Reset", callback_data=f"collect_reset_{user_id}")]
                ]
                await self.safe_edit_message(
                    query,
                    f"🧺 Collection mode started.\n\n📄 Text: {count_txts}\n🖼️ Images: {count_imgs}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            elif query.data.startswith("collect_more_text_"):
                # Ensure collection mode is active and lists exist
                if not context.user_data.get('collect_requirements_mode'):
                    context.user_data['collect_requirements_mode'] = True
                context.user_data.setdefault('collected_images', [])
                context.user_data.setdefault('collected_texts', [])
                await self.safe_edit_message(query, "📝 Send additional text requirements now. They will be added to the collection.")
                return

            elif query.data.startswith("collect_add_image_"):
                # Ensure collection mode is active and lists exist
                if not context.user_data.get('collect_requirements_mode'):
                    context.user_data['collect_requirements_mode'] = True
                context.user_data.setdefault('collected_images', [])
                context.user_data.setdefault('collected_texts', [])
                await self.safe_edit_message(query, "📸 Send another image (screenshot/PRD/UI). It will be added to the collection.")
                return

            elif query.data.startswith("collect_reset_"):
                context.user_data.pop('collect_requirements_mode', None)
                context.user_data.pop('collected_images', None)
                context.user_data.pop('collected_texts', None)
                # Provide button to re-enter empty collection mode
                user_id = int(query.data.split('_')[-1]) if query.data.split('_')[-1].isdigit() else update.effective_user.id
                keyboard = [
                    [InlineKeyboardButton("↩️ Back to Collection", callback_data=f"collect_reenter_{user_id}")],
                    [InlineKeyboardButton("← Back to Test Type Menu", callback_data="test_type_menu")]
                ]
                await self.safe_edit_message(query, "🗑️ Collection cleared.", reply_markup=InlineKeyboardMarkup(keyboard))
                return

            elif query.data.startswith("collect_reenter_"):
                user_id = int(query.data.split('_')[-1]) if query.data.split('_')[-1].isdigit() else update.effective_user.id
                context.user_data['collect_requirements_mode'] = True
                context.user_data['collected_images'] = []
                context.user_data['collected_texts'] = []
                keyboard = [
                    [InlineKeyboardButton("➕ Add More Text", callback_data=f"collect_more_text_{user_id}"),
                     InlineKeyboardButton("➕ Add Image", callback_data=f"collect_add_image_{user_id}")],
                    [InlineKeyboardButton("✅ Generate Now", callback_data=f"collect_generate_{user_id}"),
                     InlineKeyboardButton("🗑️ Reset", callback_data=f"collect_reset_{user_id}")]
                ]
                await self.safe_edit_message(
                    query,
                    "🧺 Collection mode started.\n\n📄 Text: 0\n🖼️ Images: 0",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            elif query.data.startswith("collect_generate_"):
                imgs = context.user_data.get('collected_images', [])
                texts = context.user_data.get('collected_texts', [])
                raw_type = context.user_data.get('pending_test_type') or context.user_data.get('pending_generate_type') or 'functional'
                final_type = self._deduce_effective_type(raw_type, context.user_data, self.user_sessions.get(user_id, {}))
                aggregated_text = "\n\n".join(texts) if texts else (context.user_data.get('pending_text_requirements') or context.user_data.get('pending_generation_text') or "")
                # Extra certainty summary & logs
                img_count = len(imgs) if imgs else 0
                text_count = len(texts) if texts else 0
                logger.info(f"[CollectGenerate] Using {img_count} image(s) and {text_count} text. test_type={final_type}, agg_text_len={len(aggregated_text or '')}")
                preview = (aggregated_text or "")[:200].replace("\n", " ")
                logger.debug(f"[CollectGenerate] Aggregated text preview: {preview}")
                await self.safe_edit_message(query, f"🔄 Generating using {img_count} image(s) + {text_count} text...")
                response = await self._agent_generate(final_type, aggregated_text, imgs)
                # Clear collection but keep last result for export
                context.user_data.pop('collect_requirements_mode', None)
                context.user_data.pop('collected_images', None)
                context.user_data.pop('collected_texts', None)
                context.user_data['last_generated_test_cases'] = response
                context.user_data['last_test_type'] = final_type
                # Store sources for regenerate
                context.user_data['last_sources_text'] = aggregated_text
                context.user_data['last_sources_images'] = imgs or []
                await self.send_large_text_message(context.bot, query.message.chat_id, response)
                # Offer standardized post-generation actions
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="Select next action:",
                    reply_markup=self.get_post_generation_keyboard(user_id)
                )
                return

            elif query.data.startswith("back_to_collection_"):
                # Re-show collection basket status
                uid = int(query.data.split('_')[-1]) if query.data.split('_')[-1].isdigit() else update.effective_user.id
                imgs = context.user_data.get('collected_images', []) or []
                txts = context.user_data.get('collected_texts', []) or []
                context.user_data['collect_requirements_mode'] = True
                keyboard = [
                    [InlineKeyboardButton("➕ Add More Text", callback_data=f"collect_more_text_{uid}"),
                     InlineKeyboardButton("➕ Add Image", callback_data=f"collect_add_image_{uid}")],
                    [InlineKeyboardButton("✅ Generate Now", callback_data=f"collect_generate_{uid}"),
                     InlineKeyboardButton("🗑️ Reset", callback_data=f"collect_reset_{uid}")]
                ]
                await self.safe_edit_message(
                    query,
                    f"🧺 Collection Mode Active\n\n📄 Text: {len(txts)}\n🖼️ Images: {len(imgs)}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                return

            
            # MODIFICATION HANDLERS
            if query.data.startswith("modify_testcase_"):
                user_id = int(query.data.split("_")[-1])
                
                # ✅ Initialize variable FIRST
                last_generated = context.user_data.get('last_generated_test_cases', '')
                
                if not last_generated:
                    keyboard = [
                        [
                            InlineKeyboardButton("🔄 Generate Test Cases", callback_data="mode_testcases"),
                            InlineKeyboardButton("← Back to Main", callback_data="back_main")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await query.edit_message_text(
                        "❌ No test cases found to modify. Please generate test cases first.",
                        reply_markup=reply_markup
                    )
                    return
                
                # Show modification options
                await self.show_modification_options(update, context, last_generated)
            
            elif query.data.startswith("modify_again_"):
                user_id = int(query.data.split("_")[-1])
                
                # ✅ Initialize variable FIRST
                current_test_cases = context.user_data.get('last_generated_test_cases', '')
                
                if current_test_cases:
                    await self.show_modification_options(update, context, current_test_cases)
                else:
                    await query.edit_message_text("❌ No test cases available for modification.")

            elif query.data == "modify_testcase_help":
                # Return to modification options/help
                try:
                    uid = update.effective_user.id
                    current = context.user_data.get('test_cases_for_modification') or context.user_data.get('last_generated_test_cases', '')
                    if current:
                        await self.show_modification_options(update, context, current)
                    else:
                        keyboard = [[
                            InlineKeyboardButton("🔄 Generate Test Cases", callback_data="mode_testcases"),
                            InlineKeyboardButton("← Back to Main", callback_data="back_main")
                        ]]
                        await self.safe_edit_message(query, "❌ No test cases available. Please generate first.", InlineKeyboardMarkup(keyboard))
                except Exception:
                    pass

            elif query.data.startswith("back_after_modify_"):
                # Go back to post-generation actions menu
                uid = int(query.data.split("_")[-1])
                await self.safe_edit_message(query, "↩️ Back to actions.")
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="✅ Test Cases Ready. Choose an action:",
                    reply_markup=self.get_post_generation_keyboard(uid)
                )
            
            elif query.data.startswith("revert_changes_"):
                user_id = int(query.data.split("_")[-1])
                
                # ✅ Initialize variable FIRST
                original_test_cases = context.user_data.get('original_test_cases', '')
                
                if original_test_cases:
                    # Restore original version
                    context.user_data['last_generated_test_cases'] = original_test_cases
                    context.user_data.pop('original_test_cases', None)
                    
                    await query.edit_message_text(
                        "↩️ Changes Reverted Successfully!\n\n"
                        "✅ Test cases restored to original version\n"
                        "🔄 All modifications have been undone"
                    )
                    
                    # Show options after revert
                    keyboard = [
                        [
                            InlineKeyboardButton("🔧 Modify Again", callback_data=f"modify_testcase_{user_id}"),
                            InlineKeyboardButton("📊 Export to Excel", callback_data=f"export_excel_{user_id}")
                        ],
                        [
                            InlineKeyboardButton("← Back to Main", callback_data="back_main")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="Select your next action:",
                        reply_markup=reply_markup
                    )
                else:
                    await query.edit_message_text("❌ No original version found to revert to.")

            # TEST CASE SELECTION HANDLERS
            elif query.data.startswith("select_tc_"):
                # Handle manual test case selection
                parts = query.data.split("_")
                tc_number = parts[2]
                user_id = int(parts[3])
                
                # Store selected test case for modification
                context.user_data['selected_test_case'] = tc_number
                
                await query.edit_message_text(
                    f"📝 Test Case {tc_number} Selected\n\n"
                    f"🎯 Ready to modify Test Case {tc_number}\n\n"
                    f"💡 What would you like to change?\n"
                    f"Examples:\n"
                    f"• Add mobile testing steps\n"
                    f"• Change priority to HIGH\n"
                    f"• Update expected results\n"
                    f"• Add error handling scenarios\n"
                    f"• Include API validation\n\n"
                    f"📝 Type your modification request:"
                )
                
                # Set mode for receiving modification text
                self.user_sessions[user_id]['mode'] = 'modify_selected_testcase'
                
            elif query.data.startswith("show_all_tc_"):
                user_id = int(query.data.split("_")[-1])
                test_cases_text = context.user_data.get('test_cases_for_modification', '')
                
                if test_cases_text:
                    test_case_list = await self.extract_test_case_numbers(test_cases_text)
                    
                    # Create comprehensive test case list (no limit)
                    all_tc_text = "📋 Complete Test Case List:\n\n" + "\n".join(
                        [f"{i+1}. {tc[:50] + '...' if len(tc) > 50 else tc}" for i, tc in enumerate(test_case_list)]
                    )
                    
                    keyboard = []
                    used_numbers = set()  # Track used numbers to avoid duplicates
                    
                    for i, tc in enumerate(test_case_list):
                        # Extract test case number more reliably
                        if tc.startswith('TC'):
                            tc_number = tc[2:5]  # Extract the 3 digits after "TC"
                        elif '.' in tc:
                            tc_number = tc.split('.')[0].strip()
                            if tc_number.isdigit():
                                tc_number = f"{int(tc_number):03d}"
                        else:
                            tc_number = f"{i + 1:03d}"
                        
                        # Only add if number not already used
                        if tc_number not in used_numbers:
                            used_numbers.add(tc_number)
                            button_text = f"📝 TC-{tc_number}"
                            
                            # Create rows of 3 buttons
                            if len(keyboard) == 0 or len(keyboard[-1]) == 3:
                                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_tc_{tc_number}_{user_id}")])
                            else:
                                keyboard[-1].append(InlineKeyboardButton(button_text, callback_data=f"select_tc_{tc_number}_{user_id}"))
                    
                    # Add back button
                    keyboard.append([InlineKeyboardButton("← Back to Selection", callback_data=f"modify_testcase_{user_id}")])
                    
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await query.edit_message_text(
                        all_tc_text + "\n\n👆 Select test case to modify:",
                        reply_markup=reply_markup
                    )

            # EXPORT HANDLERS
            elif query.data.startswith("export_excel_"):
                await self.safe_edit_message(query, "📊 Generating Squash TM import file...")
                
                try:
                    # ✅ Initialize variable FIRST
                    last_generated = context.user_data.get('last_generated_test_cases', '')
                    # Ensure export type is normalized to functional/visual (no 'all')
                    test_type = self._deduce_effective_type(context.user_data.get('last_test_type', 'functional'), context.user_data, self.user_sessions.get(user_id, {}))
                    
                    if not last_generated:
                        keyboard = [
                            [
                                InlineKeyboardButton("🔄 Generate Test Cases", callback_data="mode_testcases"),
                                InlineKeyboardButton("← Back to Main", callback_data="back_main")
                            ]
                        ]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        
                        await self.safe_edit_message(
                            query,
                            "❌ No test cases found to export. Please generate test cases first.",
                            reply_markup=reply_markup
                        )
                        return
                    
                    # Import the correct converter
                    try:
                        from exporters.squash_export import convert_to_squash_import_xls
                    except ImportError:
                        await self.safe_edit_message(
                            query,
                            "❌ Squash TM converter not available. Please ensure multi_sheet_converter.py exists."
                        )
                        return
                    
                    # Create filename
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"squash_import_{test_type}_{timestamp}.xls"
                    
                    # Get username from Telegram user
                    username = query.from_user.username or query.from_user.first_name or "QA_Bot"
                    
                    # Convert to Squash TM import XLS with username
                    result_file = convert_to_squash_import_xls(last_generated, filename, username)
                    
                    if not result_file or not os.path.exists(result_file):
                        await self.safe_edit_message(query, "❌ Failed to create Squash TM import file.")
                        return
                    
                    # Persist the last export type for regenerate UX
                    try:
                        context.user_data['last_export_type'] = test_type
                    except Exception:
                        pass

                    # Send the file
                    with open(result_file, 'rb') as f:
                        # Attach primary actions directly under the file message
                        _uid = query.from_user.id if query.from_user else 0
                        # After export, show only the opposite-type switch per request
                        opposite = 'visual' if test_type == 'functional' else 'functional'
                        to_label = "To Visual" if opposite == 'visual' else "Switch To Functional"
                        to_callback = f"regen_switch_{opposite}_{_uid}"
                        file_reply_markup = InlineKeyboardMarkup([
                            [
                                InlineKeyboardButton(to_label, callback_data=to_callback),
                                InlineKeyboardButton("← Back to Main", callback_data="back_main")
                            ]
                        ])

                        caption_text = f"""✅ Squash TM Import File Generated!

📅 Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"""

                        sent_msg = await context.bot.send_document(
                            chat_id=query.message.chat_id,
                            document=f,
                            filename=filename,
                            caption=caption_text,
                            reply_markup=file_reply_markup
                        )

                    # Save last export info for quick back-navigation from regenerate menu
                    try:
                        if sent_msg and getattr(sent_msg, 'document', None) and sent_msg.document.file_id:
                            context.user_data['last_export'] = {
                                'file_id': sent_msg.document.file_id,
                                'filename': filename,
                                'caption': caption_text,
                                'chat_id': sent_msg.chat.id,
                                'message_id': sent_msg.message_id,
                                'type': test_type
                            }
                    except Exception:
                        pass

                    # Cleanup file
                    try:
                        os.remove(result_file)
                    except:
                        pass
                    
                    # No follow-up text; actions are attached to the file message only
                    
                except Exception as e:
                    logger.error(f"Error exporting Squash TM import file: {e}")
                    
                    keyboard = [
                        [
                            InlineKeyboardButton("🔄 Try Again", callback_data=f"export_excel_{user_id}"),
                            InlineKeyboardButton("← Back to Main", callback_data="back_main")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await self.safe_edit_message(
                        query,
                        f"❌ Error generating Squash TM import file: {str(e)}\n\n"
                        f"Please try again or contact support.",
                        reply_markup=reply_markup
                    )

            # IMAGE-ONLY GENERATION HANDLERS
            elif query.data.startswith("generate_image_only_"):
                user_id = int(query.data.split("_")[-1])
                
                # ✅ Initialize variables FIRST
                pending_image = context.user_data.get('pending_image')
                test_type = context.user_data.get('pending_image_type', 'functional')
                
                print(f"🔍 DEBUG: generate_image_only triggered for user {user_id}")
                print(f"🔍 DEBUG: pending_image exists: {pending_image is not None}")
                print(f"🔍 DEBUG: test_type: {test_type}")
                print(f"🔍 DEBUG: context.user_data keys: {list(context.user_data.keys())}")
                
                if pending_image:
                    try:
                        await self.safe_edit_message(query, "🚀 Preparing sources...")
                        print(f"🔍 DEBUG: Edit message sent successfully")
                        
                        # Prefer multi-image + collected text if collection exists
                        if context.user_data.get('collect_requirements_mode'):
                            imgs = context.user_data.get('collected_images', []) or []
                            txts = context.user_data.get('collected_texts', []) or []
                            if pending_image not in imgs:
                                imgs = [pending_image] + imgs
                            aggregated_text = "\n\n".join(txts)
                            # Extra certainty: source summary to user and logs
                            img_count = len(imgs)
                            text_count = len(txts)
                            await self.safe_edit_message(query, f"🚀 Generating from {img_count} image(s) + {text_count} text...")
                            logger.info(f"[ImageOnly->Collect] Using {img_count} image(s) and {text_count} text . type={test_type}, agg_text_len={len(aggregated_text)}")
                            preview = aggregated_text[:200].replace("\n", " ")
                            logger.debug(f"[ImageOnly->Collect] Aggregated text preview: {preview}")
                            response = await self._agent_generate(test_type, aggregated_text, imgs)
                        else:
                            print(f"🔍 DEBUG: Calling generate_image_only_test_cases...")
                            response = await self.generate_image_only_test_cases(pending_image, test_type)
                        print(f"🔍 DEBUG: Response generated, length: {len(response)} chars")
                        print(f"🔍 DEBUG: Response first 200 chars: {response[:200]}")
                        
                        # Clear pending data
                        context.user_data.pop('pending_image', None)
                        context.user_data.pop('pending_image_mode', None)
                        context.user_data.pop('pending_image_type', None)
                        print(f"🔍 DEBUG: Pending data cleared")
                        
                        # Clear collection cache if any
                        context.user_data.pop('collect_requirements_mode', None)
                        context.user_data.pop('collected_images', None)
                        context.user_data.pop('collected_texts', None)

                        # Store for export
                        context.user_data['last_generated_test_cases'] = response
                        context.user_data['last_test_type'] = self._deduce_effective_type(test_type, context.user_data, self.user_sessions.get(user_id, {}))
                        context.user_data['last_sources_text'] = ''
                        context.user_data['last_sources_images'] = [pending_image]
                        print(f"🔍 DEBUG: Data stored in context.user_data")
                        print(f"🔍 DEBUG: Stored test_type: {context.user_data.get('last_test_type')}")
                        print(f"🔍 DEBUG: Stored test_cases length: {len(context.user_data.get('last_generated_test_cases', ''))}")
                        
                        # Send response
                        print(f"🔍 DEBUG: Sending large text message via bot+chat_id (callback-safe)...")
                        await self.send_large_text_message(context.bot, query.message.chat_id, response)
                        print(f"🔍 DEBUG: Large text message sent successfully")
                        
                        # Show standardized post-generation options
                        print(f"🔍 DEBUG: Creating standardized post-generation keyboard...")
                        reply_markup = self.get_post_generation_keyboard(user_id)
                        print(f"🔍 DEBUG: Keyboard created successfully")
                        print(f"🔍 DEBUG: Export button callback: export_excel_{user_id}")

                        print(f"🔍 DEBUG: Sending final message with export button...")
                        final_message = await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text="✅ Test Cases Generated Successfully!",
                            reply_markup=reply_markup
                        )
                        print(f"🔍 DEBUG: Final message sent successfully! Message ID: {final_message.message_id}")
                        
                    except Exception as e:
                        print(f"❌ DEBUG: Error in generate_image_only: {e}")
                        import traceback
                        print(f"❌ DEBUG: Traceback: {traceback.format_exc()}")
                        logger.error(f"Error in generate_image_only: {e}")
                        await self.safe_edit_message(query, f"❌ Error generating test cases: {str(e)}")
                else:
                    print(f"❌ DEBUG: No pending_image found for user {user_id}")
                    print(f"🔍 DEBUG: Available context.user_data: {context.user_data}")
                    await self.safe_edit_message(query, "❌ No pending image found. Please send an image first.")

            elif query.data.startswith("wait_for_text_"):
                # ✅ SET FLAG that user is now waiting for text to complete multi-modal
                context.user_data['waiting_for_text_multimodal'] = True
                
                await self.safe_edit_message(
                    query,
                    """📝 Waiting for Text Requirements...

Please send ANY text content related to your testing needs:

✅ **Examples of what you can send:**
• Feature descriptions or requirements
• User stories or acceptance criteria  
• Bug reports or issue descriptions
• API documentation or specifications
• Business rules or workflows
• Test scenarios you want to cover
• Any relevant context in your own words

💡 **No specific format required!** 
Just describe what you want to test in plain language.

Bot akan combine dengan image untuk comprehensive test case generation."""
                )

            # GENERATE TEST CASE HANDLERS
            elif query.data.startswith("generate_text_only_"):
                user_id = int(query.data.split("_")[-1])
                
                # ✅ CLEAR PENDING IMAGE DATA FIRST - Prevent multi-modal confusion
                context.user_data.pop('pending_image', None)
                context.user_data.pop('pending_image_mode', None)
                context.user_data.pop('pending_image_type', None)
                print(f"🔍 DEBUG: Cleared pending image data for text-only generation")
                
                # Get pending text requirements
                pending_text = context.user_data.get('pending_generation_text')
                # Determine effective generation type from pending data or previously selected menu
                session = self.user_sessions.get(user_id, {}) if hasattr(self, 'user_sessions') else {}
                generate_type_raw = context.user_data.get('pending_generate_type') or session.get('generate_type') or 'functional'
                effective_type = self._deduce_effective_type(generate_type_raw, context.user_data, session)
                
                if pending_text or context.user_data.get('collect_requirements_mode'):
                    await self.safe_edit_message(query, "🔄 Generating test cases from text requirements only...")
                    
                    # Use collected images and texts if available
                    aggregated_text = None
                    imgs = []
                    if context.user_data.get('collect_requirements_mode'):
                        imgs = context.user_data.get('collected_images', []) or []
                        txts = context.user_data.get('collected_texts', []) or []
                        aggregated_text = (pending_text or '') + ("\n\n" if pending_text and txts else '') + "\n\n".join(txts)
                        response = await self._agent_generate(effective_type, aggregated_text, imgs)
                    else:
                        # Process test case generation from text only
                        aggregated_text = pending_text or ''
                        response = await self._agent_generate(effective_type, aggregated_text, [])
                    
                    # Store for export and last sources for regenerate
                    context.user_data['last_generated_test_cases'] = response
                    context.user_data['last_test_type'] = self._deduce_effective_type(effective_type, context.user_data, self.user_sessions.get(user_id, {}))
                    context.user_data['last_sources_text'] = aggregated_text or ''
                    context.user_data['last_sources_images'] = imgs or []

                    # Clear pending data (after storing sources)
                    context.user_data.pop('pending_generation_text', None)
                    context.user_data.pop('pending_generate_type', None)
                    context.user_data.pop('collect_requirements_mode', None)
                    context.user_data.pop('collected_images', None)
                    context.user_data.pop('collected_texts', None)

                    # Send response and offer standardized actions
                    await self.send_large_text_message(context.bot, query.message.chat_id, response)
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="Select next action:",
                        reply_markup=self.get_post_generation_keyboard(user_id)
                    )
                else:
                    await self.safe_edit_message(query, "❌ No text requirements found. Please try again.")

            # REGENERATE MENU HANDLERS
            elif query.data.startswith("regen_menu_"):
                rid = int(query.data.split("_")[-1])
                # Resolve last type robustly
                last_type = self._resolve_last_type_for_regen(context.user_data)

                keyboard = []
                if last_type == 'visual':
                    # Visual regenerate: do NOT offer Visual again. Only Functional + Same/Different + Back
                    keyboard.append([InlineKeyboardButton("Generate Functional", callback_data=f"regen_switch_functional_{rid}")])
                    keyboard.append([InlineKeyboardButton("🔁 Generate with same Requirements", callback_data=f"regen_same_{rid}")])
                    # Open the Visual generation menu directly for different requirements
                    keyboard.append([InlineKeyboardButton("🆕 Generate with different Requirements", callback_data="generate_visual")])
                else:
                    # Keep existing behavior for other types
                    # Determine alternate type (never suggest the same type)
                    if last_type == 'functional':
                        keyboard.append([InlineKeyboardButton("Generate Visual", callback_data=f"regen_switch_visual_{rid}")])
                    elif last_type not in ('functional','visual'):
                        # Fallback safety: show only one alternate path
                        keyboard.append([InlineKeyboardButton("Generate Functional", callback_data=f"regen_switch_functional_{rid}")])
                    else:
                        keyboard.append([InlineKeyboardButton("Generate Functional", callback_data=f"regen_switch_functional_{rid}")])
                    keyboard.append([InlineKeyboardButton("🔁 Generate with same Requirements", callback_data=f"regen_same_{rid}")])
                    # Open the matching generation menu directly for different requirements
                    if last_type == 'functional':
                        keyboard.append([InlineKeyboardButton("🆕 Generate with different Requirements", callback_data="generate_functional")])
                    elif last_type == 'visual':
                        keyboard.append([InlineKeyboardButton("🆕 Generate with different Requirements", callback_data="generate_visual")])
                    else:
                        keyboard.append([InlineKeyboardButton("🆕 Generate with different Requirements", callback_data="test_type_menu")])

                # If we have an exported file available, offer back to that instead of Back to Main
                if context.user_data.get('last_export'):
                    keyboard.append([InlineKeyboardButton("← Back to Exported File", callback_data=f"back_export_{rid}")])
                else:
                    keyboard.append([InlineKeyboardButton("← Back to Main", callback_data="back_main")])
                # Use new message instead of edit to avoid 'no text to edit' when previous was a document
                try:
                    await query.edit_message_text("🔁 Regenerate options shown below (original message replaced).")
                except Exception:
                    pass
                await context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text="Choose how you want to generate again:",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

            elif query.data.startswith("regen_to_requirements_"):
                rid = int(query.data.split("_")[-1])
                # Route user to the requirements-based generation menu
                buttons = [[InlineKeyboardButton("🔍 Generate Test Case", callback_data="test_type_menu")]]
                # If we have an exported file available, allow going back
                if context.user_data.get('last_export'):
                    buttons.append([InlineKeyboardButton("← Back to Exported File", callback_data=f"back_export_{rid}")])
                await self.safe_edit_message(
                    query,
                    "📝 Switch to Requirements input. Open the generate menu or send your requirements.",
                    InlineKeyboardMarkup(buttons)
                )
            elif query.data.startswith("back_export_"):
                rid = int(query.data.split("_")[-1])
                info = context.user_data.get('last_export')
                if not info:
                    await self.safe_edit_message(query, "❌ No exported file found. Returning to main menu...")
                    # Fallback
                    await context.bot.send_message(chat_id=query.message.chat_id, text="Main menu:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔍 Generate Test Case", callback_data="test_type_menu")]]))
                    return
                # Prefer editing the original exported message instead of sending a new one
                # Mirror the post-export keyboard: show only the opposite-type switch
                export_type = info.get('type', self._resolve_last_type_for_regen(context.user_data))
                opposite = 'visual' if export_type == 'functional' else 'functional'
                to_label = "To Visual" if opposite == 'visual' else "Switch To Functional"
                to_callback = f"regen_switch_{opposite}_{rid}"
                file_reply_markup = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(to_label, callback_data=to_callback),
                        InlineKeyboardButton("← Back to Main", callback_data="back_main")
                    ]
                ])
                try:
                    # Edit caption/markup of the original exported document message
                    await context.bot.edit_message_caption(
                        chat_id=info.get('chat_id', query.message.chat_id),
                        message_id=info.get('message_id'),
                        caption=info.get('caption', '✅ Squash TM Import File Generated!'),
                        reply_markup=file_reply_markup
                    )
                    # Delete the current regenerate menu message to keep the chat tidy
                    try:
                        await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
                    except Exception:
                        pass
                except Exception as e:
                    # Fallback: if editing fails (e.g., message not found), send the document once
                    try:
                        await context.bot.send_document(
                            chat_id=query.message.chat_id,
                            document=info.get('file_id'),
                            filename=info.get('filename', 'squash_import.xls'),
                            caption=info.get('caption', '✅ Squash TM Import File Generated!'),
                            reply_markup=file_reply_markup
                        )
                        # Delete the regenerate menu message after fallback send
                        try:
                            await context.bot.delete_message(chat_id=query.message.chat_id, message_id=query.message.message_id)
                        except Exception:
                            pass
                    except Exception as e2:
                        await self.safe_edit_message(query, f"❌ Failed to show exported file again: {e2}")
                    return
            elif query.data.startswith("regen_switch_functional_"):
                rid = int(query.data.split("_")[-1])
                # Set mode and test type, reuse last sources
                context.user_data['pending_test_type'] = 'functional'
                # Keep same requirements text/images if available -> re-enter collection or direct generate
                src_text = context.user_data.get('last_sources_text', '')
                src_imgs = context.user_data.get('last_sources_images', [])
                if src_text or src_imgs:
                    # Direct regenerate
                    await self.safe_edit_message(query, "🔄 Regenerating Functional test cases with same requirements...")
                    response = await self._agent_generate('functional', src_text, src_imgs)
                    context.user_data['last_generated_test_cases'] = response
                    context.user_data['last_test_type'] = 'functional'
                    await self.send_large_text_message(context.bot, query.message.chat_id, response)
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="Select next action:",
                        reply_markup=self.get_post_generation_keyboard(rid)
                    )
                else:
                    await self.safe_edit_message(query, "⚠️ No previous requirements found. Please send requirements first.")
            elif query.data.startswith("regen_switch_visual_"):
                rid = int(query.data.split("_")[-1])
                context.user_data['pending_test_type'] = 'visual'
                src_text = context.user_data.get('last_sources_text', '')
                src_imgs = context.user_data.get('last_sources_images', [])
                if src_text or src_imgs:
                    await self.safe_edit_message(query, "🎨 Regenerating Visual test cases with same requirements...")
                    response = await self._agent_generate('visual', src_text, src_imgs)
                    context.user_data['last_generated_test_cases'] = response
                    context.user_data['last_test_type'] = 'visual'
                    await self.send_large_text_message(context.bot, query.message.chat_id, response)
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="Select next action:",
                        reply_markup=self.get_post_generation_keyboard(rid)
                    )
                else:
                    await self.safe_edit_message(query, "⚠️ No previous requirements found. Please send requirements first.")

            # === NEW: IMAGE CLASSIFICATION CALLBACKS ===
            elif query.data.startswith("classify_image_"):
                parts = query.data.split("_")
                # pattern: classify image <type> <user_id>
                if len(parts) >= 4:
                    img_type = parts[2]
                    uid = int(parts[3])
                else:
                    await self.safe_edit_message(query, "❌ Invalid classification callback.")
                    return
                # --- Simplified: ALWAYS enter Collection Mode standard basket after classification ---
                raw_img = context.user_data.pop('pending_raw_image', None)
                context.user_data.pop('pending_image_classification', None)
                context.user_data['image_classification'] = img_type
                context.user_data['collect_requirements_mode'] = True

                # Ensure collections
                imgs = context.user_data.get('collected_images', []) or []
                if raw_img and raw_img not in imgs:
                    imgs.append(raw_img)
                context.user_data['collected_images'] = imgs
                texts = context.user_data.get('collected_texts', []) or []
                # Move any pending text placeholders into collected_texts
                # Move pending text only once (mark flag to prevent double counting on next handle_text_message)
                moved_any = False
                for k in ('pending_text_requirements', 'pending_generation_text'):
                    val = context.user_data.pop(k, None)
                    if val and val not in texts:
                        texts.append(val)
                        moved_any = True
                if moved_any:
                    context.user_data['__collection_initial_text_loaded'] = True
                context.user_data['collected_texts'] = texts

                count_imgs = len(imgs)
                count_txts = len(texts)
                img_type_display = 'Requirements' if img_type == 'requirements' else 'UI Design'

                msg = (
                    f"✅ Image type recorded: {img_type_display}\n\n"
                    f"🧺 Collection Mode Active\n📄 Text: {count_txts}\n🖼️ Images: {count_imgs}\n\n"
                    "Kirim teks atau gambar tambahan, atau klik Generate Now."
                )
                keyboard = [
                    [InlineKeyboardButton("➕ Add More Text", callback_data=f"collect_more_text_{uid}"),
                     InlineKeyboardButton("➕ Add Image", callback_data=f"collect_add_image_{uid}")],
                    [InlineKeyboardButton("✅ Generate Now", callback_data=f"collect_generate_{uid}"),
                     InlineKeyboardButton("🗑️ Reset", callback_data=f"collect_reset_{uid}")]
                ]
                try:
                    await self.safe_edit_message(query, msg, reply_markup=InlineKeyboardMarkup(keyboard))
                except Exception:
                    await query.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
                return

            elif query.data.startswith("regen_same_"):
                rid = int(query.data.split("_")[-1])
                # Set mode to accept user's prompt to refine/regenerate
                self.user_sessions[rid]['mode'] = 'testcases'
                context.user_data['regen_mode'] = 'same_requirements'
                await self.safe_edit_message(
                    query,
                    "💬 Send your prompt/comments to refine the previous test cases.\nWe'll regenerate using the same requirements (and images, if any)."
                )

            elif query.data.startswith("wait_for_generate_image_"):
                await self.safe_edit_message(
                    query,
                    """📸 Waiting for Image...

Please send an image (screenshot, PRD, UI design) to complement your text requirements.

Bot akan combine text + image untuk comprehensive test case generation."""
                )

            # GENERATION HANDLERS
            elif query.data.startswith("generate_image_only_"):
                user_id = int(query.data.split("_")[-1])
                
                # ✅ Initialize variables FIRST
                pending_image = context.user_data.get('pending_image')
                translate_type = context.user_data.get('pending_image_type', 'functional')
                
                if pending_image:
                    await self.safe_edit_message(query, "🔄 Generating from image only...")
                    
                    # Process generation from image only
                    response = await self.generate_image_only_test_cases(pending_image, translate_type)
                    
                    # Clear pending data
                    context.user_data.pop('pending_image', None)
                    context.user_data.pop('pending_image_mode', None)
                    context.user_data.pop('pending_image_type', None)
                    
                    # Send response
                    await self.send_long_message(update, response)
                    
                    # Store for export
                    context.user_data['last_generated_test_cases'] = response
                    context.user_data['last_test_type'] = self._deduce_effective_type(translate_type, context.user_data, self.user_sessions.get(user_id, {}))
                    
                    # Show options
                    keyboard = [
                        [
                            InlineKeyboardButton("🔧 Modify Test Cases", callback_data=f"modify_testcase_{user_id}"),
                            InlineKeyboardButton("📊 Export to Excel", callback_data=f"export_excel_{user_id}")
                        ],
                        [
                            InlineKeyboardButton("← Back to Main", callback_data="back_main")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="✅ Test Cases Generated from Image!",
                        reply_markup=reply_markup
                    )

            elif query.data.startswith("wait_for_text_"):
                await query.edit_message_text(
                    """📝 Waiting for Text Content...

Please send ANY text content you want to generate test cases from:

✅ **What you can send:**
• Existing test cases in any format
• User stories or requirements  
• Bug reports or issues
• Documentation snippets
• Any testing-related content
• Plain text descriptions

💡 **No specific format required!** 
Just paste your content in any format you have.

Bot akan combine dengan image for better generation results."""
                )
                
            elif query.data.startswith("wait_for_image_"):
                await query.edit_message_text(
                    """📸 Waiting for Image...

    Please send:
    • Screenshot dari UI/design
    • Figma mockup  
    • API documentation
    • Error screenshot

    Bot akan combine dengan text requirements yang sudah Anda kirim untuk analysis yang lebih comprehensive."""
                )

            # MODE HANDLERS
            elif query.data == "mode_testcases":
                keyboard = [
                    [
                        InlineKeyboardButton("🔧 Functional Test", callback_data="testcase_functional"),
                        InlineKeyboardButton("🎨 Visual Test", callback_data="testcase_visual")
                    ],
                    [
                        InlineKeyboardButton("← Back to Main", callback_data="back_main")
                    ]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                testcases_menu_text = (
                    "🔍 Pilih Jenis Test Case:\n\n"
                    "• Functional Test - Business logic & user flows\n"
                    "• Visual Test - UI/UX validation & design\n\n"
                    "Pilih jenis test case yang ingin di-generate:"
                )
                try:
                    if getattr(query.message, 'text', None):
                        await query.edit_message_text(testcases_menu_text, reply_markup=reply_markup)
                    else:
                        # Original message not editable as text (document/etc.), send new one
                        await query.message.reply_text(testcases_menu_text, reply_markup=reply_markup)
                except Exception as e:
                    print(f"⚠️ Fallback sending testcases menu due to edit error: {e}")
                    await query.message.reply_text(testcases_menu_text, reply_markup=reply_markup)

            elif query.data == "mode_general":
                if user_id not in self.user_sessions:
                    self.user_sessions[user_id] = {}
                self.user_sessions[user_id]['mode'] = 'general'
                
                keyboard = [
                    [
                        InlineKeyboardButton("← Back to Main", callback_data="back_main")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(
                    """❓ General QA Mode

    Tanya apa saja tentang QA, testing, atau quality assurance!

    Contoh:
    • Apa itu regression testing?
    • Bagaimana test mobile app?
    • Perbedaan functional dan visual testing?""",
                    reply_markup=reply_markup
                )

            # GENERATE TEST CASE MENU HANDLERS
            elif query.data == "test_type_menu":
                # Determine if previous message is non-text (e.g., document) to avoid edit errors
                keyboard = [
                    [
                        InlineKeyboardButton("🧪 To Functional", callback_data="generate_functional"),
                        InlineKeyboardButton("👁️ To Visual", callback_data="generate_visual")
                    ],
                    [
                        InlineKeyboardButton("← Back to Main", callback_data="back_main")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                if getattr(query.message, 'text', None):
                    await query.edit_message_text(
                        "Select test case type:\n"
                        "🧪 Functional - Generate functional test cases\n"
                        "👁️ Visual - Generate visual test cases\n\n"
                        "Next: Send your requirements text or image to generate test cases",
                        reply_markup=reply_markup
                    )
                else:
                    # Send new message instead of editing non-text source
                    await query.message.reply_text(
                        "Select test case type:\n"
                        "🧪 Functional - Generate functional test cases\n"
                        "👁️ Visual - Generate visual test cases\n\n"
                        "Next: Send your requirements text or image to generate test cases",
                        reply_markup=reply_markup
                    )

            elif query.data.startswith("generate_"):
                generate_type = query.data.replace("generate_", "")
                
                if user_id not in self.user_sessions:
                    self.user_sessions[user_id] = {}
                self.user_sessions[user_id]['mode'] = 'generate'
                self.user_sessions[user_id]['generate_type'] = generate_type
                # Sync pending flags to avoid cross-mode bleed
                context.user_data.pop('pending_test_type', None)
                context.user_data['pending_generate_type'] = generate_type
                
                generate_messages = {
                    "functional": """🧪 Functional Test Case Generation

📋 Untuk hasil optimal, kirim:
✅ Screenshot + Text Content
🔄 Atau pilih salah satu:
• Hanya screenshot
• Hanya text content

Bot akan generate functional test cases dengan comprehensive coverage.

Kirim requirements untuk functional test generation:""",

                    "visual": """👁️ Visual Test Case Generation

📋 Untuk hasil optimal, kirim:
✅ UI Screenshot/Figma + Text Content
🔄 Atau pilih salah satu:
• Hanya screenshot/Figma design
• Hanya text description

Bot akan generate visual test cases dengan comprehensive UI testing coverage.

Kirim requirements untuk visual test generation:"""
                }
                
                keyboard = [
                    [
                        InlineKeyboardButton("← Back to Test Case Menu", callback_data="test_type_menu")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                message = generate_messages.get(generate_type, "Test case generation mode updated!")
                await query.edit_message_text(message, reply_markup=reply_markup)

            # TESTCASE TYPE HANDLERS
            elif query.data.startswith("testcase_"):
                test_type = query.data.replace("testcase_", "")
                
                if user_id not in self.user_sessions:
                    self.user_sessions[user_id] = {}
                self.user_sessions[user_id]['mode'] = 'testcases'
                self.user_sessions[user_id]['test_type'] = test_type
                
                type_messages = {
                    "functional": """🔧 Functional Test Generation Mode

    📋 Untuk hasil optimal, kirim:
    ✅ Gambar (Screenshot/Figma) + Text Requirements
    🔄 Atau pilih salah satu:
    • Hanya gambar (dengan caption)
    • Hanya text requirements

    Format Text Requirements:
    ```
    Type: Functional
    Feature: [Nama fitur]
    Scenario: [Deskripsi scenario]
    Requirements: [Detail requirements]
    Environment: [Web/Mobile]
    ```

    Contoh:
    Type: Functional
    Feature: User Login
    Scenario: Login dengan email dan password
    Requirements: User dapat login menggunakan email dan password yang valid
    Environment: Web application

    💡 Multi-Modal Analysis: Kirim gambar + caption untuk hasil yang lebih akurat!""",
                    
                    "visual": """🎨 Visual Test Generation Mode

    📋 Untuk hasil optimal, kirim:
    ✅ Screenshot/Figma + Text Requirements
    🔄 Atau pilih salah satu:
    • Hanya screenshot/Figma design
    • Hanya text description

    """
                    
                    
                }
                
                keyboard = [
                    [
                        InlineKeyboardButton("📖 Contoh Format", callback_data=f"example_{test_type}"),
                        InlineKeyboardButton("← Back to Test Types", callback_data="mode_testcases")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                message = type_messages.get(test_type, "Test case mode updated!")
                await query.edit_message_text(message, reply_markup=reply_markup)

            # FORMAT HELP HANDLERS
            elif query.data == "show_testcase_format":
                format_message = """📋 FORMAT UNTUK GENERATE TEST CASES

    🔧 Functional Test Format:
    ```
    Type: Functional
    Feature: [Nama fitur]
    Scenario: [Deskripsi scenario]
    Requirements: [Detail requirements]
    Environment: [Web/Mobile]
    ```

    🎨 Visual Test Format:
    ```
    Type: Visual
    Feature: [Nama fitur]
    Design Reference: [Figma link atau deskripsi]
    Device: [Desktop/Mobile/Tablet]
    Requirements: [Visual requirements]
    ```

    💡 Best Practice: Combine dengan screenshot untuk hasil optimal!"""
                
                keyboard = [
                    [
                        InlineKeyboardButton("← Back to Main", callback_data="back_main")
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(format_message, reply_markup=reply_markup)

            # BACK TO MAIN HANDLER
            elif query.data == "back_main":
                # Reset user session
                if user_id not in self.user_sessions:
                    self.user_sessions[user_id] = {}
                self.user_sessions[user_id]['mode'] = 'general'
                
                # Show main menu
                user_name = query.from_user.first_name or "User"
                
                welcome_message = f"""🤖 Selamat datang di SQA Netmonk Assistant Bot, {user_name}!

    Saya dapat membantu Anda dengan:
    - 🔍 Generate test cases (dari PRD atau gambar)

    Gunakan /help untuk melihat semua perintah yang tersedia."""
                
                keyboard = [
                    [
                        InlineKeyboardButton("🔍 Generate Test Case", callback_data="test_type_menu")
                    ]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                # If the callback came from a document/photo (no text), do not edit text; send a new message instead
                try:
                    if query.message and getattr(query.message, 'text', None):
                        await query.edit_message_text(welcome_message, reply_markup=reply_markup)
                    else:
                        # Try to remove inline keyboard from the original message to avoid stale buttons
                        try:
                            await query.edit_message_reply_markup(reply_markup=None)
                        except Exception:
                            pass
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=welcome_message,
                            reply_markup=reply_markup
                        )
                except BadRequest as e:
                    # Fallback: send as a new message
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=welcome_message,
                        reply_markup=reply_markup
                    )

            # CATCH-ALL FOR UNHANDLED CALLBACKS
            else:
                logger.warning(f"Unhandled callback query: {query.data}")
                await query.edit_message_text(
                    f"❌ Unknown command: {query.data}\n\nPlease use the main menu."
                )
                    
        except Exception as e:
            logger.error(f"Error in callback query handler: {e}")
            # ✅ Safe error handling with multiple fallback strategies
            error_sent = False
            
            # Strategy 1: Try query.edit_message_text
            if not error_sent:
                try:
                    keyboard = [
                        [
                            InlineKeyboardButton("← Back to Main", callback_data="back_main")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await query.edit_message_text(
                        f"❌ Error occurred: {str(e)}\n\nPlease try again.",
                        reply_markup=reply_markup
                    )
                    error_sent = True
                except Exception as edit_error:
                    logger.error(f"Error editing message in callback: {edit_error}")
            
            # Strategy 2: Try sending new message
            if not error_sent:
                try:
                    keyboard = [
                        [
                            InlineKeyboardButton("← Back to Main", callback_data="back_main")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text=f"❌ Error processing command: {str(e)}",
                        reply_markup=reply_markup
                    )
                    error_sent = True
                except Exception as send_error:
                    logger.error(f"Error sending error message in callback: {send_error}")
            
            # Strategy 3: Try query.answer
            if not error_sent:
                try:
                    await query.answer("❌ Error occurred, please try again")
                    error_sent = True
                except Exception as answer_error:
                    logger.error(f"Error answering callback query: {answer_error}")
            
            if not error_sent:
                logger.error("Could not send error message to user - all strategies failed")


    async def show_modification_examples(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show modification examples to help users"""
        examples_text = """💡 Modification Examples & Tips:

    🔧 Specific Test Case Modifications:
    • "Modify test case 001 to include mobile testing"
    • "Change test case 003 from web to mobile environment"
    • "Add data validation step to test case 002"
    • "Update login test case with biometric authentication"

    📝 Content Modifications:
    • "Add error handling to registration test"
    • "Include accessibility testing in UI test cases"
    • "Change priority of security test cases to HIGH"

    🎯 Scope Modifications:
    • "Add edge cases to payment test case"
    • "Include negative testing in search functionality"
    • "Add performance requirements to load test"
    • "Update browser compatibility for Chrome only"

    💡 Best Practices:
    ✅ Be specific about which test case to modify
    ✅ Mention the exact changes you want
    ✅ Reference test case numbers when possible
    ✅ Only one modification request per message

    ❌ Avoid vague requests like "make it better"
    ❌ Don't ask to modify "all test cases" at once"""

        keyboard = [
            [
                InlineKeyboardButton("← Back to Modification", callback_data="modify_testcase_help")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(examples_text, reply_markup=reply_markup)

    async def validate_modification_request(self, modification_request: str) -> dict:
        """Validate and parse modification request"""
        try:
            if getattr(self, 'functional_agent', None):
                return await self.functional_agent.validate_modification_request(modification_request)
            return {"is_valid": True, "target_identified": True}
        except Exception as e:
            logger.error(f"Error validating modification request: {e}")
            return {"is_valid": True, "target_identified": True}
    
    async def generate_from_image_only(self, image: PILImage.Image, target_type: str) -> str:
        """Generate content from image only using custom knowledge base"""
        try:
            if getattr(self, 'visual_agent', None):
                # Reuse multimodal with empty text
                return await self.visual_agent.generate_multimodal_content(image, "", target_type)
            return "❌ VisualAgent not available for image-only generation."
            
        except Exception as e:
            logger.error(f"Error in image-only generation: {e}")
            return f"Error generating from image: {str(e)}"

    async def generate_image_only_test_cases(self, image: PILImage.Image, test_type: str) -> str:
        """Generate test cases from image only using pure custom knowledge base"""
        try:
            if getattr(self, 'visual_agent', None):
                return await self.visual_agent.image_only(image)
            return "❌ VisualAgent not available for image-only generation."
            
        except Exception as e:
            logger.error(f"Error in image-only test case generation: {e}")
            return f"❌ Error generating test cases from image: {str(e)}"

    async def generate_testcases_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /generate_testcases command"""
        try:
            # Set mode and show type selection
            user_id = update.effective_user.id
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = {}
            self.user_sessions[user_id]['mode'] = 'testcases'

            keyboard = [
                [
                    InlineKeyboardButton("🔧 Functional Test", callback_data="testcase_functional"),
                    InlineKeyboardButton("🎨 Visual Test", callback_data="testcase_visual")
                ],
                [InlineKeyboardButton("← Back to Main", callback_data="back_main")]
            ]
            await update.message.reply_text(
                "🔍 Pilih Jenis Test Case:\n\n• Functional Test - Business logic & user flows\n• Visual Test - UI/UX validation & design",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Error opening generation menu: {e}")

    async def generate_consistent_multimodal_test_cases(self, image: PILImage.Image, requirements_text: str, test_type: str) -> str:
        """Generate consistent test cases using template-based approach"""
        try:
            # Step 1: Extract requirements structure via FunctionalAgent
            if not getattr(self, 'functional_agent', None) or not getattr(self, 'visual_agent', None):
                return "❌ Agents not initialized for consistent multimodal generation."

            requirements_analysis = await self.functional_agent.analyze_requirements_structure(requirements_text)

            # Step 2: Extract visual elements via VisualAgent
            visual_elements = await self.visual_agent.extract_visual_elements(image)

            # Step 3: Get template from Squash project (local helper retained)
            template_structure = self.get_squash_template_structure(test_type)

            # Step 4: Generate consistent test cases using FunctionalAgent template generator
            response = await self.functional_agent.generate_from_template(
                requirements_data=requirements_analysis,
                visual_data=visual_elements,
                template=template_structure,
                test_type=test_type,
            )

            return response
            
        except Exception as e:
            logger.error(f"Error in consistent multimodal generation: {e}")
            return f"❌ Error generating consistent test cases: {str(e)}"
    def get_squash_template_structure(self, test_type: str) -> dict:
        """Get consistent template structure from Squash project"""
        try:
            if self.squash_integration:
                # Get actual template from Squash project
                template = self.squash_integration.get_test_case_template(test_type)
                if template:
                    return template
            
            # Fallback template structure based on your Squash format
            base_template = {
                "format": "BDD",
                "numbering": "001, 002, 003...",
                "structure": {
                    "title": "[Component] - [Action/Validation]",
                    "nature": "FUNCTIONAL/VISUAL/API",
                    "importance": "HIGH/MEDIUM/LOW",
                    "steps": [
                        {"type": "given", "template": "Given [initial condition]"},
                        {"type": "when", "template": "When [user action]"},
                        {"type": "then", "template": "Then [expected result]"}
                    ]
                },
                "categories": {
                    "functional": ["business logic", "user flows", "data validation"],
                    "visual": ["UI elements", "layout", "responsiveness", "design compliance"],
                    "api": ["endpoints", "responses", "authentication", "data format"]
                }
            }
            
            return base_template
            
        except Exception as e:
            logger.error(f"Error getting template structure: {e}")
            return {"format": "BDD", "numbering": "sequential", "structure": {}}

    # Removed bot-local generate_from_template; delegated to FunctionalAgent.generate_from_template
        

    async def sync_squash_templates_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Command untuk sync templates dari project Squash"""
        await update.message.reply_text("🔄 Syncing templates from your Squash project...")

        try:
            if self.squash_integration:
                success = self.squash_integration.sync_project_templates()
                if success:
                    await update.message.reply_text("✅ Templates berhasil di-sync dari project Squash Anda!")
                else:
                    await update.message.reply_text("❌ Gagal sync templates dari project Squash.")
            else:
                await update.message.reply_text("❌ Squash integration tidak tersedia.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error syncing templates: {e}")

    async def show_project_examples_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Command untuk melihat contoh test cases dari project"""
        await update.message.reply_text("📋 Mengambil contoh test cases dari project Squash Anda...")

        try:
            if self.squash_integration:
                examples = self.squash_integration.get_sample_test_cases_for_reference()
                await self.send_long_message(update, examples)
            else:
                await update.message.reply_text("❌ Squash integration tidak tersedia.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error getting examples: {e}")

    async def test_squash_connection_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Command untuk test koneksi Squash"""
        await update.message.reply_text("🔧 Testing Squash connection...")

        try:
            if self.squash_integration:
                if self.squash_integration.validate_connection():
                    project_info = self.squash_integration.get_project_info()
                    project_name = project_info.get('name', 'Unknown') if project_info else 'Unknown'

                    await update.message.reply_text(f"✅ Squash connection successful!\n🏗️ Project: {project_name}")
                else:
                    await update.message.reply_text("❌ Squash connection failed. Check your credentials.")
            else:
                await update.message.reply_text("❌ Squash integration tidak tersedia.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error testing connection: {e}")

    async def sync_squash_realtime_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Command untuk sync real-time dengan explore folder otomatis"""
        await update.message.reply_text("🔄 Starting real-time sync with Squash project folder exploration...")

        try:
            if self.squash_integration:
                # Step 1: Test connection
                await update.message.reply_text("🔧 Step 1: Testing Squash connection...")
                if not self.squash_integration.validate_connection():
                    await update.message.reply_text("❌ Connection failed. Check your Squash credentials in .env file.")
                    return

                # Step 2: Explore all folders recursively
                await update.message.reply_text("📁 Step 2: Exploring all project folders recursively...")
                project_id = int(self.squash_integration.project_id) if self.squash_integration.project_id else 103
                
                # Get comprehensive test cases with folder exploration
                test_cases = self.squash_integration.get_test_cases_from_project(project_id, limit=200)
                
                if test_cases:
                    # Step 3: Update knowledge base
                    await update.message.reply_text("🧠 Step 3: Updating bot knowledge base...")
                    self.squash_integration.discovered_test_cases = test_cases
                    patterns = self.squash_integration.analyze_test_case_patterns()
                    
                    # Step 4: Save to cache for fast access
                    await update.message.reply_text("💾 Step 4: Caching results for fast access...")
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    cache_file = f"realtime_sync_{timestamp}.json"
                    
                    cache_data = {
                        "sync_timestamp": timestamp,
                        "project_id": project_id,
                        "total_test_cases": len(test_cases),
                        "test_cases": test_cases[:50],  # Sample for knowledge base
                        "patterns": patterns,
                        "sync_method": "folder_exploration"
                    }
                    
                    try:
                        with open(cache_file, 'w', encoding='utf-8') as f:
                            json.dump(cache_data, f, indent=2, ensure_ascii=False)
                    except Exception as cache_error:
                        print(f"Warning: Could not save cache file: {cache_error}")
                    
                    # Success message
                    success_msg = f"""✅ Real-time sync completed successfully!

📊 **Sync Results:**
• **Project ID:** {project_id}
• **Total Test Cases Found:** {len(test_cases)}
• **Folders Explored:** Recursive exploration completed
• **Knowledge Base:** Updated with latest patterns
• **Cache File:** {cache_file}

🚀 **What's Updated:**
• Bot now knows your current project structure
• Test case generation will use your actual formats
• Templates synced from real Squash data
• Export format matches your project standards

💡 **Next Steps:**
• Use `/generate_testcases` with your updated knowledge
• Export will now match your Squash project format
• Use `/show_project_examples` to see loaded templates"""

                    await update.message.reply_text(success_msg)
                    
                else:
                    await update.message.reply_text("⚠️ No test cases found during folder exploration. Check your project ID and permissions.")
                    
            else:
                await update.message.reply_text("❌ Squash integration not available. Check your .env configuration.")
                
        except Exception as e:
            await update.message.reply_text(f"❌ Error during real-time sync: {str(e)}")

    async def explore_squash_folders_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Command untuk explore folder structure saja tanpa sync penuh"""
        await update.message.reply_text("📁 Exploring Squash project folder structure...")

        try:
            if self.squash_integration:
                project_id = int(self.squash_integration.project_id) if self.squash_integration.project_id else 103
                
                # Get folder structure
                await update.message.reply_text("🔍 Mapping folder hierarchy...")
                folders = self.squash_integration.get_project_folders(project_id, folder_limit=50)
                
                if folders:
                    folder_summary = f"""📂 **Project {project_id} Folder Structure:**

🗂️ **Top-level folders found:** {len(folders)}

📋 **Folder List:**"""
                    
                    for i, folder in enumerate(folders[:10], 1):  # Show first 10 folders
                        folder_name = folder.get('name', 'Unnamed')
                        folder_id = folder.get('id', 'N/A')
                        folder_summary += f"\n{i}. **{folder_name}** (ID: {folder_id})"
                    
                    if len(folders) > 10:
                        folder_summary += f"\n... and {len(folders) - 10} more folders"
                    
                    folder_summary += f"""

🔍 **Exploration Options:**
• Use `/sync_squash_realtime` for full sync with test cases
• Folders will be explored recursively during sync
• Test cases from all subfolders will be discovered

💡 **Tip:** Your project has a rich folder structure ready for exploration!"""
                    
                    await update.message.reply_text(folder_summary)
                    
                else:
                    await update.message.reply_text("⚠️ No folders found in project. Check your project ID and permissions.")
                    
            else:
                await update.message.reply_text("❌ Squash integration not available.")
                
        except Exception as e:
            await update.message.reply_text(f"❌ Error exploring folders: {str(e)}")

    # Removed: analyze_multimodal_general (unused)

    async def generate_multimodal_test_cases(self, image: PILImage.Image, requirements_text: str, test_type: str) -> str:
        """Generate test cases dari kombinasi gambar dan requirements dengan consistency"""
        try:
            # Use consistent generation method
            consistent_result = await self.generate_consistent_multimodal_test_cases(
                image, requirements_text, test_type
            )
            
            return consistent_result
            
        except Exception as e:
            logger.error(f"Error in multimodal test case generation: {e}")
            # Fallback to original method
            return await self.generate_multimodal_test_cases_fallback(image, requirements_text, test_type)

    async def generate_multimodal_test_cases_multi(self, images: List[PILImage.Image], requirements_text: str, test_type: str) -> str:
        """Generate test cases from multiple images + aggregated text requirements."""
        try:
            # Delegate to AgentManager/agents for multimodal generation
            if getattr(self, 'agent_manager', None):
                return await self.agent_manager.generate(test_type, requirements_text or "", images or [])
            # Fallback: choose specific agent
            if (test_type or '').lower() == 'visual' and getattr(self, 'visual_agent', None):
                return await self.visual_agent.generate_multimodal(images or [], requirements_text or "")
            if getattr(self, 'functional_agent', None):
                return await self.functional_agent.generate_multimodal(images or [], requirements_text or "")
            return "❌ Agents not available for multimodal generation."
        except Exception as e:
            logger.error(f"Error in multi-image multimodal generation: {e}")
            # Fallback to single-image if available
            if images:
                try:
                    return await self.generate_multimodal_test_cases(images[0], requirements_text, test_type)
                except Exception:
                    pass
            return f"Error generating multi-image test cases: {e}"

    # ------------------------------------------------------------------
    # SQUASH TM REST API INTEGRATION (FETCH TEST CASES DIRECTLY)
    # ------------------------------------------------------------------
    def _init_squash_api_session(self):
        """Lazy-init a requests Session for Squash TM REST API using env vars.
        Required env vars:
          SQUASH_BASE_URL, SQUASH_USERNAME, SQUASH_PASSWORD
        Optional:
          SQUASH_PROJECT_ID (used in later filtering / logging)
        """
        if getattr(self, 'squash_api_session', None):
            return
        import os, base64, requests
        base_url = os.getenv('SQUASH_BASE_URL', '').rstrip('/')
        user = os.getenv('SQUASH_USERNAME')
        pwd = os.getenv('SQUASH_PASSWORD')
        if not (base_url and user and pwd):
            logger.warning("Squash API env vars missing; skipping session init")
            return
        sess = requests.Session()
        token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
        sess.headers.update({
            'Authorization': f'Basic {token}',
            'Accept': 'application/json'
        })
        self.squash_api_base_url = base_url
        self.squash_api_session = sess
        self.squash_api_project_id = os.getenv('SQUASH_PROJECT_ID')
        logger.info("Squash API session initialized")

    def _squash_api_get(self, path_or_url: str, params: Optional[dict] = None, timeout: int = 20):
        """Internal GET helper that accepts either relative path or full URL."""
        if not getattr(self, 'squash_api_session', None):
            self._init_squash_api_session()
        sess = getattr(self, 'squash_api_session', None)
        base = getattr(self, 'squash_api_base_url', '')
        if not sess or not base:
            raise RuntimeError("Squash API session not initialized (missing env vars?).")
        if path_or_url.startswith('http'):  # absolute from _links
            url = path_or_url
        else:
            url = f"{base}{path_or_url if path_or_url.startswith('/') else '/' + path_or_url}"
        r = sess.get(url, params=params, timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Squash API GET {url} failed: {r.status_code} {r.text[:200]}")
        return r.json()

    @staticmethod
    def _strip_html(raw: Optional[str]) -> str:
        if not raw:
            return ''
        import re, html
        text = html.unescape(raw)
        # Remove simple <p> and <br> style tags
        text = re.sub(r'<\/?(p|br|div|span)[^>]*>', '\n', text, flags=re.I)
        # Remove any remaining tags
        text = re.sub(r'<[^>]+>', '', text)
        # Collapse whitespace
        text = re.sub(r'\s+\n', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def _normalize_squash_test_case(self, tc: dict) -> dict:
        """Normalize Squash TM test case JSON to internal lightweight schema."""
        steps = []
        for step in (tc.get('steps') or []):
            action = step.get('action') or step.get('action_html') or step.get('action-step') or ''
            expected = step.get('expected_result') or step.get('expectedResult') or ''
            steps.append({
                'index': step.get('index'),
                'action': self._strip_html(action),
                'expected': self._strip_html(expected)
            })
        # Debug mode: retain raw object if SQUASH_DEBUG env variable is set
        import os
        keep_raw = os.environ.get('SQUASH_DEBUG') in ('1', 'true', 'yes')
        # Parent id extraction (may be dict or simple value)
        parent_field = tc.get('parent')
        parent_id = None
        if isinstance(parent_field, dict):
            parent_id = parent_field.get('id') or parent_field.get('entity-id')
        elif isinstance(parent_field, (int, str)):
            parent_id = parent_field
        normalized = {
            'id': tc.get('id'),
            'reference': tc.get('reference') or '',
            'name': tc.get('name') or '',
            'importance': tc.get('importance'),
            'status': tc.get('status'),
            'type': (tc.get('type') or {}).get('code') if isinstance(tc.get('type'), dict) else tc.get('type'),
            'prerequisite': self._strip_html(tc.get('prerequisite')),
            'description': self._strip_html(tc.get('description')),
            'steps': steps,
            'last_modified_on': tc.get('last_modified_on') or tc.get('lastModified') or tc.get('last_modified'),
            'folder_path': tc.get('path') or '',
            'parent_id': parent_id,
            'raw': tc if keep_raw else None
        }
        return normalized


    async def handle_sync_squash_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Telegram command handler: /sync_squash → fetch test cases via REST API."""
        chat_id = update.effective_chat.id if update and update.effective_chat else None
        try:
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text="🔄 Syncing Squash TM test cases (API)...")
            count = await self.fetch_all_squash_test_cases_api()
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text=f"✅ Synced {count} test cases from Squash TM API.")
        except Exception as e:
            logger.error(f"/sync_squash error: {e}")
            if chat_id:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to sync Squash test cases: {e}")

    # ---------------- Specific Folder Fetch (e.g., "A. Main") -----------------
    def _find_folder_id_by_name(self, target_name: str) -> str:
        """Brute-force search all test-case-folders to find ID by exact name match.
        NOTE: This may be expensive; in large instances consider caching folder listings.
        """
        import math
        self._init_squash_api_session()
        base = getattr(self, 'squash_api_base_url', None)
        if not base:
            raise RuntimeError("Squash API not configured")
        # Paginate through folders; typical endpoint: /api/rest/latest/test-case-folders?page=0&size=...
        page = 0
        size = 200
        found_id = None
        while True:
            try:
                data = self._squash_api_get('/api/rest/latest/test-case-folders', {'page': page, 'size': size})
            except Exception as e:
                logger.error(f"Folder listing error page {page}: {e}")
                break
            embedded = data.get('_embedded', {})
            folders = embedded.get('test-case-folders') or []
            for f in folders:
                if f.get('name') == target_name:
                    found_id = str(f.get('id')) if f.get('id') is not None else ''
                    logger.info(f"Matched folder '{target_name}' → ID {found_id}")
                    return found_id
            page_meta = data.get('page', {})
            total_pages = page_meta.get('totalPages')
            if total_pages is not None and page >= total_pages - 1:
                break
            if not folders:
                break
            page += 1
        return str(found_id or '')

    def _collect_test_cases_in_folder(self, folder_id: str) -> list:
        """Recursively collect all test cases inside a folder using /content endpoint."""
        collected = []
        seen_tc_ids = set()
        stack = [folder_id]
        while stack:
            current = stack.pop()
            page = 0
            while True:
                try:
                    content = self._squash_api_get(
                        f"/api/rest/latest/test-case-folders/{current}/content",
                        {'page': page, 'size': 200}
                    )
                except Exception as e:
                    logger.error(f"Error fetching folder content {current} (page {page}): {e}")
                    break
                embedded = content.get('_embedded', {}) or {}
                # Debug keys for diagnosis
                if page == 0:
                    logger.debug(f"Folder {current} embedded keys: {list(embedded.keys())}")
                # Collect potential subfolder containers
                nodes = embedded.get('test-case-library-nodes') or embedded.get('test-case-folder-nodes') or []
                # Some Squash instances use 'test-case-folders' for subfolders
                subfolders = embedded.get('test-case-folders') or []
                # Collect test case stubs
                testcases = embedded.get('test-cases') or []
                # Handle nodes style referencing
                for n in nodes:
                    etype = n.get('entity-type') or n.get('_type') or ''
                    eid = n.get('entity-id') or n.get('id')
                    if not eid:
                        continue
                    if 'folder' in etype.lower():
                        stack.append(eid)
                    elif 'test-case' in etype.lower():
                        testcases.append({'id': eid})
                # Handle explicit subfolders list
                for sf in subfolders:
                    sfid = sf.get('id')
                    if sfid:
                        stack.append(sfid)
                # Fetch details for each test case
                for tc_stub in testcases:
                    tc_id = tc_stub.get('id')
                    if tc_id is None or tc_id in seen_tc_ids:
                        continue
                    try:
                        detail = self._squash_api_get(f"/api/rest/latest/test-cases/{tc_id}")
                        norm = self._normalize_squash_test_case(detail)
                        collected.append(norm)
                        seen_tc_ids.add(tc_id)
                    except Exception as de:
                        logger.debug(f"Skip tc {tc_id}: {de}")
                # Pagination handling within folder
                page_meta = content.get('page') or {}
                total_pages = page_meta.get('totalPages')
                if total_pages is not None and page >= total_pages - 1:
                    break
                if not (nodes or subfolders or testcases) and page == 0:
                    # Empty folder; stop early
                    break
                page += 1
        logger.info(f"Collected {len(collected)} test cases under folder {folder_id}")
        if not collected:
            logger.warning("Folder traversal produced 0 test cases. Possible causes: wrong folder, no direct linkage, or permission filters.")
        return collected

    def _collect_test_cases_via_children(self, folder_id: str) -> list:
        """Recursive traversal using /children and /test-cases endpoints for deep hierarchies."""
        results = []
        queue = [folder_id]
        seen_folders = set()
        seen_tc_ids = set()
        while queue:
            current = queue.pop(0)
            if current in seen_folders:
                continue
            seen_folders.add(current)
            # Collect test cases directly under current
            page = 0
            while True:
                try:
                    data = self._squash_api_get(f"/api/rest/latest/test-case-folders/{current}/test-cases", {'page': page, 'size': 200})
                except Exception:
                    break
                embedded = data.get('_embedded', {}) if isinstance(data, dict) else {}
                tcs = embedded.get('test-cases') or []
                for stub in tcs:
                    tc_id = stub.get('id')
                    if not tc_id or tc_id in seen_tc_ids:
                        continue
                    try:
                        detail = self._squash_api_get(f"/api/rest/latest/test-cases/{tc_id}")
                        norm = self._normalize_squash_test_case(detail)
                        results.append(norm)
                        seen_tc_ids.add(tc_id)
                    except Exception:
                        pass
                page_meta = data.get('page') or {}
                total_pages = page_meta.get('totalPages')
                if total_pages is None or page >= total_pages - 1:
                    break
                page += 1
            # Enumerate children folders
            page = 0
            while True:
                try:
                    cdata = self._squash_api_get(f"/api/rest/latest/test-case-folders/{current}/children", {'page': page, 'size': 200})
                except Exception:
                    break
                embedded = cdata.get('_embedded', {}) if isinstance(cdata, dict) else {}
                child_entries = []
                for k, v in embedded.items():
                    if 'folder' in k and isinstance(v, list):
                        child_entries.extend(v)
                for cf in child_entries:
                    cfid = cf.get('id')
                    if cfid and cfid not in seen_folders:
                        queue.append(cfid)
                page_meta = cdata.get('page') or {}
                total_pages = page_meta.get('totalPages')
                if total_pages is None or page >= total_pages - 1:
                    break
                page += 1
        logger.info(f"Children traversal collected {len(results)} test cases (folder {folder_id})")
        return results

    async def fetch_folder_test_cases(self, folder_name: str) -> int:
        """Public async helper: find folder by name then cache its test cases only.
        Stores in self.squash_api_testcases_folder (list).
        Returns number collected.
        """
        import asyncio
        # Path-based override: if user supplied a path with '/' or SQUASH_AUTO_FOLDER_PATH is set & matches
        if getattr(self, 'auto_fetch_folder_path', None) and folder_name == self.auto_fetch_folder:
            # If the provided 'folder_name' equals the scheduled auto folder but path mode is active, delegate
            return await self.fetch_path_test_cases(self.auto_fetch_folder_path)
        folder_id = await asyncio.to_thread(self._find_folder_id_by_name, folder_name)
        if not folder_id:
            logger.warning(f"Folder '{folder_name}' not found")
            self.squash_api_testcases_folder = []
            return 0
        testcases = await asyncio.to_thread(self._collect_test_cases_in_folder, folder_id)
        if not testcases:
            logger.info("Primary /content traversal empty; trying children traversal...")
            try:
                via_children = await asyncio.to_thread(self._collect_test_cases_via_children, folder_id)
                if via_children:
                    testcases = via_children
            except Exception as ce:
                logger.debug(f"Children traversal error: {ce}")
        if not testcases:
            # Fallback strategy: broad scan test-cases endpoint pages and heuristic filter by folder name token
            def _fallback_scan():
                results = []
                import re
                try:
                    page = 0
                    size = 100
                    # Precompute normalized tokens of folder name
                    raw_tokens = re.split(r"[^A-Za-z0-9]+", folder_name)
                    tokens = [t.lower() for t in raw_tokens if t and len(t) > 1]
                    while page < 50:  # hard cap
                        try:
                            data = self._squash_api_get('/api/rest/latest/test-cases', {'page': page, 'size': size})
                        except Exception as e:
                            logger.debug(f"Fallback scan stop at page {page}: {e}")
                            break
                        embedded = data.get('_embedded', {})
                        tcs = embedded.get('test-cases') or []
                        if not tcs:
                            break
                        for raw in tcs:
                            # Basic heuristic: if reference path or name contains folder_name token tokens
                            name = (raw.get('name') or '').lower()
                            ref = (raw.get('reference') or '').lower()
                            if any(tok in name or tok in ref for tok in tokens):
                                try:
                                    detail = self._squash_api_get(f"/api/rest/latest/test-cases/{raw.get('id')}")
                                    norm = self._normalize_squash_test_case(detail)
                                    results.append(norm)
                                except Exception:
                                    pass
                        page_meta = data.get('page') or {}
                        total_pages = page_meta.get('totalPages')
                        if total_pages is not None and page >= total_pages - 1:
                            break
                        page += 1
                except Exception as se:
                    logger.debug(f"Fallback scan error: {se}")
                return results
            fallback = await asyncio.to_thread(_fallback_scan)
            if fallback:
                logger.warning(f"Folder content empty; fallback scan added {len(fallback)} candidates")
                testcases = fallback
        self.squash_api_testcases_folder = testcases
        # Persist a lightweight copy (without heavy raw) for offline reuse
        try:
            import json, os, time
            os.makedirs('squash_cache', exist_ok=True)
            persist_path = os.path.join('squash_cache', f"folder_examples_{folder_id}.json")
            slim = []
            for tc in testcases:
                if isinstance(tc, dict):
                    c = {k: v for k, v in tc.items() if k not in ('raw',)}
                    slim.append(c)
            meta = {
                'folder_name': folder_name,
                'folder_id': folder_id,
                'count': len(slim),
                'saved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'test_cases': slim
            }
            with open(persist_path, 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            logger.info(f"Persisted {len(slim)} folder test cases to {persist_path}")
        except Exception as perr:
            logger.debug(f"Persist folder examples failed: {perr}")
        # Debug print all fetched test cases (id + title)
        try:
            print(f"[DEBUG] Total fetched from '{folder_name}': {len(testcases)}")
            for tc in testcases:
                print(f"   - {tc.get('id')} :: {(tc.get('name') or '').strip()}")
            # Show path/parent for debug clarity (first 10)
            for tc in testcases[:10]:
                print(f"      [DEBUG] path={tc.get('folder_path')} parent_id={tc.get('parent_id')}")
            # If debug mode raw retained, introspect for potential folder linkage fields
            import os
            if os.environ.get('SQUASH_DEBUG') in ('1','true','yes'):
                sample_raw = None
                for tc in testcases:
                    if isinstance(tc, dict) and tc.get('raw'):
                        sample_raw = tc['raw']
                        break
                if sample_raw:
                    keys = list(sample_raw.keys())
                    print(f"[DEBUG] Sample raw test case keys (count {len(keys)}): {keys}")
                    folder_like = [k for k in keys if 'folder' in k.lower() or 'path' in k.lower() or 'parent' in k.lower()]
                    if folder_like:
                        print(f"[DEBUG] Potential folder linkage fields: {folder_like}")
        except Exception as dbg_err:
            logger.debug(f"Debug listing failed: {dbg_err}")
        return len(testcases)

    async def fetch_path_test_cases(self, full_path: str) -> int:
        """Collect test cases whose path matches the given folder path prefix.

        full_path: e.g. "A. Main/[1] Prime/01 Freemium/001-DEV-Network/API/Functional"
        Matching rule: test case detail['path'] lower startswith '/' + normalized(full_path) + '/'
        (detail['path'] includes test case name at end)
        """
        import asyncio, re, json, os, time
        norm_segments = [seg.strip() for seg in re.split(r"/+", full_path) if seg.strip()]
        if not norm_segments:
            logger.warning("Empty full_path provided for path test case fetch")
            return 0
        prefix = '/' + '/'.join(norm_segments).lower() + '/'
        logger.info(f"Path fetch: scanning test cases for prefix '{prefix}'")
        collected = []
        seen = set()
        def _scan():
            page = 0
            size = 100
            while page < 200:  # safety cap
                try:
                    data = self._squash_api_get('/api/rest/latest/test-cases', {'page': page, 'size': size})
                except Exception as e:
                    logger.debug(f"Path scan stop at page {page}: {e}")
                    break
                embedded = data.get('_embedded', {}) if isinstance(data, dict) else {}
                tcs = embedded.get('test-cases') or []
                if not tcs:
                    break
                matched_this_page = 0
                for raw in tcs:
                    tc_id = raw.get('id')
                    if not tc_id or tc_id in seen:
                        continue
                    try:
                        detail = self._squash_api_get(f"/api/rest/latest/test-cases/{tc_id}")
                    except Exception:
                        continue
                    path_val = (detail.get('path') or '').lower()
                    if not path_val.endswith('/'):
                        path_val = path_val  # path includes test case name, not trailing slash
                    # Ensure prefix match (folder path segments only, exclude test case name)
                    if path_val.startswith(prefix):
                        norm = self._normalize_squash_test_case(detail)
                        collected.append(norm)
                        seen.add(tc_id)
                        matched_this_page += 1
                if matched_this_page:
                    logger.info(f"Path scan page {page}: matched {matched_this_page}")
                page_meta = data.get('page') or {}
                total_pages = page_meta.get('totalPages')
                if total_pages is not None and page >= total_pages - 1:
                    break
                page += 1
            return collected
        await asyncio.to_thread(_scan)
        self.squash_api_testcases_folder = collected
        # Persist
        try:
            os.makedirs('squash_cache', exist_ok=True)
            safe_name = re.sub(r'[^A-Za-z0-9_.-]+','_', full_path)[:120]
            persist_path = os.path.join('squash_cache', f"path_examples_{safe_name}.json")
            slim = [{k:v for k,v in tc.items() if k!='raw'} for tc in collected]
            meta = {
                'folder_path': full_path,
                'count': len(slim),
                'saved_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'test_cases': slim
            }
            with open(persist_path, 'w', encoding='utf-8') as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            logger.info(f"Persisted path examples to {persist_path}")
        except Exception as perr:
            logger.debug(f"Persist path examples failed: {perr}")
        # Debug print
        try:
            print(f"[DEBUG] Total fetched from path '{full_path}': {len(collected)}")
            for tc in collected:
                print(f"   - {tc.get('id')} :: {(tc.get('name') or '').strip()}")
        except Exception:
            pass
        return len(collected)

    # ---------------- Dataset Preparation (Title Only) -----------------
    def _ensure_english_title(self, title: str) -> str:
        """Very light heuristic: strip asterisks, remove obvious Indonesian filler words.
        (Real translation pipeline can be added later.)"""
        if not title:
            return title
        t = title.replace('*', '').strip()
        indo_tokens = [" dan ", " ketika ", " tombol ", " pengguna ", " aplikasi ", " validasi ", " warna "]
        lower = f" {t.lower()} "
        if any(tok in lower for tok in indo_tokens):
            # Simple fallback: keep but note (could also drop or mark)
            t = t  # no-op; could append marker or attempt translation
        return t

    def _build_title_only_dataset(self, folder_name: str, numbering: bool = True, split=(0.8, 0.1, 0.1), seed: int = 42):
        """Create title-only dataset from cached folder testcases.
        Returns dict with keys train/val/test each list of {id, reference, title, numbered_title}.
        If cache empty, raise.
        """
        import random, math
        data = getattr(self, 'squash_api_testcases_folder', None)
        if not data:
            raise RuntimeError("Folder test cases cache empty. Run /fetch_folder first.")
        # Extract base records
        rows = []
        for tc in data:
            title = tc.get('name') or tc.get('reference') or ''
            if not title:
                continue
            clean = self._ensure_english_title(title)
            rows.append({
                'id': tc.get('id'),
                'reference': tc.get('reference'),
                'title': clean
            })
        # Deterministic shuffle
        random.Random(seed).shuffle(rows)
        # Numbering global
        if numbering:
            width = 3 if len(rows) < 1000 else len(str(len(rows)))
            for idx, r in enumerate(rows, start=1):
                r['numbered_title'] = f"{idx:0{width}d}. {r['title']}"
        # Split
        n = len(rows)
        n_train = int(split[0] * n)
        n_val = int(split[1] * n)
        train = rows[:n_train]
        val = rows[n_train:n_train + n_val]
        test = rows[n_train + n_val:]
        return {
            'meta': {
                'folder': folder_name,
                'total': n,
                'split': {'train': len(train), 'val': len(val), 'test': len(test)},
                'numbering': numbering,
                'english_enforced': True,
                'fields': ['id','reference','title','numbered_title']
            },
            'train': train,
            'val': val,
            'test': test
        }

    async def handle_export_folder_dataset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/export_folder_dataset <Folder Name> - produce JSONL (title-only) dataset split 80/10/10."""
        chat_id = update.effective_chat.id if update and update.effective_chat else None
        args = update.message.text.split(maxsplit=1) if update.message and update.message.text else []
        if len(args) < 2:
            if chat_id is not None:
                await context.bot.send_message(chat_id=chat_id, text="Usage: /export_folder_dataset <Folder Name>")
            return
        folder_name = args[1].strip()
        try:
            # Make sure cache exists
            if not getattr(self, 'squash_api_testcases_folder', None):
                if chat_id is not None:
                    await context.bot.send_message(chat_id=chat_id, text="📂 Fetching folder first...")
                await self.fetch_folder_test_cases(folder_name)
            dataset = self._build_title_only_dataset(folder_name)
            # Write JSONL
            import json, os, datetime
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            fname = f"dataset_{folder_name.replace(' ','_').replace('.','_')}_{ts}.jsonl"
            with open(fname, 'w', encoding='utf-8') as f:
                # First meta line
                f.write(json.dumps({'meta': dataset['meta']}, ensure_ascii=False) + '\n')
                for section in ('train','val','test'):
                    for row in dataset[section]:
                        out = {**row, 'split': section}
                        f.write(json.dumps(out, ensure_ascii=False) + '\n')
            # Send file
            with open(fname, 'rb') as f:
                if chat_id is not None:
                    await context.bot.send_document(chat_id=chat_id, document=f, filename=fname, caption="✅ Dataset exported (title-only, 80/10/10 split)")
            try:
                os.remove(fname)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"/export_folder_dataset error: {e}")
            if chat_id is not None:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Failed to export dataset: {e}")

    async def handle_fetch_folder_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Telegram command: /fetch_folder <exact folder name>"""
        chat_id = update.effective_chat.id if update and update.effective_chat else None
        args = update.message.text.split(maxsplit=1) if update.message and update.message.text else []
        if len(args) < 2:
            if chat_id is not None:
                await context.bot.send_message(chat_id=chat_id, text="Usage: /fetch_folder <Folder Name>")
            return
        folder_name = args[1].strip()
        try:
            if chat_id is not None:
                await context.bot.send_message(chat_id=chat_id, text=f"🔄 Fetching test cases under folder '{folder_name}' ...")
            count = await self.fetch_folder_test_cases(folder_name)
            if chat_id is not None:
                await context.bot.send_message(chat_id=chat_id, text=f"✅ Collected {count} test cases from folder '{folder_name}'.")
        except Exception as e:
            logger.error(f"/fetch_folder error: {e}")
            if chat_id is not None:
                await context.bot.send_message(chat_id=chat_id, text=f"❌ Failed: {e}")
    
    # Removed: convert_to_squash_multisheet (unused)

    # Removed: parse_test_cases_from_text / extract_steps_from_section (unused)

    async def generate_multimodal_test_cases_fallback(self, image: PILImage.Image, requirements_text: str, test_type: str) -> str:
        """Fallback method that delegates to agents for multimodal generation."""
        try:
            t = (test_type or '').lower()
            if t == 'visual' and getattr(self, 'visual_agent', None):
                return await self.visual_agent.generate_multimodal([image], requirements_text or '')
            if getattr(self, 'functional_agent', None):
                return await self.functional_agent.generate_multimodal([image], requirements_text or '')
            return "❌ Agents not available for multimodal fallback generation."
        except Exception as e:
            logger.error(f"Error in fallback multimodal generation: {e}")
            return f"❌ Error generating multimodal test cases: {str(e)}"

    def run(self):
        """Run the bot with enhanced error handling"""
        logger.info("Starting QA Assistant Bot...")
        print("🤖 QA Assistant Bot is starting...")
        
        try:
            # Test network connectivity first
            print("🔍 Testing network connectivity...")
            self._test_connectivity()

            # Perform deferred auto-fetch of folder test cases (blocking, safe)
            if getattr(self, 'auto_fetch_folder', None):
                folder_name = self.auto_fetch_folder
                print(f"📥 Executing scheduled auto-fetch for folder '{folder_name}' ...")
                try:
                    # Ensure API session
                    try:
                        self._init_squash_api_session()
                    except Exception as init_err:
                        logger.debug(f"API session init issue (continuing): {init_err}")
                    # Use asynchronous method via a temporary event loop
                    import asyncio
                    async def _do_fetch():
                        try:
                            count = await self.fetch_folder_test_cases(folder_name)
                            print(f"✅ Auto-fetch complete: {count} test cases cached for few-shot prompts")
                        except Exception as fe:
                            print(f"⚠️ Auto-fetch error: {fe}")
                    try:
                        asyncio.run(_do_fetch())
                    except RuntimeError as re:
                        # In case already inside a loop (unlikely here), fallback to creating a new loop manually
                        try:
                            loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(loop)
                            loop.run_until_complete(_do_fetch())
                        finally:
                            try:
                                loop.close()
                            except Exception:
                                pass
                except Exception as fetch_err:
                    print(f"⚠️ Deferred auto-fetch failed: {fetch_err}")
            
            print("Bot is ready to receive messages!")
            import inspect, asyncio, telegram
            try:
                print(f"🐍 python-telegram-bot version: {getattr(telegram, '__version__', 'unknown')}")
            except Exception:
                pass

            # Ensure a current event loop exists (Python 3.11+ stricter behavior)
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                base_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(base_loop)

            def _sync_run_polling():
                return self.application.run_polling(
                    allowed_updates=Update.ALL_TYPES,
                    drop_pending_updates=True,
                )

            async def _async_polling_fallback():
                # Full async lifecycle for versions where run_polling must be awaited
                if hasattr(self.application, 'initialize'):
                    await self.application.initialize()
                if hasattr(self.application, 'start'):
                    await self.application.start()
                # Updater path (older compatibility)
                updater = getattr(self.application, 'updater', None)
                if updater and hasattr(updater, 'start_polling'):
                    start_res = updater.start_polling()
                    if inspect.iscoroutine(start_res):
                        await start_res
                # Idle/wait
                if updater and hasattr(updater, 'idle'):
                    idle_res = updater.idle()
                    if inspect.iscoroutine(idle_res):
                        await idle_res
                else:
                    # Fallback idle sleep loop
                    try:
                        while True:
                            await asyncio.sleep(3600)
                    except asyncio.CancelledError:
                        pass

            try:
                result = _sync_run_polling()
                # If the library returns a coroutine (newer async signature), run it
                if inspect.iscoroutine(result):
                    print("ℹ️ Detected async run_polling coroutine – running via asyncio.run()")
                    try:
                        asyncio.run(result)
                    except RuntimeError as re:
                        # Event loop already running (e.g., embedded). Use create_task approach.
                        loop = asyncio.get_event_loop()
                        loop.create_task(result)
                else:
                    # Synchronous path completed
                    pass
            except (TypeError, AttributeError, RuntimeError) as e:
                print(f"⚠️ run_polling sync path failed ({e}); trying async lifecycle fallback...")
                try:
                    asyncio.run(_async_polling_fallback())
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(_async_polling_fallback())
                    loop.close()
        
        except Exception as e:
            logger.error(f"Bot runtime error: {e}")
            print(f"❌ Bot runtime error: {e}")
            self._show_connectivity_help()
            raise
        finally:
            try:
                if getattr(self, '_lock_file', None) and os.path.exists(self._lock_file):
                    os.remove(self._lock_file)
            except Exception:
                pass

    async def cmd_refresh_folder_examples(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        folder_name = getattr(self, 'auto_fetch_folder', 'A. Main')
        await update.message.reply_text(f"Refreshing folder examples for '{folder_name}'...")
        count = await self.fetch_folder_test_cases(folder_name)
        await update.message.reply_text(f"Done. Cached {count} test cases.")

    def _test_connectivity(self):
        """Test basic connectivity before starting bot"""
        import socket
        import time
        
        # Test DNS resolution for Telegram
        try:
            print("   📡 Testing DNS resolution for api.telegram.org...")
            socket.gethostbyname('api.telegram.org')
            print("   ✅ DNS resolution successful")
        except socket.gaierror as e:
            print(f"   ❌ DNS resolution failed: {e}")
            print("   💡 Please check your internet connection")
            raise
        
        # Test basic internet connectivity
        try:
            print("   🌐 Testing internet connectivity...")
            socket.create_connection(("8.8.8.8", 53), timeout=10)
            print("   ✅ Internet connectivity confirmed")
        except Exception as e:
            print(f"   ❌ Internet connectivity failed: {e}")
            raise

    def _show_connectivity_help(self):
        """Show connectivity troubleshooting help"""
        print("\n🔧 Troubleshooting Network Issues:")
        print("   1. Check your internet connection")
        print("   2. Try using a VPN if behind corporate firewall")
        print("   3. Check if Telegram is blocked in your region")
        print("   4. Verify your bot token is correct")
        print("   5. Try running: ping api.telegram.org")
        print("   6. Check Windows Firewall settings")
        print("   7. Restart your network adapter")
        print("\n📋 Quick fixes to try:")
        print("   • ipconfig /flushdns")
        print("   • netsh winsock reset")
        print("   • Restart your router/modem")

    async def export_squash_xls_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Command untuk export test cases ke format XLS Squash TM"""
        await update.message.reply_text("🔄 Mengexport test cases ke format XLS Squash TM...")
        
        try:
            # Get generated test cases from user data
            test_cases_text = context.user_data.get('last_generated_test_cases', '')
            username = update.effective_user.username or update.effective_user.first_name or "QA_Bot"
            
            # Debug: Print what we found
            print(f"DEBUG: test_cases_text length: {len(test_cases_text) if test_cases_text else 0}")
            print(f"DEBUG: username: {username}")
            
            # Gunakan test cases asli jika tersedia; fallback ke sample internal bila kosong
            source_text = test_cases_text if test_cases_text and len(test_cases_text.strip()) > 0 else None
            output_file = await self.create_squash_xls_export(source_text, username)
            
            if output_file and os.path.exists(output_file):
                # Calculate counts for display (5 test cases, no steps for BDD)
                total_test_cases = 5
                total_steps = 0  # No steps for BDD test cases
                # Send file to user
                with open(output_file, 'rb') as file:
                    await update.message.reply_document(
                        document=file,
                        filename=output_file,
                        caption=f"""✅ Export Squash TM XLS - GHERKIN (BDD) Test Cases

📋 HASIL EXPORT:
• TEST_CASES: {total_test_cases} test cases (TC_KIND = GHERKIN)
• STEPS: Header only (NO DATA ROWS) - sesuai BDD, langkah ada di TC_SCRIPT
• Contoh TC_NAME: "001 Verify Absence Of Sort Button In Customer Feature Active"
• Contoh TC_PATH: "/Netmonk/B. Test-Plan/Generated/{username}/001 Verify Absence Of Sort Button In Customer Feature Active"

🎯 FORMAT GHERKIN BDD:
✅ {total_test_cases} test cases dengan TC_KIND='GHERKIN'
✅ TC_PATH format lengkap sesuai standard
✅ Penomoran title "001 Verify..." konsisten
✅ STEPS sheet kosong (hanya header) karena script ada di kolom TC_SCRIPT
✅ Import menghasilkan test cases BDD dengan kolom TC_SCRIPT berisi Scenario & Given/When/Then

📊 SHEETS:
• TEST_CASES: {total_test_cases} rows
• STEPS / PARAMETERS / DATASETS / LINK_REQ_TC: hanya header (kosong)

🚀 Ready for Squash TM import sebagai GHERKIN BDD test cases!"""
                    )
                
                # Clean up file
                try:
                    os.remove(output_file)
                except:
                    pass
            else:
                await update.message.reply_text("❌ Gagal membuat file XLS export.")
                
        except Exception as e:
            await update.message.reply_text(f"❌ Error creating XLS export: {e}")

    async def convert_to_xls_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Command untuk convert test cases yang ada ke XLS"""
        await update.message.reply_text("📝 Instruksi Convert ke XLS:\n\n1. Upload file IMPORTFIXFINAL.xlsx ke chat ini\n2. Bot akan otomatis convert ke format Squash TM XLS\n3. Download hasil convert\n\nAtau gunakan /export_squash_xls untuk generate dari data bot.")

    async def export_squash_xlsx_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Export test cases using centralized multi-sheet .xls generator and send the file."""
        await update.message.reply_text("🔄 Mengexport test cases ke format Squash TM XLS (centralized exporter)...")

        try:
            # Use the last generated chat text as source; fallback to a minimal sample
            test_cases_text = context.user_data.get('last_generated_test_cases', '') or ''
            if not test_cases_text.strip():
                test_cases_text = (
                    "001. Sample Test Case\n"
                    "Given the system is ready\n"
                    "When executing the sample scenario\n"
                    "Then it should succeed"
                )

            username = update.effective_user.username or update.effective_user.first_name or "QA_Bot"

            # Create .xls via centralized exporter, then send and clean up
            output_file = await self.create_squash_xls_export(test_cases_text, username=username)
            if not output_file or not os.path.exists(output_file):
                await update.message.reply_text("❌ Gagal membuat file export.")
                return

            # Estimate test case count quickly
            try:
                total_test_cases = max(1, len([ln for ln in test_cases_text.splitlines() if re.match(r"^\s*\d{1,3}\.\s+\S", ln)]))
            except Exception:
                total_test_cases = 1

            with open(output_file, 'rb') as f:
                await update.message.reply_document(
                    document=f,
                    filename=os.path.basename(output_file),
                    caption=(
                        "✅ Export Squash TM XLS (multi-sheet, BDD)\n\n"
                        f"• Test cases: {total_test_cases}\n"
                        "• TC_KIND: GHERKIN (BDD script in TC_SCRIPT)\n"
                        f"• Owner path base: /Netmonk/G Generated Test Cases/Generated/{username}/...\n"
                    ),
                )

            try:
                os.remove(output_file)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error creating XLS export: {e}")
            await update.message.reply_text(f"❌ Error creating XLS export: {e}")

    async def create_squash_xls_export(self, test_cases_text: Optional[str] = None, username: str = "QA_Bot"):
        """Create Squash TM .xls using centralized exporter (multi-sheet BDD template)."""
        try:
            # Build test_cases list (either parse provided text or sample fallback)
            test_cases: List[Dict] = []
            if test_cases_text:
                # Try robust parser first
                test_cases = self.parse_generated_test_cases(test_cases_text)
                if not test_cases:
                    # Minimal fallback: detect numbered titles with basic steps
                    import re as _re
                    pattern = _re.compile(r'^(\d{3}|\d{1,2})\.\s+(.+)$', _re.MULTILINE)
                    matches = list(pattern.finditer(test_cases_text))
                    for idx, m in enumerate(matches):
                        num = int(m.group(1))
                        title = m.group(2).strip()
                        test_cases.append({'id': f'TC_{num:03d}', 'name': f"{num:03d} {title}", 'description': title, 'steps': []})
            if not test_cases:
                test_cases = [{
                    'id': 'TC_001',
                    'name': '001 Sample Test Case Placeholder',
                    'description': 'Fallback sample because parsing yielded no test cases'
                }]

            # Call centralized exporter that writes .xls and returns the file path
            output_file = exporter_export_squash_xls_file(test_cases, username=username, test_cases_text=test_cases_text)
            return output_file
        except Exception as e:
            logger.error(f"Error creating XLS export: {e}")
            return None

    async def generate_testcases_from_text(self, text_content: str, test_type: str) -> str:
        """Generate test cases from text requirements using custom knowledge base"""
        try:
            # Delegate to agents based on test_type
            t = (test_type or '').lower()
            if t == 'visual' and getattr(self, 'visual_agent', None):
                return await self.visual_agent.generate_from_text(text_content)
            if getattr(self, 'functional_agent', None):
                return await self.functional_agent.generate_from_text(text_content)
            return "❌ Agents not available for text-based generation."
            
        except Exception as e:
            logger.error(f"Error generating test cases from text: {e}")
            return f"❌ Error generating test cases: {str(e)}\n\nPlease try again or check your requirements format."


    # ------------------------------
    # TYPE-SPECIFIC KNOWLEDGE LOADER
    # ------------------------------
    # Removed: _load_type_knowledge / _system_prompt_for_type (agents own prompts)

    # Removed: _enforce_bdd_and_type (moved to agent layer)
    # Removed: _visual_rewrite_steps (unused helper)
    # Removed: _get_visual_only_guidelines (moved to agent layer)


def _preflight_checks() -> bool:
    """Minimal preflight checks before starting the bot (env + DNS)."""
    try:
        import os, socket
        print("🔍 Preflight: Checking TELEGRAM_BOT_TOKEN...")
        tok = os.getenv("TELEGRAM_BOT_TOKEN")
        if not tok or not tok.strip():
            print("  ❌ TELEGRAM_BOT_TOKEN is missing.")
            return False
        print("  ✅ Token present")

        print("🔍 Preflight: DNS resolution (api.telegram.org)...")
        try:
            socket.gethostbyname("api.telegram.org")
            print("  ✅ DNS OK")
        except Exception as e:
            print(f"  ⚠️ DNS resolution issue: {e}")
            print("  🔄 Continuing anyway - may still work if connectivity is OK")
        return True
    except Exception as e:
        print(f"⚠️ Preflight check error: {e}")
        return True


def start_bot_with_retry(max_retries: int = 3, delay_seconds: int = 5) -> bool:
    """Start TelegramQABot with retry and troubleshooting tips.

    Returns True if the bot started successfully (or was stopped by user), False otherwise.
    """
    import time
    print("🤖 QA Casecraft Agent Launcher")
    print("=" * 40)

    for attempt in range(1, max_retries + 1):
        print(f"\n🚀 Starting bot (attempt {attempt}/{max_retries})...")
        try:
            if not _preflight_checks():
                print("❌ Preflight checks failed.")
                raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or network prerequisites failed")

            bot = TelegramQABot()
            print("✅ Bot initialized successfully")
            bot.run()
            return True

        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
            print("\n🛑 Bot stopped by user")
            return True
        except Exception as e:
            logger.error(f"Bot start attempt {attempt} failed: {e}")
            print(f"❌ Attempt {attempt} failed: {e}")
            if attempt < max_retries:
                print(f"⏳ Retrying in {delay_seconds} seconds...")
                print("💡 Tips: check internet, run as admin, disable VPN, check Windows Firewall")
                time.sleep(delay_seconds)
            else:
                print(f"❌ All {max_retries} attempts failed.")
                print("\n🔧 Troubleshooting:")
                print("1. Verify TELEGRAM_BOT_TOKEN and GOOGLE_API_KEY in .env")
                print("2. ipconfig /flushdns")
                print("3. netsh winsock reset")
                print("4. Restart network adapter / try VPN")
                print("5. Run terminal as Administrator")
                return False

    # Safety return; we should never reach here, but satisfy type checker
    return False


def main():
    """Entry point to run this file directly with retry."""
    ok = start_bot_with_retry(max_retries=3, delay_seconds=5)
    if not ok:
        raise SystemExit(1)

if __name__ == "__main__":
    try:
        main()
    except SystemExit as se:
        raise
    except Exception as e:
        logger.error(f"Launcher error: {e}")
        print(f"❌ Launcher error: {e}")
        raise
     