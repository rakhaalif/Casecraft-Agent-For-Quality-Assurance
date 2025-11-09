# Casecraft Agentbot for Quality Assurance

Assistant Telegram berbasis RAG + Gemini untuk menghasilkan test case fungsional dan visual berkualitas tinggi dari prompt natural language maupun gambar UI. Repo ini juga menyertakan pipeline evaluasi otomatis (cosine, BDD, durasi, dan LLM scoring) beserta template dataset.

> ⚠️ Repo ini memerlukan API Key Gemini dan token bot Telegram yang valid.

## Fitur Utama

- **Telegram Bot** dengan menu interaktif untuk memilih produk, mengirim requirement, dan memicu generator test case.
- **Double Agent** (Functional & Visual) yang memanfaatkan konteks RAG BM25 di folder `knowledge/` untuk menjaga grounding terhadap UI produk.
- **RAG Proof Block** opsional di output untuk menunjukkan sumber pengetahuan yang dipakai.
- **Evaluator Otomatis** (`evaluation_template.py`, `render_eval_table.py`, `gemini_semantic_evaluator.py`) untuk menghitung metrik:
  - Cosine similarity (akurasi kontekstual)
  - BDD compliance (Given/When/Then)
  - Durasi generasi
  - Semantic score (Gemini LLM evaluator)
- **Exporter & utils** (mis. `exporters/squash_export.py`) untuk integrasi lebih lanjut.

## Prasyarat

- Python 3.10 atau lebih baru (>=3.11 direkomendasikan)
- `pip` dan virtual environment (opsional tapi disarankan)
- Token bot Telegram (buat via [BotFather](https://t.me/BotFather))
- Google Gemini API Key (buat di [Google AI Studio](https://aistudio.google.com/app/apikey))
- Windows PowerShell (perintah di README menggunakan `pwsh`, namun shell lain juga dapat digunakan)

## Setup

```pwsh
# 0. Clone repo
git clone https://github.com/rakhaalif/Casecraft-Agent-For-Quality-Assurance.git
cd Casecraft-Agent-For-Quality-Assurance

# 1. (Opsional) buat virtual environment
python -m venv .venv
.\.venv\Scripts\Activate

# 2. Install dependensi
pip install -r requirements.txt

# 3. Konfigurasi .env
copy .env.example .env  # jika tersedia; jika tidak buat manual
notepad .env
```

Isi `.env` minimal dengan:

```
TELEGRAM_BOT_TOKEN=123456:ABC-YourBotToken
GOOGLE_API_KEY=your-gemini-api-key
# GEMINI_MODEL=gemini-1.5-flash  # opsional override
```

## Struktur Proyek

```
Chatbot QA/
├─ telegram_bot.py          # Entry point bot Telegram
├─ agent_manager.py         # Orkestrasi agen fungsional & visual
├─ agent_functional.py      # Generator test case fungsional
├─ agent_visual.py          # Analisis UI / visual cues
├─ rag_engine.py            # BM25 indexing & retrieval
├─ knowledge/               # Knowledge base per produk (1 file = 1 topik)
├─ data/eval_cases.json     # Dataset evaluasi contoh
├─ evaluation_template.py   # Pipeline metrik otomatis
├─ gemini_semantic_evaluator.py # LLM evaluator (Gemini)
├─ render_eval_table.py     # Renderer tabel Markdown dari hasil evaluasi
└─ exporters/, utils/, parsers/, dll
```

## Menambahkan Produk Baru

1. Buat folder `knowledge/<produk>/` berisi file bernomor `1.txt`, `2.txt`, dst dengan deskripsi UI/flow spesifik.
2. Tambahkan slug ke `PRODUCT_DIRS` di `rag_engine.py`.
3. Tambahkan slug ke `valid_products` dan menu inline di `telegram_bot.py`.
4. (Opsional) Update prompt agen bila butuh tone khusus.

Tips penulisan knowledge:

- Gunakan label UI persis (contoh: `"Export to CSV"`).
- 1 topik = 1 file berbentuk paragraf singkat (200–400 karakter).
- Sertakan warna/icon/placeholder agar test case dapat diverifikasi visual.

## Menjalankan Bot

```pwsh
python telegram_bot.py
```

Di Telegram:

1. Buka bot Anda (token dari BotFather).
2. `/start` → pilih produk.
3. Kirim requirement teks atau gambar (screenshot UI).
4. Pilih jenis test case (Functional / Visual). Bot akan merespons dengan daftar BDD.

Setiap jawaban dapat menyertakan blok `RAG SOURCES (Proof)` untuk audit pengetahuan.

## Pipeline Evaluasi(Opsional)

### 1. Siapkan Dataset

`data/eval_cases.json` berisi array test scenario dengan struktur:

```json
{
  "case_id": "TC-001",
  "scenario": "Device Status Page",
  "prompt": "...",
  "requirement": "...",
  "generated_testcases": [
    {
      "id": "001",
      "title": "...",
      "body": "Given ...\nWhen ...\nThen ...",
      "generated_at": "2025-10-29T09:00:00Z"
    }
  ],
  "metrics": { "duration_seconds": 3.6 }
}
```

### 2. Jalankan Evaluasi Utama(Opsional)

```pwsh
python .\evaluation_template.py --input data\eval_cases.json --output results\eval_report.csv --markdown results\eval_table.md
```

Output:

- `results/eval_report.csv` – metrik per skenario (cosine_mean, BDD, durasi, semantic avg)
- `results/eval_table.md` – ringkasan Markdown siap tempel
- Ringkasan di terminal

### 3. Render Ulang Tabel(Opsional)

```pwsh
python .\render_eval_table.py --input results\eval_report.csv --output results\eval_table.md
```

### 4. Evaluator LLM(Gemini)

```pwsh
python .\gemini_semantic_evaluator.py --input data\eval_cases.json --limit 5 --csv results\gemini_semantic_scores.csv
```

Script ini menanyakan Gemini 2.0 Flash untuk menilai kesesuaian makna requirement vs test case (skala 1–5) dan menghasilkan tabel Markdown + CSV.

#### Mengapa LLM Evaluator Layak Dipercaya?

- **Rubrik eksplisit** – prompt memaksa Gemini menggunakan skala 1–5 dan hanya membandingkan requirement dengan test case, sehingga konsisten.
- **Model berkelas industri** – Gemini 2.0 Flash dilatih pada korpus QA dan UI yang luas, mampu memahami label/id UI lebih baik dibandingkan metrik leksikal.
- **Audit trail** – hasil skor beserta test case tersimpan di CSV sehingga dosen/penguji dapat menelusuri setiap angka.
- **Komplementer** – digunakan bersama cosine & BDD; jika semua metrik selaras, ini meningkatkan reliabilitas penilaian.
- **Dapat direplikasi** – selama API key sama, evaluator dapat dijalankan ulang kapan saja untuk memverifikasi hasil skripsi.

## Testing & Debugging

- **Local run**: gunakan `python telegram_bot.py` dengan `logging` bawaan untuk melihat request-response.
- **Telemetry**: bidang `metrics.duration_seconds` di dataset bisa diisi otomatis bila Anda mencatat waktu generasi.
- **Troubleshooting**:
  - API error Gemini → cek koneksi & validitas `GOOGLE_API_KEY`.
  - Bot tidak menjawab → pastikan `TELEGRAM_BOT_TOKEN` benar dan webhook tidak aktif.
  - Evaluasi gagal → periksa struktur JSON dan pastikan minimal satu test case per skenario.

## Docker (opsional)

Tersedia `Dockerfile` dan `docker-compose.yml`. Contoh build & run:

```pwsh
docker compose up --build
```

Pastikan variabel environment sudah diatur (lihat file compose untuk detail).

## Kontribusi

1. Fork repository & buat branch baru
2. Kerjakan perubahan, jalankan lint/test
3. Buka Pull Request ke `main`
