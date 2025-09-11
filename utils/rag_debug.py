"""RAG debug helper utilities.

Provides a simple function rag_search_debug that can be wired to a Telegram
command (/rag_search) or used directly in scripts to inspect the top
retrieval chunks (optionally filtered by product).

Example (Telegram handler pseudo-code):

    from utils.rag_debug import rag_search_debug

    async def cmd_rag_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("Usage: /rag_search <query> [product=prime|hi|portal]")
            return
        # Parse optional product= param
        prod = None
        cleaned_args = []
        for a in context.args:
            if a.startswith("product="):
                prod = a.split("=",1)[1].lower()
            else:
                cleaned_args.append(a)
        query = " ".join(cleaned_args)
        text = rag_search_debug(query, product=prod, k=5)
        await update.message.reply_text(text or "(no results)")

"""
from __future__ import annotations
from typing import Optional

try:
    from rag_engine import get_index
except Exception:  # pragma: no cover
    get_index = None  # type: ignore

VALID_PRODUCTS = {"prime", "hi", "portal"}

def rag_search_debug(query: str, product: Optional[str] = None, k: int = 5) -> str:
    """Return a concise textual debug view of top-k RAG matches.

    Args:
        query: user natural language query.
        product: optional product filter (prime|hi|portal). If invalid ignored.
        k: number of chunks.
    """
    if not query or not query.strip():
        return "(empty query)"
    prod = product.lower() if product and product.lower() in VALID_PRODUCTS else None
    try:
        if not get_index:
            return "RAG engine not available (import failed)."
        idx = get_index()
        if not idx.is_ready():
            return "RAG index not loaded. Use /reload_rag first."  # type: ignore
        results = idx.search(query, k=k, product=prod)  # type: ignore
        if not results:
            return "No matches found." if prod is None else f"No matches for product '{prod}'."
        lines = [
            f"RAG Search Debug (k={k}, product={prod or 'all'})",
            "-------------------------------------------",
        ]
        for i, r in enumerate(results, 1):
            snippet = r.get("text", "").replace("\n", " ")
            if len(snippet) > 180:
                snippet = snippet[:177] + "..."
            lines.append(
                f"{i}. {r.get('product')}::{r.get('file')} score={r.get('score'):.2f} | {snippet}"
            )
        return "\n".join(lines)
    except Exception as e:  # pragma: no cover
        return f"RAG search error: {e}"  # keep concise for chat
