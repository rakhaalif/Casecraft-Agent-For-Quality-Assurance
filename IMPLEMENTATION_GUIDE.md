# Telegram QA Casecraft Agent – How to Implement

This guide explains how to implement the existing Telegram QA Bot for a different product outside of Netmonk. It focuses on preparing product-specific knowledge and wiring the product into the RAG-driven generation flow.

## Quick Start (from git clone)

Use these steps for a fresh install on Windows with PowerShell.

```pwsh
# 0) Clone the repository
git clone <REPO_URL> casecraft-agent
cd .\casecraft-agent

# 1) Install dependencies (system Python)
pip install -r requirements.txt

# 2) Configure .env (file: ./.env — edit only these two keys)
# TELEGRAM_BOT_TOKEN=123456:ABC-YourBotToken
# GOOGLE_API_KEY=your-google-generative-ai-key

# 3) First run
python telegram_bot.py
```

Then, in Telegram:

- Open your bot (the one created via BotFather using the token above)
- Tap “Generate Test Case” → choose a product → send requirements (text and/or image)
- Check the RAG proof block at the bottom of the generated output to confirm sources

Tips:

- To update later: `git pull`, then re-run `pip install -r requirements.txt` if dependencies changed.

How to obtain the keys:

- Telegram Bot Token: In Telegram, talk to @BotFather → `/newbot` → follow prompts → copy the token.
- Gemini API Key: Create a key in Google AI Studio:
  - https://aistudio.google.com/app/apikey or
  - Docs: https://ai.google.dev/gemini-api/docs/api-key

## What you get

- A Telegram bot that generates test cases (functional/visual) from text and images
- Product-aware context via RAG (BM25) over your knowledge files
- Optional “RAG SOURCES (Proof)” at the end of every output to verify which documents were used

---

## Prerequisites

- Python 3.10+ (3.11 recommended)
- A Telegram Bot token (create via BotFather)
- A Google Generative AI API Key (Gemini)
- Windows PowerShell (or any shell) to run commands

Environment variables (recommended via `.env`, file: `./.env`):

- `TELEGRAM_BOT_TOKEN` – your Telegram bot token
- `GOOGLE_API_KEY` – your Google Generative AI key
- `GEMINI_MODEL` – optional, model override (e.g., `gemini-1.5-flash`). If omitted, the default will be used. (file: `./.env`)

Main libraries (already referenced in `requirements.txt`):

- `python-telegram-bot`
- `google-generativeai`
- `rank-bm25`
- `Pillow`, `xlwt`, `requests`, `python-dotenv`

---

## Variables you must edit and where (TL;DR)

Use this checklist to see exactly which variables you need to change and the file paths:

- `TELEGRAM_BOT_TOKEN` (file: `./.env`, location: repo root)
  - Set to your BotFather token.
- `GOOGLE_API_KEY` (file: `./.env`, location: repo root)
  - Set to your Google Generative AI API key.
- `GEMINI_MODEL` (optional) (file: `./.env`, location: repo root)
  - Override the model if desired (e.g., `gemini-1.5-flash`).
- `PRODUCT_DIRS` (file: `./rag_engine.py`, location: repo root)
  - Add your product slug so RAG loads `knowledge/<product>/`.
- `valid_products` (file: `./telegram_bot.py`, location: repo root)
  - Include your product slug so selection/validation works.
- Product menu buttons (file: `./telegram_bot.py`, location: repo root)
  - Add a button for your product or switch to dynamic rendering.
- `show_rag_proof` toggle (optional) (file: `./telegram_bot.py`, location: repo root)
  - Set to `False` to hide the proof block in production.
- Knowledge files (folder: `./knowledge/<yourproduct>/`, location: repo root)
  - Create numbered `.txt` files: `1.txt`, `2.txt`, ... with product UI details.

## Folder structure overview

```
Chatbot QA/
├─ telegram_bot.py            # Main bot logic, menus, handlers
├─ agent_manager.py           # Orchestrates Functional & Visual agents
├─ agent_functional.py        # Functional test generation agent
├─ agent_visual.py            # Visual/UI analysis agent
├─ bot_callbacks.py           # (If used) extra callback helpers
├─ rag_engine.py              # RAG (BM25) index over knowledge/*
├─ exporters/
│  └─ squash_export.py        # Export to Squash TM (.xls)
├─ parsers/                   # Parsers (if any)
├─ utils/
│  ├─ bdd_utils.py
│  ├─ knowledge_utils.py
│  ├─ generate_pdf_knowledge.py
│  └─ rag_debug.py
├─ knowledge/
│  ├─ prime/                  # Product: prime (numbered .txt)
│  │  ├─ 1.txt
│  │  ├─ 2.txt
│  │  └─ ...
│  ├─ hi/                     # Product: hi (numbered .txt)
│  ├─ portal/                 # Product: portal (numbered .txt)
│  ├─ functional_knowledge.txt
│  └─ visual_knowledge.txt
├─ multi_sheet_converter.py   # (If used) utility converter
├─ requirements.txt
├─ .env                       # Set TELEGRAM_BOT_TOKEN & GOOGLE_API_KEY here
├─ Dockerfile
├─ docker-compose.yml
├─ README.md
└─ IMPLEMENTATION_GUIDE.md
```

RAG expects numbered `.txt` files under `knowledge/<product>/`. Each file becomes a retrievable unit.

---

## Product naming (simple)

This document still uses "Netmonk" as an example. If you want to use your own brand/unit name:

- Update the knowledge text files that should display your brand (folder: `./knowledge/<yourproduct>/`).
- If any UI labels display the brand, edit them in `./telegram_bot.py`.

Easy way to find "Netmonk" without terminal commands:

- In your VSCode(any other IDE): press Ctrl+Shift+F → type Netmonk → review results and modify only what’s necessary.

Notes:
you don’t need to replace every instance of "Netmonk". Change it only if you want the brand to appear in outputs.

---

## Step 1 — Create knowledge for your product

1. Create a new folder: `knowledge/yourproduct/`
2. Add numbered `.txt` files: `1.txt`, `2.txt`, `3.txt`, ...
3. Write concise, directly-usable, UI-grounded content:
   - Use the same labels and wording as your product UI (e.g., buttons, placeholders, colors, icons)
   - Split content by topics for better retrieval (one topic per file works best)

Suggested file set for a complex page (example: “Device Status”) — 1 feature per 1 file:

- `1.txt` — Overview & KPIs
- `2.txt` — Card: Total Up (icon/arrow/green, wording, alignment)
- `3.txt` — Card: Total Down (icon/arrow/red, wording, alignment)
- `4.txt` — Card: Total Undetected (icon/question/gray, tooltip behavior)
- `5.txt` — Search bar (placeholder: “Type to search”)
- `6.txt` — Filter: Up (checkbox label & behavior)
- `7.txt` — Filter: Down (checkbox label & behavior)
- `8.txt` — Filter: Undetected (checkbox label & behavior)
- `9.txt` — Table header: Device Name
- `10.txt` — Table header: Node Name
- `11.txt` — Table header: IP Management
- `12.txt` — Table header: Status (dot colors: green/red/gray)
- `13.txt` — Table header: Uptime (format hh:mm:ss)
- `14.txt` — Table header: Tagging (chip styles)
- `15.txt` — Table header: Action (icons & consistency)
- `16.txt` — Sorting indicators (up/down arrows per column)
- `17.txt` — Export button (label: “Export to CSV”, behavior)
- `18.txt` — Pagination controls (First, Prev, 1..N, Next, Last)
- `19.txt` — Responsive: mobile breakpoint (layout adjustments)
- `20.txt` — Responsive: tablet breakpoint (layout adjustments)
- `21.txt` — Accessibility: color contrast (WCAG)
- `22.txt` — Accessibility: keyboard navigation
- `23.txt` — Empty state: no results (message & styling)

Additional notes:

- If you add more features, continue the numbering (24.txt, 25.txt, etc.).
- Don’t combine multiple features in one file; keep 1 feature = 1 file for more precise RAG.

Tips for better retrieval:

- Keep 1 feature = 1 .txt file to achieve precise retrieval and easier maintenance
- Keep each file at least ~200–300 characters (avoid super-short files)
- Embed key phrases explicitly (e.g., "Export to CSV")
- Use headings and short sentences

---

## Step 2 — Register your product in the RAG engine

Open `rag_engine.py` (file: `./rag_engine.py`, repo root) and update the product list (`PRODUCT_DIRS`):

```python
# rag_engine.py
PRODUCT_DIRS = ['prime', 'hi', 'portal', 'yourproduct']
```

That’s enough for the BM25 index to load your new documents from `knowledge/yourproduct/`.

Optional: You can auto-discover products by listing folders under `knowledge/`, but the simple explicit list is reliable and easy to maintain.

---

## Step 3 — Expose your product in the bot UI

Open `telegram_bot.py` (file: `./telegram_bot.py`, repo root) and update the product selectors.

1. Validation list (variable: `valid_products`, in the `select_product_` callback):

```python
# telegram_bot.py
valid_products = {"prime", "hi", "portal", "yourproduct"}
```

2. Product menu (in `choose_product_menu` handler): add a button for your product similar to the others, or render dynamically. Example pattern:

```python
# Use the same pattern as existing buttons
InlineKeyboardButton("YourProduct" + (" ✅" if current_product=="yourproduct" else ""), callback_data="select_product_yourproduct")
```

Note: If you want to avoid editing code for every new product, you can render these buttons dynamically from a single product list (still in `telegram_bot.py`).

---

## Step 4 — Run locally (Windows PowerShell)

```pwsh
# 1) Install dependencies
pip install -r requirements.txt

# 2) Edit .env (only two keys are required)
# TELEGRAM_BOT_TOKEN=123456:ABC-YourBotToken
# GOOGLE_API_KEY=your-google-generative-ai-key
# 3) Start the bot
python telegram_bot.py
```

Open Telegram, chat your bot, pick your product, send requirements (text/images), and generate test cases.

---

## Step 5 — Verify RAG is using your product

Every generation appends a proof block :

```
--- RAG SOURCES (Proof) ---
1. yourproduct/12.txt  score=4.51
2. yourproduct/7.txt   score=3.10
...
```

Success criteria:

- The product folder in proof matches your selection (e.g., `yourproduct/…`)
- The files listed are contextually relevant to your requirements

To hide the proof in production, set `self.show_rag_proof = False` on the bot instance/class (file: `./telegram_bot.py`). If you don’t find this property, search for `show_rag_proof` and define it once in the main bot class/instance initialization.

## Step 6 — Tips for higher relevance

- Use the exact UI labels and terminology in your knowledge
- Split topics: 1 topic = 1 file (numbered)
- Prefer targeted files (avoid dumping everything in a single large file)
- Include behavior and states (filters, pagination, empty state, accessibility)
- Keep knowledge updated when UI changes

---

## Troubleshooting

- Proof shows wrong product:
  - Ensure the selected product was stored in the session (product picker step)
  - Verify your new product is in `valid_products` and in `PRODUCT_DIRS`
- Proof shows fallback “full product knowledge”:
  - Check your `knowledge/yourproduct` folder exists and contains numbered `.txt`
  - Ensure `PRODUCT_DIRS` includes your product
- Generated output misses specific UI details:
  - Enrich the knowledge with those details and explicit labels
  - Split into more focused files so BM25 can pick the right snippets

---

## Security & maintenance

- Keep API keys in environment (or `.env` with restricted access)
- Review and curate knowledge regularly to avoid stale guidance
- For production, consider disabling RAG proof

---

## Checklist –

- [ ] `knowledge/yourproduct/*.txt` exists with numbered, descriptive files
- [ ] `PRODUCT_DIRS` includes `yourproduct`
- [ ] Bot UI offers your product in the selection menu
- [ ] RAG proof shows `yourproduct/*.txt` on generation
- [ ] Generated test cases are grounded in your product’s UI/behavior

If you need help templating the knowledge files or making product discovery dynamic, reach out to the bot maintainer team.
