import os
import math
import json
from pathlib import Path
from typing import List, Tuple, Dict, Any, Optional
from rank_bm25 import BM25Okapi

KNOWLEDGE_ROOT = Path(__file__).resolve().parent / 'knowledge'
PRODUCT_DIRS = ['prime', 'hi', 'portal']

class RagIndex:
    def __init__(self):
        self.documents: List[str] = []
        self.doc_meta: List[Dict[str, Any]] = []
        self._bm25: BM25Okapi | None = None

    def load(self, limit_per_product: int | None = None) -> int:
        docs: List[str] = []
        meta: List[Dict[str, Any]] = []
        for product in PRODUCT_DIRS:
            pdir = KNOWLEDGE_ROOT / product
            if not pdir.is_dir():
                continue
            numbered = sorted([f for f in pdir.glob('*.txt') if f.name[:-4].isdigit()], key=lambda x: int(x.name[:-4]))
            if limit_per_product:
                numbered = numbered[:limit_per_product]
            for path in numbered:
                try:
                    txt = path.read_text(encoding='utf-8').strip()
                except Exception:
                    txt = ''
                if not txt:
                    continue
                docs.append(txt)
                meta.append({'product': product, 'file': path.name, 'path': str(path)})
        self.documents = docs
        self.doc_meta = meta
        tokenized = [d.lower().split() for d in self.documents]
        if tokenized:
            self._bm25 = BM25Okapi(tokenized)
        return len(self.documents)

    def is_ready(self) -> bool:
        return bool(self.documents) and self._bm25 is not None

    def search(self, query: str, k: int = 5, product: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.is_ready():
            return []
        q_tokens = query.lower().split()
        scores = self._bm25.get_scores(q_tokens)  # type: ignore
        pairs = list(enumerate(scores))
        pairs.sort(key=lambda x: x[1], reverse=True)
        out = []
        for idx, score in pairs[:k]:
            m = dict(self.doc_meta[idx])
            m['score'] = float(score)
            m['text'] = self.documents[idx][:2000]  # safety truncate per doc
            # Apply product filter if requested
            if product and m.get('product') != product:
                continue
            out.append(m)
            if len(out) >= k:
                break
        return out

    def build_context(self, query: str, k: int = 5, max_chars: int = 4000, product: Optional[str] = None) -> str:
        results = self.search(query, k=k, product=product)
        if not results:
            return ''
        parts: List[str] = []
        total = 0
        for r in results:
            snippet = r['text']
            header = f"[{r['product']}:{r['file']} score={r['score']:.2f}]"
            block = header + '\n' + snippet.strip()
            if total + len(block) > max_chars:
                remaining = max_chars - total
                if remaining <= 0:
                    break
                block = block[:remaining]
            parts.append(block)
            total += len(block)
            if total >= max_chars:
                break
        return '\n\n'.join(parts)

_index_singleton: RagIndex | None = None

def get_index() -> RagIndex:
    global _index_singleton
    if _index_singleton is None:
        _index_singleton = RagIndex()
        _index_singleton.load()
    return _index_singleton
