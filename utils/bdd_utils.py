import re
def sanitize_example_titles(text: str) -> str:
    if not text:
        return text
    try:
        text = re.sub(r'(^\s*\d{1,3}\.\s*)\[[^\]]+\]\s*', r'\1', text, flags=re.MULTILINE)
        text = re.sub(r'(^\s*)\[[^\]]+\]\s*', r'\1', text, flags=re.MULTILINE)
        return text
    except Exception:
        return text

def sanitize_generated_output(text: str) -> str:
    if not text:
        return ""
    try:
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        text = text.replace('*', '')
        cleaned_lines = []
        for line in text.split('\n'):
            stripped = line.lstrip()
            if stripped[:1] in ['-', '•', '●', '▪']:
                stripped = stripped[1:].lstrip()
            stripped = re.sub(r'^(\d+\))\s+', '', stripped)
            cleaned_lines.append(stripped)
        text = '\n'.join(cleaned_lines)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()
    except Exception:
        return text

def normalize_numbering(text: str) -> str:
    if not text:
        return text
    import re as _re
    pattern = _re.compile(r"^(\s*)(\d+)\.(\s*)(.+)$", _re.MULTILINE)
    def _repl(m):
        indent = m.group(1) or ''
        num = int(m.group(2))
        rest = (m.group(4) or '').strip()
        return f"{indent}{num:03d}. {rest}"
    return pattern.sub(_repl, text)

def ensure_blank_line_between_numbered(text: str) -> str:
    if not text:
        return text
    try:
        import re as _re
        lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        out = []
        header_pat = _re.compile(r'^\s*\d{1,3}\.\s+\S')
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

def contains_indonesian(text: str) -> bool:
    if not text:
        return False
    tokens = [
        ' yang ', ' dan ', ' adalah ', ' ketika', ' saat ', ' tombol', ' halaman', ' pengguna', ' aplikasi', ' tampil', ' ditampilkan', ' ukuran', ' warna', ' berhasil', ' gagal', ' data ', ' sistem '
    ]
    lower = f" {text.lower()} "
    return any(tok in lower for tok in tokens)
