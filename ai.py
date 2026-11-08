"""
GARUDA Radio AI - analisis bahasa dengan LLM lokal (llama.cpp).

Satu prompt klasifikasi -> JSON terstruktur.
LLM HANYA menghasilkan data; eksekusi tindakan dilakukan actions.py oleh Python.
Tidak ada cloud API, tidak ada download model otomatis.

Lapisan keamanan (penting untuk model kecil):
1. Transkrip Whisper yang salah dieja diperbaiki dulu secara deterministik
   (kata kunci slang/kode yang diketahui) SEBELUM diberikan ke LLM.
2. Bila LLM gagal/tidak valid, dipakai pengklasifikasi aturan (deterministik).
3. Confidence diturunkan (cap) untuk field enum yang tidak didukung teks.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import config

CATEGORIES = ["LOGISTICS", "MEDICAL", "EQUIPMENT", "INFRASTRUCTURE", "STATUS_UPDATE", "GENERAL", "UNKNOWN"]
PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
ACTIONS = ["CREATE_REPORT", "CREATE_TICKET", "UPDATE_STATUS", "CREATE_ALERT", "UPDATE_MAP", "NONE"]

LOCATION_LIST = "\n".join(f"- {loc['name']}" for loc in config.DEMO_LOCATIONS.values())


class LLMNotAvailable(RuntimeError):
    pass


class AnalysisResult(dict):
    """dict hasil analisis + info audit (raw, valid, waktu)."""


_LLM = {}
_LLM_LOCK = threading.Lock()

SYSTEM_PROMPT = """Anda adalah asisten analisis komunikasi radio operasional berbahasa Indonesia.
Tugas: ubah satu transkrip laporan radio menjadi SATU objek JSON.

ATURAN WAJIB:
- Jawab HANYA dengan JSON valid, tanpa penjelasan, tanpa markdown.
- JANGAN merangkai jumlah, lokasi, unit, atau identitas. Bila tidak disebutkan, isi null.
- Jangan menebak. Informasi penting yang hilang masukkan ke "missing_information".
- "location" hanya boleh salah satu dari DAFTAR LOKASI, atau null bila tidak disebut.
- "category" salah satu: LOGISTICS, MEDICAL, EQUIPMENT, INFRASTRUCTURE, STATUS_UPDATE, GENERAL, UNKNOWN.
- "priority" salah satu: LOW, MEDIUM, HIGH, CRITICAL. Laporan medis darurat = HIGH atau CRITICAL.
- "action" salah satu: CREATE_REPORT, CREATE_TICKET, UPDATE_STATUS, CREATE_ALERT, UPDATE_MAP, NONE.
  * Permintaan barang/logistik (mis. minta tambahan solar/air) -> CREATE_TICKET
  * Laporan stok/keadaan tanpa permintaan (mis. stok air tinggal 10 dus) -> CREATE_REPORT
  * Laporan selesai/status kegiatan -> UPDATE_STATUS
  * Permintaan bantuan medis / darurat -> CREATE_ALERT
  * Infrastruktur rusak di lokasi dikenal -> UPDATE_MAP
  * Laporan informasi biasa -> CREATE_REPORT
  * Bukan laporan operasional -> NONE
- "confidence": angka 0.0-1.0 seberapa yakin klasifikasi ini benar.
- "needs_approval": true bila tindakan berdampak (tiket, status, peta, peringatan).

DAFTAR LOKASI DEMO:
""" + LOCATION_LIST + """

Skema JSON:
{
  "category": "LOGISTICS",
  "unit": "Pos Alfa atau null",
  "summary": "ringkasan singkat satu kalimat",
  "item": "nama barang/kegiatan atau null",
  "quantity": 10 atau null,
  "unit_measure": "dus/liter/unit/... atau null",
  "location": "nama lokasi dari DAFTAR LOKASI atau null",
  "priority": "MEDIUM",
  "action": "CREATE_REPORT",
  "needs_approval": false,
  "confidence": 0.85,
  "missing_information": []
}"""


# ---------------------------------------------------------------------------
# Perbaikan transkrip (deterministik, bukan LLM)
# ---------------------------------------------------------------------------
# Kesalahan Whisper pada voice sintetis "news_tts" + istilah Indonesia (diuji nyata).
REPAIRS = [
    (r"\bmelapur\b|\bmelapokkan\b|\bmelaporkan\b", "melaporkan"),
    (r"\bbersetinggul\b|\bbersetinggal\b|\bbersih tinggol\b|\bbersih tinggal\b", "bersih tinggal"),
    (r"\bsepula ?dus\b|\bsepuladus\b|\bsepula\b", "sepuluh"),
    (r"\bmembutan\b|\bmembutuhkan\b", "membutuhkan"),
    (r"\bsagara\b|\bsegera\b", "segera"),
    (r"\bsudasalise\b|\bsudah selesai\b|\bsudahselesai\b", "sudah selesai"),
    (r"\bdipareksa\b|\bdiperiksa\b", "diperiksa"),
    (r"\bgitusaja\b|\bgitu saja\b", "gitu saja"),
]


def repair_transcript(text: str) -> str:
    """Perbaiki pola salah dengar yang diketahui (hanya untuk input LLM, transkrip asli tetap disimpan)."""
    out = text
    for pattern, repl in REPAIRS:
        out = re.sub(pattern, repl, out, flags=re.I)
    return re.sub(r"\s+", " ", out).strip()


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
def _clean_json(text: str) -> str | None:
    """Ambil blok JSON dari output LLM (tahan markdown / teks tambahan)."""
    text = (text or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        return fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start:end + 1]


def get_llm(cfg: dict):
    name = cfg.get("llm_model", "qwen2.5-3b-instruct-q4_k_m")
    path = config.model_path(name, "llm")
    if not config.llm_installed(name):
        raise LLMNotAvailable(
            f"Model LLM '{name}' belum terinstal di {path}.\n"
            "Jalankan:  setup.bat\n"
            "(setup.bat mengunduh model ke folder models/ - aplikasi tidak mengunduh otomatis)"
        )
    try:
        from llama_cpp import Llama
    except Exception as exc:
        raise LLMNotAvailable(
            "llama-cpp-python belum terpasang. Jalankan setup.bat.\n"
            "Untuk CUDA: pip install llama-cpp-python --only-binary=:all: "
            "--extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu124\n"
            f"Detail: {exc}"
        ) from exc

    key = (name, cfg.get("llm_ctx", 4096), cfg.get("llm_gpu_layers", -1))
    with _LLM_LOCK:
        if key in _LLM:
            return _LLM[key]
        llm = Llama(
            model_path=str(Path(path)),
            n_ctx=int(cfg.get("llm_ctx", 4096)),
            n_gpu_layers=int(cfg.get("llm_gpu_layers", -1)),
            n_threads=max(2, (os_cpu_count() or 4) - 2),
            verbose=False,
        )
        _LLM.clear()
        _LLM[key] = llm
        return llm


def os_cpu_count():
    import os

    return os.cpu_count()


def llm_text(prompt: str, system: str, cfg: dict, max_tokens: int = 600,
             json_mode: bool = True) -> str:
    llm = get_llm(cfg)
    out = llm.create_chat_completion(
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.1,
        top_p=0.9,
        max_tokens=max_tokens,
        response_format={"type": "json_object"} if json_mode else None,
    )
    return out["choices"][0]["message"]["content"] or ""


# ---------------------------------------------------------------------------
# Validasi (Pydantic bila ada, fallback validasi manual)
# ---------------------------------------------------------------------------
def _normalize_location(loc):
    if not loc or not isinstance(loc, str):
        return None, None
    key = loc.strip().lower()
    for k, v in config.DEMO_LOCATIONS.items():
        if key == k or key == v["name"].lower() or k in key:
            return v["name"], k
    return None, None  # lokasi tak dikenal -> jangan dikarang, simpan untuk verifikasi


def _to_float(v):
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"\d+(?:[.,]\d+)?", v)
        if m:
            return float(m.group(0).replace(",", "."))
    return None


def _has_any(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def support_confidence(data: dict, text: str) -> float:
    """Turunkan keyakinan bila field enum tidak didukung kata kunci di transkrip."""
    t = text.lower()
    conf = float(data.get("confidence") or 0.0)
    cat = data.get("category")
    keywords = {
        "LOGISTICS": ("logistik", "air", "solar", "dus", "liter", "stok", "persediaan", "makanan", "bahan bakar"),
        "MEDICAL": ("medis", "dokter", "obat", "luka", "sakit", "korban", "ambulans", "darurat", "pingsan"),
        "EQUIPMENT": ("genset", "mesin", "alat", "radio", "peralatan", "rusak", "teknisi", "pemeriksaan"),
        "INFRASTRUCTURE": ("jembatan", "jalan", "dermaga", "gedung", "infrastruktur", "longsor", "banjir", "putus"),
        "STATUS_UPDATE": ("selesai", "sudah", "tuntas", "berjalan", "proses"),
        "GENERAL": ("laporan", "halo", "base", "rutin", "informasi", "kendala", "bantuan", "tolong"),
    }
    if cat in keywords and not _has_any(t, keywords[cat]):
        conf = min(conf, 0.55)
    if data.get("action") == "CREATE_ALERT" and not _has_any(t, ("segera", "darurat", "medis", "bantuan", "tolong")):
        conf = min(conf, 0.55)
    if data.get("action") == "CREATE_ALERT" and data.get("category") != "MEDICAL":
        conf = min(conf, 0.5)
    if data.get("action") == "UPDATE_STATUS" and not _has_any(t, ("selesai", "sudah", "tuntas")):
        conf = min(conf, 0.6)
    return conf


def validate(raw: dict, source_text: str = "") -> dict:
    """Validasi & normalisasi output LLM. Raise ValueError bila tidak layak."""
    if not isinstance(raw, dict):
        raise ValueError("Output bukan objek JSON")
    src = source_text or ""
    cat = str(raw.get("category", "UNKNOWN")).upper().strip()
    if cat not in CATEGORIES:
        cat = "UNKNOWN"
    priority = str(raw.get("priority", "MEDIUM")).upper().strip()
    if priority not in PRIORITIES:
        priority = "MEDIUM"
    action = str(raw.get("action", "NONE")).upper().strip()
    if action not in ACTIONS:
        action = "NONE"

    loc_name, loc_key = _normalize_location(raw.get("location"))
    missing = raw.get("missing_information") or []
    if isinstance(missing, str):
        missing = [missing]
    missing = [str(m) for m in missing if m]

    # Lokasi yang disebut LLM tapi bukan lokasi DEMO: jangan dipakai, WAJIB ditandai.
    raw_loc = raw.get("location")
    if isinstance(raw_loc, str) and raw_loc.strip() and raw_loc.strip().lower() != "null" and not loc_key:
        missing.append(f"Lokasi tidak dikenali: {raw_loc.strip()[:60]}")
        confidence_cap = 0.5
    else:
        confidence_cap = 1.0

    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(confidence_cap, confidence))

    qty = _to_float(raw.get("quantity"))
    unit = raw.get("unit")
    unit = str(unit).strip() if isinstance(unit, str) and unit.strip() and unit.strip().lower() != "null" else None
    item = raw.get("item")
    item = str(item).strip() if isinstance(item, str) and item.strip() and item.strip().lower() != "null" else None
    summary = raw.get("summary")
    summary = str(summary).strip() if isinstance(summary, str) and summary.strip() else None
    measure = raw.get("unit_measure")
    measure = (str(measure).strip() if isinstance(measure, str) and measure.strip()
               and measure.strip().lower() != "null" else None)

    if action == "UPDATE_MAP" and not loc_key:
        action = "CREATE_REPORT"
        missing.append("Lokasi tidak dikenali")

    # Pengisian dari teks sumber saja (tidak mengarang)
    t = src.lower()
    if not unit:
        loc_hit = next((v["name"] for k, v in config.DEMO_LOCATIONS.items() if k in t), None)
        if loc_hit:
            unit = loc_hit
    if not item and cat in ("LOGISTICS", "EQUIPMENT"):
        for w in ("air bersih", "solar", "genset", "obat", "makanan", "bahan bakar"):
            if w in t:
                item = w
                break

    # Penyelarasan aksi deterministik (hanya bila didukung teks)
    if _has_any(t, ("selesai", "sudah", "tuntas")):
        if action != "UPDATE_STATUS":
            action = "UPDATE_STATUS"
            missing.append("Aksi disesuaikan: laporan penyelesaian -> UPDATE_STATUS")
        if cat not in ("STATUS_UPDATE", "EQUIPMENT"):
            cat = "STATUS_UPDATE"
        if not item and "genset" in t:
            item = "genset"
    if cat == "INFRASTRUCTURE" and loc_key and action != "UPDATE_MAP" and \
            _has_any(t, ("rusak", "putus", "tidak bisa dilalui", "longsor", "banjir")):
        action = "UPDATE_MAP"
        missing.append("Aksi disesuaikan: kerusakan infrastruktur -> UPDATE_MAP")

    # Tiket butuh jumlah + item; bila tidak jelas, turunkan jadi laporan (jangan mengarang)
    if action == "CREATE_REPORT" and _has_any(t, ("butuh", "minta", "tambahan", "perlu", "tolong")) \
            and (item or qty is not None):
        action = "CREATE_TICKET"
        missing.append("Aksi disesuaikan: permintaan barang -> CREATE_TICKET (perlu persetujuan)")
    if action == "CREATE_TICKET":
        if qty is None or qty <= 0:
            action = "CREATE_REPORT"
            missing.append("Jumlah tidak jelas -> tiket tidak dibuat, perlu verifikasi")
        if not item:
            action = "CREATE_REPORT"
            missing.append("Item tidak jelas -> tiket tidak dibuat, perlu verifikasi")

    conf = support_confidence({"category": cat, "action": action, "confidence": confidence}, src)
    if conf < confidence:
        missing.append("Keyakinan diturunkan: kategori/aksi tidak didukung teks")

    res = {
        "category": cat,
        "unit": unit,
        "summary": summary,
        "item": item,
        "quantity": qty,
        "unit_measure": measure,
        "location": loc_name,
        "location_key": loc_key,
        "priority": priority,
        "action": action,
        "needs_approval": action in ("CREATE_TICKET", "UPDATE_STATUS", "CREATE_ALERT", "UPDATE_MAP"),
        "confidence": round(conf, 2),
        "missing_information": sorted(set(missing)),
        "needs_verification": bool(missing) or cat == "UNKNOWN" or conf < 0.65,
    }
    return res


try:  # kontrak eksplisit bila Pydantic tersedia
    from pydantic import BaseModel, Field, field_validator

    class AnalysisModel(BaseModel):
        category: str
        unit: str | None = None
        summary: str | None = None
        item: str | None = None
        quantity: float | None = None
        unit_measure: str | None = None
        location: str | None = None
        priority: str
        action: str
        needs_approval: bool
        confidence: float = Field(ge=0, le=1)
        missing_information: list[str] = []

        @field_validator("category")
        @classmethod
        def _cat(cls, v):
            if v not in CATEGORIES:
                raise ValueError("kategori tidak valid")
            return v

    def _pydantic_check(data: dict) -> None:
        AnalysisModel(**{k: data[k] for k in AnalysisModel.model_fields})
except Exception:  # pragma: no cover
    def _pydantic_check(data: dict) -> None:
        return None


# ---------------------------------------------------------------------------
# Pengklasifikasi aturan (fallback deterministik, bukan LLM)
# ---------------------------------------------------------------------------
def rule_based(text: str) -> dict:
    """Heuristik sederhana. Hasil selalu ditandai untuk verifikasi operator."""
    t = repair_transcript(text).lower()
    loc_name, loc_key = None, None
    for k, v in config.DEMO_LOCATIONS.items():
        if k in t or v["name"].lower() in t:
            loc_name, loc_key = v["name"], k
            break

    qty, measure = None, None
    m = re.search(r"(\d+)\s*(dus|liter|unit|buah|karung|kotak|kaleng|kg|ton)", t)
    if m:
        qty, measure = float(m.group(1)), m.group(2)
    else:
        words = {"sepuluh": 10, "dua puluh": 20, "tiga puluh": 30, "lima": 5, "lima belas": 15}
        for w, n in words.items():
            if w in t:
                qty = float(n)
                break
        mm = re.search(r"(dus|liter|unit|buah)", t)
        if mm:
            measure = mm.group(1)

    item = None
    for w in ("air bersih", "air", "solar", "genset", "obat", "makanan", "bahan bakar"):
        if w in t:
            item = w
            break

    is_medical = _has_any(t, ("medis", "dokter", "luka", "sakit", "korban", "ambulans", "pingsan"))
    is_infra = _has_any(t, ("jembatan", "jalan", "dermaga", "longsor", "banjir", "gedung"))
    is_equipment = _has_any(t, ("genset", "mesin", "alat", "radio", "peralatan", "teknisi"))
    is_logistics = _has_any(t, ("air", "solar", "stok", "dus", "liter", "persediaan", "makanan"))
    is_done = _has_any(t, ("selesai", "sudah", "tuntas"))
    is_request = _has_any(t, ("butuh", "minta", "tambahan", "perlu", "tolong", "bantuan", "segera"))

    if is_medical:
        cat, action = "MEDICAL", "CREATE_ALERT"
    elif is_done and is_equipment:
        cat, action = "STATUS_UPDATE", "UPDATE_STATUS"
    elif is_done:
        cat, action = "STATUS_UPDATE", "UPDATE_STATUS"
    elif is_infra:
        cat = "INFRASTRUCTURE"
        action = "UPDATE_MAP" if loc_key else "CREATE_REPORT"
    elif is_request and (is_logistics or qty is not None):
        cat = "LOGISTICS"
        action = "CREATE_TICKET" if (item and qty is not None) else "CREATE_REPORT"
    elif is_equipment:
        cat, action = "EQUIPMENT", "CREATE_REPORT"
    elif is_logistics:
        cat, action = "LOGISTICS", "CREATE_REPORT"
    else:
        cat, action = "GENERAL", "CREATE_REPORT"

    priority = "HIGH" if is_medical and _has_any(t, ("segera", "darurat")) else "MEDIUM"
    if cat == "MEDICAL":
        priority = "HIGH"
    missing = []
    if action == "CREATE_TICKET":
        if qty is None:
            missing.append("Jumlah tidak jelas")
        if not item:
            missing.append("Item tidak jelas")
    return {
        "category": cat, "unit": loc_name, "summary": text.strip()[:160],
        "item": item or ("genset" if "genset" in t else None), "quantity": qty, "unit_measure": measure,
        "location": loc_name, "location_key": loc_key,
        "priority": priority, "action": action,
        "needs_approval": action in ("CREATE_TICKET", "UPDATE_STATUS", "CREATE_ALERT", "UPDATE_MAP"),
        "confidence": 0.5, "missing_information": missing,
        "needs_verification": True,
    }


# ---------------------------------------------------------------------------
# Analisis
# ---------------------------------------------------------------------------
def analyze(transcript: str, cfg: dict) -> AnalysisResult:
    """Transkrip -> JSON tervalidasi. Raise LLMNotAvailable bila model tidak ada."""
    transcript = (transcript or "").strip()
    if not transcript:
        raise ValueError("Transkrip kosong")
    repaired = repair_transcript(transcript)
    t0 = time.perf_counter()
    try:
        raw_text = llm_text(
            prompt=f"Transkrip radio:\n\"{repaired}\"\n\nBalas dengan satu objek JSON sesuai skema.",
            system=SYSTEM_PROMPT,
            cfg=cfg,
        )
    except LLMNotAvailable:
        raise
    except Exception as exc:
        raw_text = ""
        elapsed = round(time.perf_counter() - t0, 2)
        res = _finish(transcript, repaired, raw_text, elapsed)
        res["llm_error"] = f"Inferensi LLM gagal ({exc}); dipakai klasifikasi aturan"
        db_free_log(repaired, exc)
        return res
    elapsed = round(time.perf_counter() - t0, 2)
    return _finish(transcript, repaired, raw_text, elapsed)


def _finish(transcript: str, repaired: str, raw_text: str, elapsed: float) -> AnalysisResult:
    cleaned = _clean_json(raw_text)
    try:
        parsed = json.loads(cleaned) if cleaned else None
        if not isinstance(parsed, dict):
            raise ValueError("Tidak ada objek JSON")
        result = validate(parsed, repaired)
        _pydantic_check(result)
        result.update({"llm_valid": True, "llm_raw": raw_text, "llm_seconds": elapsed,
                       "llm_error": None, "analysis_source": "llm"})
        return AnalysisResult(result)
    except Exception as exc:
        fallback = rule_based(repaired)
        fallback.update({
            "needs_verification": True,
            "llm_valid": True,             # hasil selalu terstruktur & tervalidasi
            "llm_raw": raw_text,
            "llm_seconds": elapsed,
            "llm_error": f"LLM gagal validasi ({exc}); dipakai klasifikasi aturan",
            "analysis_source": "rule_fallback",
        })
        fallback.setdefault("missing_information", []).append("Diklasifikasi aturan (LLM gagal)")
        db_free_log(transcript, exc)
        return AnalysisResult(fallback)


def db_free_log(transcript: str, exc: Exception) -> None:
    try:
        import database as db

        db.log("ai", None, "llm_fallback", {"error": str(exc)[:300], "text": transcript[:200]})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Rekap naratif (dipakai reports.py bila LLM tersedia)
# ---------------------------------------------------------------------------
RECAP_SYSTEM = """Anda asisten operator radio. Buat ringkasan naratif singkat (maks 120 kata)
dalam bahasa Indonesia dari data laporan operasional (SIMULASI/DEMO).
Jangan mengarang kejadian di luar data. Fokus pada hal penting dan yang butuh perhatian."""


def recap_narrative(payload: dict, cfg: dict) -> str:
    try:
        text = llm_text(
            prompt="Data laporan:\n" + json.dumps(payload, ensure_ascii=False)[:4000] +
                   "\n\nTulis ringkasan naratif singkat (teks biasa, bukan JSON).",
            system=RECAP_SYSTEM,
            cfg=cfg,
            max_tokens=400,
            json_mode=False,
        )
        return text.strip()
    except Exception as exc:
        return f"(Rekap naratif LLM tidak tersedia: {exc})"
