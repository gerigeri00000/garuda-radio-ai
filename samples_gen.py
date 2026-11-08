"""
GARUDA Radio AI - daftar skenario demo (audio simulasi).

Generator audio memakai suara sintetis bahasa Indonesia (Piper TTS, offline) -
bukan rekaman asli. Tujuannya menguji alur penuh: audio -> Whisper -> LLM -> aksi.
"""
from __future__ import annotations

import random
import threading

import config
import database as db

SCENARIOS = [
    {
        "id": "01_logistik",
        "title": "A. Permintaan logistik",
        "text": ("Pos Alfa melaporkan stok air bersih tinggal sepuluh dus. "
                 "Mohon tambahan air bersih untuk Pos Alfa."),
        "expect": {"category": "LOGISTICS", "action": ("CREATE_REPORT", "CREATE_TICKET"),
                   "location": "Pos Alfa"},
    },
    {
        "id": "02_peralatan",
        "title": "B. Kerusakan peralatan",
        "text": ("Pos Bravo melapor genset di Pos Bravo rusak dan tidak bisa dinyalakan. "
                 "Perlu pemeriksaan teknisi."),
        "expect": {"category": "EQUIPMENT",
                   "action": ("CREATE_REPORT", "CREATE_TICKET", "CREATE_ALERT"),
                   "location": "Pos Bravo"},
    },
    {
        "id": "03_selesai",
        "title": "C. Penyelesaian kegiatan",
        "text": "Pemeriksaan genset Pos Alfa sudah selesai.",
        "expect": {"category": ("STATUS_UPDATE", "EQUIPMENT"),
                   "action": "UPDATE_STATUS", "location": None},
    },
    {
        "id": "04_medis",
        "title": "D. Permintaan bantuan medis",
        "text": "Ada warga di Pos Charlie membutuhkan bantuan medis segera.",
        "expect": {"category": "MEDICAL", "action": "CREATE_ALERT", "location": "Pos Charlie"},
    },
    {
        "id": "05_infrastruktur",
        "title": "E. Gangguan infrastruktur",
        "text": "Jembatan Desa Contoh rusak dan tidak bisa dilalui. Mohon segera diperiksa.",
        "expect": {"category": "INFRASTRUCTURE", "action": "UPDATE_MAP",
                   "location": "Jembatan Desa Contoh"},
    },
    {
        "id": "06_berisik",
        "title": "F. Suara berisik / informasi tidak lengkap",
        "text": "Halo base, di sini ada kendala, tolong dikirim bantuan, ah, "
                "tidak tahu jumlahnya, koneksi kurang jelas, gitu saja.",
        "expect": {"needs_verification": True,
                   "action": ("CREATE_ALERT", "CREATE_REPORT", "NONE")},
    },
]

# Skenario tambahan: audio permintaan logistik yang menghasilkan TIKET (aksi B)
SCENARIOS_EXTRA = {
    "solar": {
        "id": "07_solar",
        "title": "Permintaan logistik (tiket)",
        "text": "Pos Bravo membutuhkan tambahan tiga puluh liter solar.",
        "expect": {"category": "LOGISTICS", "action": "CREATE_TICKET", "location": "Pos Bravo"},
    }
}


# ---------------------------------------------------------------------------
# TTS lokal (Piper voice Indonesia, offline via piper-tts + onnxruntime)
# ---------------------------------------------------------------------------
_TTS_LOCK = threading.Lock()
TTS_DIR = config.MODELS_DIR / "tts" / "vits-piper-id_ID-news_tts-medium"
_TTS_VOICE = None


def tts_installed() -> bool:
    return (TTS_DIR / "id_ID-news_tts-medium.onnx").exists()


def _get_voice():
    global _TTS_VOICE
    if _TTS_VOICE is None:
        if not tts_installed():
            raise RuntimeError(
                "Voice TTS Indonesia belum ada di models/tts/.\n"
                "Jalankan:  python setup_models.py tts-id")
        from piper import PiperVoice

        _TTS_VOICE = PiperVoice.load(
            TTS_DIR / "id_ID-news_tts-medium.onnx",
            config_path=TTS_DIR / "id_ID-news_tts-medium.onnx.json")
    return _TTS_VOICE


def synthesize(text: str, out_path) -> str:
    """Suara sintetis bahasa Indonesia (Piper). Offline, tidak sensitif."""
    import numpy as np
    import soundfile as sf

    with _TTS_LOCK:
        voice = _get_voice()
        chunks = list(voice.synthesize(text))
        audio = np.concatenate([c.audio_float_array for c in chunks])
        sf.write(str(out_path), audio, voice.config.sample_rate)
    return str(out_path)


def _add_noise(src_wav, dst_wav, snr_db: float = 8.0):
    """Derau kuat (untuk menguji sistem saat audio buruk)."""
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(src_wav), always_2d=False)
    power = np.mean(data ** 2) if len(data) else 1e-6
    noise = np.random.normal(0, np.sqrt(power / (10 ** (snr_db / 10))), len(data))
    sf.write(str(dst_wav), (data + noise) * 0.9, sr)
    return str(dst_wav)


def generate(scenario: dict, noise: bool = False) -> str:
    base = _base_path(scenario)
    synthesize(scenario["text"], base)
    warm = wav_rms(base)
    if noise:
        # Audio "berisik": sinyal ber-SNR rendah + informasi tidak lengkap di skripnya
        noisy = dict(scenario)
        noisy["id"] = scenario["id"] + "_noisy"
        dst = _base_path(noisy)
        synthesize(scenario["text"], dst)
        return _add_noise(dst, dst, snr_db=3.0)
    append_radio_noise(base, snr_db=18.0)
    print(f"[sample] {base.name}: rms={warm:.3f}")
    return str(base)


def wav_rms(path) -> float:
    import numpy as np
    import soundfile as sf

    data, _ = sf.read(str(path), always_2d=False)
    return float(np.sqrt(np.mean(np.asarray(data) ** 2))) if len(data) else 0.0


def _base_path(scenario: dict):
    return config.SAMPLES_DIR / f"{scenario['id']}.wav"


def append_radio_noise(path, snr_db: float = 18.0):
    """Tambah derau ringan ala radio ke file (level wajar, tetap bisa ditranskripsi)."""
    import numpy as np
    import soundfile as sf

    data, sr = sf.read(str(path), always_2d=False)
    if not len(data):
        return
    power = float(np.mean(data ** 2)) or 1e-6
    noise = np.random.normal(0, np.sqrt(power / (10 ** (snr_db / 10))), len(data))
    sf.write(str(path), (data + noise) * 0.9, sr)


def ensure_samples(force: bool = False) -> list[str]:
    """Buat sample yang belum ada (dipanggil tombol 'Buat contoh audio')."""
    made = []
    for sc in SCENARIOS:
        path = config.SAMPLES_DIR / f"{sc['id']}.wav"
        if path.exists() and not force:
            made.append(str(path))
            continue
        made.append(generate(sc))
    return made


def describe_tts() -> str:
    if not tts_installed():
        return "⚠ Voice TTS Indonesia belum ada - jalankan: python setup_models.py tts-id"
    return "TTS Piper Indonesia (offline) siap."


def allowed_location_choices() -> list[str]:
    return list(config.DEMO_LOCATIONS.values())


def seed_demo_reports(n: int = 12) -> int:
    """Isi data demo SIMULASI (bukan hasil AI) ketika database kosong.

    Tujuannya supaya dashboard/peta/rekap bisa dinilai tanpa audio.
    Semua laporan ditandai DEMO.
    """
    if db.stats()["reports"] > 0:
        return 0
    rng = random.Random(7)
    units = ["Pos Alfa", "Pos Bravo", "Pos Charlie", "Pos Delta"]
    categories = ["LOGISTICS", "EQUIPMENT", "STATUS_UPDATE", "MEDICAL", "INFRASTRUCTURE", "GENERAL"]
    created = 0
    for i in range(n):
        cat = categories[i % len(categories)]
        unit = units[i % len(units)]
        summary = {
            "LOGISTICS": "Persediaan air bersih menipis",
            "EQUIPMENT": "Genset perlu pemeriksaan",
            "STATUS_UPDATE": "Pemeriksaan peralatan selesai",
            "MEDICAL": "Permintaan bantuan medis",
            "INFRASTRUCTURE": "Jembatan rusak",
            "GENERAL": "Laporan rutin pos",
        }[cat]
        rid = db.add_report(transcript_id=None, category=cat, unit=unit, summary=f"[DEMO] {summary}",
                            item="Air bersih" if cat == "LOGISTICS" else None,
                            quantity=float(rng.randint(5, 30)) if cat == "LOGISTICS" else None,
                            unit_measure="dus" if cat == "LOGISTICS" else None,
                            location=unit if unit.lower() in config.DEMO_LOCATIONS else None,
                            priority="HIGH" if cat == "MEDICAL" else "MEDIUM",
                            action="CREATE_REPORT", confidence=0.8,
                            needs_verification=False, llm_valid=False, status="NEW")
        if cat == "LOGISTICS":
            db.add_ticket(report_id=rid, kind="Permintaan logistik", item="Air bersih",
                          quantity=10.0, unit_measure="dus", requester=unit,
                          priority="MEDIUM", status="Pending", notes="[DEMO]")
        if cat == "MEDICAL":
            db.add_alert(report_id=rid, category="MEDICAL", priority="HIGH",
                         message="[DEMO] Permintaan bantuan medis", location=unit,
                         status="Needs Review")
        if cat == "INFRASTRUCTURE":
            db.add_map_update(report_id=rid, location_key="jembatan desa contoh",
                              location_name="Jembatan Desa Contoh",
                              new_status="Rusak - tidak bisa dilalui",
                              note="[DEMO] Jembatan dilaporkan rusak")
        created += 1
    db.log("system", None, "seed_demo", {"count": created}, "setup")
    return created
