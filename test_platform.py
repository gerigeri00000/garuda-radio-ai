"""
GARUDA Radio AI - uji jalur platform (tanpa model unduhan: cepat & deterministik).

Menjaga perubahan untuk macOS Apple Silicon tanpa mengubah jalur Windows:
  1. pemilihan mesin Whisper (mlx vs faster-whisper) + cadangan CPU,
  2. mode JSON cepat LLM (tanpa grammar dulu, grammar bila keluaran bukan JSON),
  3. pengaman keluaran LLM yang menyalin template skema (nilai contoh karangan),
  4. helper platform & kode keluar `setup_models.py status`,
  5. berkas sementara (fd bocor / WAV tertinggal) dan load/save config,
  6. MLX lintas thread (hanya Apple Silicon, memakai model MLX mini acak).
LLM dipalsukan; MLX nyata hanya di bagian 6, jadi uji ini juga jalan di Windows.

Jalankan:  .venv/bin/python test_platform.py   (Windows: .venv\\Scripts\\python.exe)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path

import ai
import audio
import config
import setup_models

results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    results.append(bool(ok))


@contextmanager
def patched(obj, **attrs):
    """Ganti atribut modul sementara, lalu kembalikan."""
    old = {k: getattr(obj, k) for k in attrs}
    for k, v in attrs.items():
        setattr(obj, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(obj, k, v)


class FakeLlm:
    """Pengganti llama_cpp.Llama: mencatat argumen, membalas sesuai antrian."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def create_chat_completion(self, **kw):
        self.calls.append(kw)
        return {"choices": [{"message": {"content": self.replies.pop(0)}}]}


def uji_mesin_whisper() -> None:
    print("\n1. Pemilihan mesin Whisper")
    cfg = dict(config.DEFAULTS, whisper_model="small")
    semua_ada = dict(whisper_mlx_installed=lambda n: True, whisper_installed=lambda n: True)

    with patched(config, **semua_ada), patched(audio, mlx_available=lambda: True):
        check("auto + MLX tersedia -> mlx",
              audio.whisper_backend(dict(cfg, whisper_device="auto"))["engine"] == "mlx")
        check("mlx -> mlx", audio.whisper_backend(dict(cfg, whisper_device="mlx"))["engine"] == "mlx")
        be = audio.whisper_backend(dict(cfg, whisper_device="cpu"))
        check("cpu -> faster-whisper cpu", be["engine"] == "faster-whisper" and be["device"] == "cpu",
              be["label"])

    with patched(config, **semua_ada), patched(audio, mlx_available=lambda: False):
        be = audio.whisper_backend(dict(cfg, whisper_device="mlx"))
        check("mlx diminta tapi MLX tidak ada (Windows/Intel) -> faster-whisper",
              be["engine"] == "faster-whisper" and be["device"] in ("cpu", "cuda"), be["label"])

    with patched(config, whisper_mlx_installed=lambda n: False, whisper_installed=lambda n: True), \
            patched(audio, mlx_available=lambda: True):
        check("bobot MLX belum ada -> faster-whisper (tanpa error)",
              audio.whisper_backend(cfg)["engine"] == "faster-whisper")

    with patched(config, whisper_mlx_installed=lambda n: False, whisper_installed=lambda n: False), \
            patched(audio, mlx_available=lambda: True):
        check("model tidak ada di mesin mana pun -> engine None",
              audio.whisper_backend(cfg)["engine"] is None)
    with patched(config, whisper_mlx_installed=lambda n: False):
        check("pesan model belum ada menyebut perintah setup platform",
              config.SETUP_CMD in str(audio._not_installed("small")))
    check("_resolve_device tidak pernah meneruskan 'mlx' ke CTranslate2",
          audio._resolve_device({"whisper_device": "mlx"})[0] in ("cpu", "cuda"))

    # Instalasi setup.sh di Mac: turbo hanya versi MLX + small versi CPU.
    mac_setup = dict(whisper_installed=lambda n: n == "small",
                     whisper_mlx_installed=lambda n: n == "large-v3-turbo")
    turbo = dict(cfg, whisper_model="large-v3-turbo")
    with patched(config, **mac_setup), patched(audio, mlx_available=lambda: False):
        be = audio.whisper_backend(turbo)
        check("turbo hanya-MLX + MLX tidak bisa dimuat -> cadangan small di CPU",
              be["engine"] == "faster-whisper" and be["name"] == "small" and "cadangan" in be["label"],
              be["label"])
    with patched(config, **mac_setup), patched(audio, mlx_available=lambda: True):
        be = audio.whisper_backend(dict(turbo, whisper_device="cpu"))
        check("turbo hanya-MLX + perangkat 'cpu' -> cadangan small di CPU",
              be["engine"] == "faster-whisper" and be["name"] == "small", be["label"])
        dipakai_fw: list[dict] = []
        with patched(audio, to_wav16k=lambda p: p, get_whisper=lambda c: dipakai_fw.append(c),
                     _transcribe_faster_whisper=lambda wav, c: (dipakai_fw.append(c) or {
                         "parts": ["ok"], "logprobs": [-0.1], "language": "id",
                         "language_probability": 1.0, "duration": 1.0})):
            res = audio.transcribe("tidak-ada/klip-cpu.wav", dict(turbo, whisper_device="cpu"))
        check("transcribe memakai nama model cadangan (bukan turbo)",
              res["text"] == "ok" and all(c["whisper_model"] == "small" for c in dipakai_fw),
              str([c["whisper_model"] for c in dipakai_fw]))
    with patched(config, whisper_installed=lambda n: False,
                 whisper_mlx_installed=lambda n: n == "large-v3-turbo"), \
            patched(audio, mlx_available=lambda: False):
        msg = str(audio._not_installed("large-v3-turbo"))
        check("hanya-MLX tanpa cadangan -> pesan menyarankan whisper-small (CPU)",
              "whisper-small" in msg and "MLX" in msg)

    with patched(config, whisper_installed=lambda n: n == "small"):
        check("cadangan CPU: model sama tidak ada -> small",
              audio._cpu_fallback_model("large-v3-turbo") == "small")
    with patched(config, whisper_installed=lambda n: False):
        check("cadangan CPU: tidak ada model CTranslate2 -> None",
              audio._cpu_fallback_model("small") is None)

    # MLX gagal saat jalan -> transkrip tetap ada lewat faster-whisper CPU.
    dipakai: list[dict] = []

    def mlx_rusak(*_a, **_k):
        raise RuntimeError("Metal error (simulasi)")

    def fw_palsu(wav, c):
        dipakai.append(c)
        return {"parts": ["halo base"], "logprobs": [-0.2], "language": "id",
                "language_probability": 1.0, "duration": 1.0}

    with ExitStack() as st:
        st.enter_context(patched(config, whisper_mlx_installed=lambda n: True,
                                 whisper_installed=lambda n: n == "small"))
        st.enter_context(patched(audio, mlx_available=lambda: True, to_wav16k=lambda p: p,
                                 _mlx_load=lambda n: None, _transcribe_mlx=mlx_rusak,
                                 get_whisper=lambda c: None, _transcribe_faster_whisper=fw_palsu))
        res = audio.transcribe("tidak-ada/klip.wav", dict(cfg, whisper_model="large-v3-turbo"))
    check("MLX gagal -> transkrip tetap ada (cadangan CPU)",
          res["text"] == "halo base" and "cadangan" in res["backend"], res["backend"])
    check("cadangan memakai faster-whisper small di CPU",
          bool(dipakai) and dipakai[0]["whisper_model"] == "small"
          and dipakai[0]["whisper_device"] == "cpu")


def uji_json_cepat() -> None:
    print("\n2. Mode JSON cepat LLM")
    ok = '{"category": "LOGISTICS", "action": "CREATE_REPORT"}'
    cfg = dict(config.DEFAULTS)

    fake = FakeLlm([ok])
    with patched(ai, get_llm=lambda c: fake):
        out = ai.llm_text("p", "s", dict(cfg, llm_fast_json=True))
    check("cepat + JSON valid -> 1 panggilan tanpa grammar",
          out == ok and len(fake.calls) == 1 and fake.calls[0]["response_format"] is None)
    check("cepat -> berhenti dini pada frasa template", fake.calls[0]["stop"] == [ai.TEMPLATE_MARK])

    fake = FakeLlm(['{"category": "LOGISTICS", "quantity": 10 ', ok])
    with patched(ai, get_llm=lambda c: fake):
        out = ai.llm_text("p", "s", dict(cfg, llm_fast_json=True))
    check("cepat + bukan JSON -> diulang dengan grammar JSON",
          out == ok and len(fake.calls) == 2
          and fake.calls[1]["response_format"] == {"type": "json_object"}
          and fake.calls[1]["stop"] is None)

    fake = FakeLlm([ok])
    with patched(ai, get_llm=lambda c: fake):
        ai.llm_text("p", "s", dict(cfg, llm_fast_json=False))
    check("mode cepat mati -> perilaku asli (1 panggilan dengan grammar)",
          len(fake.calls) == 1 and fake.calls[0]["response_format"] == {"type": "json_object"})

    fake = FakeLlm(["Ringkasan naratif."])
    with patched(ai, get_llm=lambda c: fake):
        ai.llm_text("p", "s", dict(cfg, llm_fast_json=True), json_mode=False)
    check("teks biasa (rekap) -> 1 panggilan tanpa grammar & tanpa stop",
          len(fake.calls) == 1 and fake.calls[0]["response_format"] is None
          and fake.calls[0]["stop"] is None)

    fake = FakeLlm(['{"category": "LOGISTICS", "confidence": NaN}', ok])
    with patched(ai, get_llm=lambda c: fake):
        out = ai.llm_text("p", "s", dict(cfg, llm_fast_json=True))
    check("cepat + NaN (bukan JSON standar) -> diulang dengan grammar",
          out == ok and len(fake.calls) == 2)

    kw = ai._llama_kwargs(cfg)
    check("flash_attn hanya diaktifkan di Apple Silicon",
          ("flash_attn" in kw) == config.IS_APPLE_SILICON, str(sorted(kw)))


def uji_template() -> None:
    print("\n3. Pengaman keluaran yang menyalin template skema")
    teks = ("Halo, Basar, disini ada kendala, tolong dikirim bantuan, ah, tidak tahu "
            "jumlahnya, koneksi kurang jelas, gitu saja.")
    # Keluaran nyata Qwen2.5 3B (jalur grammar) untuk transkrip di atas.
    salinan = ('{"category": "LOGISTICS", "unit": "Pos Alfa atau null", "summary": "Ada kendala", '
               '"item": "barang atau kegiatan atau null", "quantity": 10, '
               '"unit_measure": "dus atau null", "location": "Pos Alfa atau null", '
               '"priority": "MEDIUM", "action": "CREATE_REPORT", "needs_approval": false, '
               '"confidence": 0.85, "missing_information": ["item", "unit_measure"]}')
    with patched(ai, db_free_log=lambda *a, **k: None):  # jangan menulis ke DB pengguna
        res = ai._finish(teks, ai.repair_transcript(teks), salinan, 0.1)
        check("salinan template -> klasifikasi aturan", res["analysis_source"] == "rule_fallback")
        check("tidak menjadi tiket karangan (jumlah 10 dari contoh)",
              res["action"] != "CREATE_TICKET", res["action"])
        check("lokasi karangan 'Pos Alfa' tidak dipakai", res["location"] is None, str(res["location"]))
        check("ditandai perlu verifikasi", res["needs_verification"] is True)

        normal = ('{"category": "LOGISTICS", "unit": "Pos Bravo", "summary": "Minta solar", '
                  '"item": "solar", "quantity": 30, "unit_measure": "liter", "location": null, '
                  '"priority": "MEDIUM", "action": "CREATE_TICKET", "needs_approval": true, '
                  '"confidence": 0.9, "missing_information": []}')
        teks2 = "Pos Bravo membutuhkan tambahan 30 liter solar."
        res = ai._finish(teks2, ai.repair_transcript(teks2), normal, 0.1)
        check("keluaran normal tetap dari LLM", res["analysis_source"] == "llm"
              and res["action"] == "CREATE_TICKET" and res["quantity"] == 30)

    raw = {"category": "LOGISTICS", "item": "solar", "quantity": float("inf"),
           "priority": "MEDIUM", "action": "CREATE_TICKET", "needs_approval": True,
           "confidence": float("nan"), "missing_information": []}
    res = ai.validate(raw, teks2)
    check("NaN/Infinity: tidak jadi tiket & keyakinan tidak melonjak ke 1.0",
          res["action"] != "CREATE_TICKET" and res["quantity"] is None and res["confidence"] <= 0.5,
          f"{res['action']} qty={res['quantity']} conf={res['confidence']}")


def uji_helper() -> None:
    print("\n4. Helper platform & setup_models")
    check("thread LLM >= 2", config.llm_threads() >= 2, str(config.llm_threads()))
    if config.IS_APPLE_SILICON:
        check("Apple Silicon: thread = jumlah P-core",
              config.perf_cores() is not None and config.llm_threads() == config.perf_cores()
              == config.whisper_cpu_threads(), str(config.perf_cores()))
    else:
        check("non-Mac: thread Whisper tetap 4 (nilai asli)", config.whisper_cpu_threads() == 4)
    check("perintah setup sesuai platform",
          config.SETUP_CMD == ("setup.bat" if config.IS_WINDOWS else "./setup.sh"))
    with patched(setup_models, status=lambda: 1):
        check("status model belum lengkap -> kode keluar 1", setup_models.main(["status"]) == 1)
    with patched(setup_models, status=lambda: 0):
        check("status model lengkap -> kode keluar 0", setup_models.main(["status"]) == 0)
    # Kode keluar status mengikuti konfigurasi AKTIF (bukan daftar tetap small + 3B).
    with patched(config, llm_installed=lambda n: True), \
            patched(audio, whisper_backend=lambda c: {"engine": "mlx", "label": "turbo · mlx"}):
        check("status: Whisper aktif (MLX saja) + LLM ada -> lengkap", setup_models._active_ok())
    with patched(config, llm_installed=lambda n: True), \
            patched(audio, whisper_backend=lambda c: {"engine": None, "label": "x · BELUM"}):
        check("status: Whisper aktif tidak ada -> belum lengkap", not setup_models._active_ok())


def _open_fds() -> int | None:
    for d in ("/dev/fd", "/proc/self/fd"):
        try:
            return len(os.listdir(d))
        except OSError:
            continue
    return None  # Windows: tidak bisa dihitung dengan cara ini


def uji_berkas_dan_config() -> None:
    print("\n5. Berkas sementara & config")
    import numpy as np
    import soundfile as sf

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "klip.wav"
        sf.write(str(src), np.zeros(8000, dtype=np.float32), 8000, subtype="PCM_16")
        awal = _open_fds()
        hasil = [audio.to_wav16k(src) for _ in range(30)]
        akhir = _open_fds()
        if awal is not None:
            check("to_wav16k tidak membocorkan fd (macOS: batas 256)", akhir - awal <= 2,
                  f"fd {awal} -> {akhir} setelah 30 konversi")
        for h in hasil:
            h.unlink(missing_ok=True)

        dilihat: list[Path] = []

        def tangkap(wav, c):
            dilihat.append(Path(wav))
            return {"text": "", "backend": "palsu"}

        with patched(audio, _transcribe_wav=tangkap):
            audio.transcribe(src, {})
        check("WAV sementara dihapus setelah transkripsi",
              len(dilihat) == 1 and not dilihat[0].exists() and src.exists())

        cfg_file = Path(tmp) / "config.json"
        salah = "cuda" if config.IS_APPLE_SILICON else "mlx"
        cfg_file.write_text(json.dumps({"whisper_device": salah}), encoding="utf-8")
        with patched(config, CONFIG_FILE=cfg_file):
            check(f"config: perangkat '{salah}' tidak berlaku di platform ini -> auto",
                  config.load_config()["whisper_device"] == "auto")
            cfg_file.unlink()
            config.save_config(dict(config.DEFAULTS, llm_ctx=2048))
            tersimpan = json.loads(cfg_file.read_text(encoding="utf-8"))
            check("save_config hanya menyimpan nilai yang diubah (default platform tidak terkunci)",
                  tersimpan == {"llm_ctx": 2048}, str(tersimpan))
            config.save_config(dict(config.DEFAULTS))
            tersimpan = json.loads(cfg_file.read_text(encoding="utf-8"))
            check("save_config mempertahankan kunci yang sudah ada di berkas",
                  tersimpan == {"llm_ctx": config.DEFAULTS["llm_ctx"]}, str(tersimpan))


def _buat_model_mlx_mini(folder: Path) -> None:
    """Whisper MLX acak berukuran mini (±1 MB) - cukup untuk menguji jalur thread."""
    import mlx.core as mx
    from mlx.utils import tree_flatten
    from mlx_whisper import whisper as w

    dims = dict(n_mels=80, n_audio_ctx=1500, n_audio_state=8, n_audio_head=2, n_audio_layer=1,
                n_vocab=51865, n_text_ctx=448, n_text_state=8, n_text_head=2, n_text_layer=1)
    model = w.Whisper(w.ModelDimensions(**dims), mx.float16)
    params = {k: v.astype(mx.float16) for k, v in tree_flatten(model.parameters())}
    mx.eval(params)
    mx.save_safetensors(str(folder / "weights.safetensors"), params)
    (folder / "config.json").write_text(json.dumps(dict(dims, model_type="whisper")))


def uji_mlx_lintas_thread() -> None:
    print("\n6. MLX lintas thread (Apple Silicon)")
    if not (config.IS_APPLE_SILICON and audio.mlx_available()):
        print("  [SKIP] bukan Apple Silicon / MLX tidak tersedia")
        return
    import numpy as np
    import soundfile as sf

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / "mini"
        folder.mkdir()
        _buat_model_mlx_mini(folder)
        sunyi, bunyi = Path(tmp) / "sunyi.wav", Path(tmp) / "bunyi.wav"
        sf.write(str(sunyi), np.zeros(16000, dtype=np.float32), 16000, subtype="PCM_16")
        rng = np.random.default_rng(0)
        sf.write(str(bunyi), (rng.standard_normal(16000) * 0.1).astype(np.float32), 16000,
                 subtype="PCM_16")
        cfg = dict(config.DEFAULTS, whisper_model="mini-uji", whisper_device="mlx")
        hasil: dict[str, str] = {}

        def jalan(tag: str, path: Path) -> None:
            try:
                hasil[tag] = audio.transcribe(path, cfg)["backend"]
            except Exception as exc:
                hasil[tag] = f"ERROR {exc}"

        def di_thread(tag: str, path: Path) -> None:
            t = threading.Thread(target=jalan, args=(tag, path), name=f"uji-{tag}")
            t.start()
            t.join(timeout=300)

        mlx_models = dict(config.WHISPER_MLX_MODELS, **{"mini-uji": str(folder)})
        with patched(config, WHISPER_MLX_MODELS=mlx_models), \
                patched(audio, _cpu_fallback_model=lambda n: None,  # galat MLX harus terlihat
                        _speech_only=lambda a: a if np.abs(a).max() > 0 else None):
            di_thread("A-sunyi", sunyi)  # muat model pertama kali, tanpa inferensi
            di_thread("B", bunyi)
            di_thread("C", bunyi)
            audio.release_models()
        check("klip sunyi di thread A lalu bunyi di thread B/C tetap di MLX (stream GPU per thread)",
              all("mlx" in hasil.get(t, "") and "ERROR" not in hasil.get(t, "")
                  for t in ("A-sunyi", "B", "C")), str(hasil))


def main() -> int:
    print(f"Platform: {sys.platform}, Apple Silicon: {config.IS_APPLE_SILICON}")
    uji_mesin_whisper()
    uji_json_cepat()
    uji_template()
    uji_helper()
    uji_berkas_dan_config()
    uji_mlx_lintas_thread()
    n_ok = sum(results)
    print(f"\nHASIL: {n_ok}/{len(results)} PASS")
    return 0 if n_ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
