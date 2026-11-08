"""
GARUDA Radio AI - staging & verifikasi model lokal.

- `setup.bat` memanggil `python setup_models.py whisper-small llm-3b`.
- Model diunduh HANYA di setup (dengan konfirmasi operator), tidak saat app.py jalan.
- Filename & folder mengikuti dokumentasi resmi:
  * Whisper : https://github.com/SYSTRAN/faster-whisper (Systran/faster-whisper-<size>)
  * LLM     : https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF
"""
from __future__ import annotations

import sys
import time

import config

WHISPER_REPOS = {
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}

LLM_FILES = {
    "qwen2.5-1.5b-instruct-q4_k_m": ("Qwen/Qwen2.5-1.5B-Instruct-GGUF",
                                     "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
    "qwen2.5-3b-instruct-q4_k_m": ("Qwen/Qwen2.5-3B-Instruct-GGUF",
                                   "qwen2.5-3b-instruct-q4_k_m.gguf"),
}

WHISPER_FILES = {
    "base": ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
    "small": ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"],
    "large-v3-turbo": ["config.json", "model.bin", "tokenizer.json", "vocabulary.json",
                       "preprocessor_config.json"],
}

TTS_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"
           "vits-piper-id_ID-news_tts-medium.tar.bz2")
TTS_DIR = config.MODELS_DIR / "tts"
TTS_MODEL = TTS_DIR / "vits-piper-id_ID-news_tts-medium" / "id_ID-news_tts-medium.onnx"


def _progress(name: str):
    state = {"last": 0.0}

    def cb(*args):
        # kompatibel: callback lama (done, total) & progres baru (transferred, total)
        done = args[0]
        total = args[1] if len(args) > 1 and isinstance(args[1], int) else 0
        now = time.time()
        if now - state["last"] < 1.0 and done != total:
            return
        state["last"] = now
        mb = done / 1e6
        if total:
            print(f"\r  {name}: {mb:8.1f}/{total/1e6:.1f} MB ({100*done/total:5.1f}%)",
                  end="", flush=True)
        else:
            print(f"\r  {name}: {mb:8.1f} MB", end="", flush=True)

    return cb


def fetch_whisper(size: str) -> None:
    from pathlib import Path

    from huggingface_hub import hf_hub_download

    repo = WHISPER_REPOS[size]
    dest = config.model_path(size, "whisper")
    dest.mkdir(parents=True, exist_ok=True)
    print(f"[whisper:{size}] {repo} -> {dest}")
    for f in WHISPER_FILES.get(size, WHISPER_FILES["small"]):
        target = dest / f
        if target.exists() and target.stat().st_size > 0:
            print(f"  {f}: sudah ada")
            continue
        path = Path(hf_hub_download(repo_id=repo, filename=f, local_dir=str(dest)))
        print(f"\n  {f}: selesai -> {path}")
    print("OK\n")


def fetch_llm(name: str) -> None:
    from pathlib import Path

    from huggingface_hub import hf_hub_download

    repo, filename = LLM_FILES[name]
    dest = config.model_path(name, "llm")
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"[llm:{name}] {repo}/{filename} -> {dest}")
    if dest.exists() and dest.stat().st_size > 100 * 1024 * 1024:
        print("  sudah ada\n")
        return
    path = Path(hf_hub_download(repo_id=repo, filename=filename, local_dir=str(dest.parent)))
    print(f"\n  selesai -> {path}\n")


def fetch_tts() -> None:
    """Voice TTS Indonesia (Piper) untuk membuat 6 audio demo secara offline-setelahnya."""
    import io
    import tarfile
    import urllib.request

    if TTS_MODEL.exists():
        print("[tts:id_ID] sudah ada\n")
        return
    print(f"[tts:id_ID] mengunduh {TTS_URL}")
    data = urllib.request.urlopen(TTS_URL, timeout=600).read()
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as tf:
        tf.extractall(TTS_DIR)
    print(f"  selesai -> {TTS_MODEL}\n")


def status() -> int:
    print("Status model lokal:")
    ok = True
    for size in config.WHISPER_MODELS:
        have = config.whisper_installed(size)
        ok = ok and (have or size != "small")
        print(f"  whisper/{size:16s} : {'OK ' + str(config.model_path(size,'whisper')) if have else 'BELUM ADA'}")
    for name in config.LLM_MODELS:
        have = config.llm_installed(name)
        ok = ok and (have or "3b" not in name)
        print(f"  llm/{name:32s} : {'OK ' + str(config.model_path(name,'llm')) if have else 'BELUM ADA'}")
    print(f"  tts/id_ID-news_tts             : {'OK' if TTS_MODEL.exists() else 'BELUM ADA (opsional, untuk audio demo)'}")
    print(f"  vad (browser)                  : {'OK' if config.vad_assets_ready() else 'BELUM ADA (opsional, untuk deteksi suara otomatis)'}")
    return 0 if ok else 1


VAD_CDN_ASSETS = [
    ("https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@{ver}/dist/silero_vad.onnx",
     "silero_vad.onnx"),
    ("https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@{ver}/dist/bundle.min.js",
     "vad-web.bundle.min.js"),
    ("https://cdn.jsdelivr.net/npm/onnxruntime-web@{ort}/dist/ort.min.js",
     "ort.min.js"),
    ("https://cdn.jsdelivr.net/npm/onnxruntime-web@{ort}/dist/ort-wasm-simd-threaded.wasm",
     "ort-wasm-simd-threaded.wasm"),
]


def install_vad() -> int:
    """Unduh aset VAD sekali (butuh internet). Gagal keras bila tidak lengkap."""
    import urllib.request

    config.VAD_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Mengunduh aset VAD ke {config.VAD_DIR} ...")
    for url_tpl, name in VAD_CDN_ASSETS:
        url = url_tpl.format(ver=config.VAD_ASSET_VERSION,
                             ort=config.VAD_ONNXRT_VERSION)
        dst = config.VAD_DIR / name
        try:
            with urllib.request.urlopen(url, timeout=180) as r, open(dst, "wb") as f:
                f.write(r.read())
            print(f"  OK  {name} ({dst.stat().st_size // 1024} KB)")
        except Exception as exc:
            print(f"  GAGAL {name}: {exc}")
    if config.vad_assets_ready():
        print("Aset VAD lengkap. Mode dengar terus bisa memakai deteksi otomatis.")
        return 0
    hilang = [f for f in config.VAD_REQUIRED_FILES
              if not (config.VAD_DIR / f).exists()]
    print(f"BELUM LENGKAP, file hilang: {', '.join(hilang)}")
    return 1


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        print("Pemakaian: python setup_models.py whisper-small llm-3b status")
        return 0
    for arg in argv:
        if arg == "status":
            status()
        elif arg.startswith("whisper-"):
            fetch_whisper(arg[len("whisper-"):])
        elif arg.startswith("llm-"):
            key = arg[len("llm-"):]
            mapping = {"1.5b": "qwen2.5-1.5b-instruct-q4_k_m",
                       "3b": "qwen2.5-3b-instruct-q4_k_m"}
            fetch_llm(mapping.get(key, key))
        elif arg == "tts-id":
            fetch_tts()
        elif arg == "vad":
            return install_vad()
        else:
            print(f"Argumen tidak dikenal: {arg}")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
