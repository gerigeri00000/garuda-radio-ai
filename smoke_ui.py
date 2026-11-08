"""Smoke test UI: jalankan server Gradio sebentar, cek HTTP 200 + judul GARUDA.

Memakai kwargs yang sama dengan app.launch() supaya kegagalan launch ikut terdeteksi.
Jalankan:  .venv\\Scripts\\python.exe smoke_ui.py
"""
import sys
import time
import urllib.request

sys.argv = ["app.py"]
import app  # noqa: E402
import config  # noqa: E402
import gradio as gr  # noqa: E402

demo = app.build_ui()
demo.queue(default_concurrency_limit=2)
demo.launch(server_name="127.0.0.1", server_port=7861, inbrowser=False,
            prevent_thread_lock=True, footer_links=[], ssr_mode=False,
            theme=gr.themes.Soft(), css=app.CSS,
            allowed_paths=[str(config.EXPORT_DIR), str(config.SAMPLES_DIR)])
ok, title = False, False
for _ in range(15):
    try:
        with urllib.request.urlopen("http://127.0.0.1:7861/", timeout=10) as r:
            body = r.read().decode("utf-8", "ignore")
            ok = r.status == 200
            title = "GARUDA" in body
            print("HTTP", r.status, "bytes", len(body), "contains title:", title)
            break
    except Exception as exc:
        print("retry:", exc)
        time.sleep(2)
demo.close()
print("SMOKE", "PASS" if (ok and title) else "FAIL")
