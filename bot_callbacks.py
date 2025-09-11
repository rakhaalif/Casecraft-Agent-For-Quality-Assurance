"""Minimal callback flow patch implementing product + test type selection.

This file is a lightweight substitute if the original telegram_bot.py
was removed or not loaded. It wires a small set of handlers providing:
- /start main menu
- test type selection -> product selection -> ready state
- /rag_search <query> [product=prime|hi|portal]
- Guard for generation when product not chosen

Integrate: import register_handlers() in your main bot bootstrap after
creating Application instance.
"""
from __future__ import annotations
from typing import Optional
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters)

from utils.rag_debug import rag_search_debug

logger = logging.getLogger(__name__)

PRODUCTS = ["prime", "hi", "portal"]

MAIN_MENU_TEXT = (
    "🤖 QA Bot\n\n"
    "Pilih aksi:\n"
    "- Generate Test Case: pilih jenis & produk\n"
    "- /rag_search <query> [product=prime|hi|portal] untuk debug RAG"
)

def _main_menu_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Generate Test Case", callback_data="test_type_menu")]
    ])

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=_main_menu_kb())

async def cmd_rag_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /rag_search <query> [product=prime|hi|portal]")
        return
    prod = None
    args = []
    for a in context.args:
        if a.startswith("product="):
            prod = a.split("=",1)[1].lower()
        else:
            args.append(a)
    query = " ".join(args)
    text = rag_search_debug(query, product=prod, k=5)
    await update.message.reply_text(text)

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Basic collection of requirements after selection
    if 'ready_for_requirements' in context.user_data:
        accum = context.user_data.get('req_text','')
        fragment = update.message.text or ''
        context.user_data['req_text'] = (accum + '\n' + fragment).strip()
        await update.message.reply_text("✅ Teks diterima. Ketik /generate untuk generate atau teruskan kirim teks.")
    else:
        await update.message.reply_text("Ketik /start untuk memulai.")

async def cmd_generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    test_type = context.user_data.get('selected_test_type')
    product = context.user_data.get('selected_product')
    if not (test_type and product):
        await update.message.reply_text("⚠️ Harus pilih jenis test & produk dulu. Gunakan menu.")
        return
    req_text = context.user_data.get('req_text','(no requirements)')
    # Placeholder generation output (hook to real agents in main bot)
    output = f"[DUMMY OUTPUT]\nType={test_type}\nProduct={product}\nRequirements=\n{req_text[:500]}"  # truncate preview
    await update.message.reply_text(output)

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'test_type_menu':
        kb = [
            [InlineKeyboardButton('🧪 Functional', callback_data='choose_product_functional'), InlineKeyboardButton('👁️ Visual', callback_data='choose_product_visual')],
            [InlineKeyboardButton('← Main', callback_data='back_main')]
        ]
        msg = "Langkah 1/2: Pilih jenis test case."
        await _safe_edit(query, msg, InlineKeyboardMarkup(kb))
        return
    if data in ('choose_product_functional','choose_product_visual'):
        test_type = 'functional' if 'functional' in data else 'visual'
        context.user_data['pending_test_type'] = test_type
        kb = [
            [InlineKeyboardButton('Prime', callback_data='product_prime'), InlineKeyboardButton('HI', callback_data='product_hi'), InlineKeyboardButton('Portal', callback_data='product_portal')],
            [InlineKeyboardButton('← Back', callback_data='test_type_menu')]
        ]
        await _safe_edit(query, f"Langkah 2/2: Pilih produk untuk {test_type.title()} test.", InlineKeyboardMarkup(kb))
        return
    if data.startswith('product_'):
        product = data.split('_',1)[1]
        if product not in PRODUCTS:
            await _safe_edit(query, "❌ Produk tidak dikenal.")
            return
        test_type = context.user_data.get('pending_test_type','functional')
        context.user_data['selected_product'] = product
        context.user_data['selected_test_type'] = test_type
        context.user_data['ready_for_requirements'] = True
        guide = (
            f"✅ {test_type.title()} Test untuk produk {product.title()} dipilih.\n"
            "Kirim requirements (boleh beberapa pesan). Ketik /generate untuk mulai."
        )
        kb = [
            [InlineKeyboardButton('🔁 Ganti Produk', callback_data=f'choose_product_{test_type}'), InlineKeyboardButton('🧪 Jenis Lain', callback_data='test_type_menu')],
            [InlineKeyboardButton('← Main', callback_data='back_main')]
        ]
        await _safe_edit(query, guide, InlineKeyboardMarkup(kb))
        return
    if data == 'back_main':
        await _safe_edit(query, MAIN_MENU_TEXT, _main_menu_kb())
        return

    await _safe_edit(query, f"❌ Unknown command: {data}\nGunakan menu.")

async def _safe_edit(query, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    try:
        if getattr(query.message, 'text', None):
            if reply_markup:
                await query.edit_message_text(text, reply_markup=reply_markup)
            else:
                await query.edit_message_text(text)
        else:
            await query.message.reply_text(text, reply_markup=reply_markup)
    except Exception:
        try:
            await query.message.reply_text(text, reply_markup=reply_markup)
        except Exception:
            pass

def register_handlers(app: Application):
    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('rag_search', cmd_rag_search))
    app.add_handler(CommandHandler('generate', cmd_generate))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    logger.info('Minimal callback flow handlers registered.')
