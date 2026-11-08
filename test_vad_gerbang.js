// Uji gerbang sunyi di browser memakai BUNDLE VAD ASLI.
//
// Ini satu-satunya uji yang bisa menangkap:
//   - desain lama (VAD mengklik tombol rekam Gradio)
//   - sunyi menghasilkan klip
//   - transmisi pendek hilang / tergabung salah
//   - mikrofon bocor saat Mulai ditekan dua kali atau Hentikan saat menyiapkan
//
// Skrip diambil langsung dari app.py (VAD_BOOT_JS) setelah placeholder diisi,
// jadi yang diuji adalah kode yang benar-benar dikirim ke browser.
//
// Jalankan: node test_vad_gerbang.js   (dipanggil juga dari test_live.py)

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const FRAME = 1536;
const SR = 16000;
const FRAME_SEC = FRAME / SR;
const BATAS_BICARA = 0.5;

let lulus = 0, gagal = 0;
function check(nama, ok, detail) {
  if (ok) { lulus++; console.log(`  [PASS] ${nama}${detail ? " — " + detail : ""}`); }
  else { gagal++; console.log(`  [FAIL] ${nama}${detail ? " — " + detail : ""}`); }
  return ok;
}

// ---------------------------------------------------------------- ambil skrip
function ambilSkrip() {
  const py = fs.readFileSync("app.py", "utf8");
  const awal = py.indexOf('VAD_BOOT_JS = """');
  if (awal < 0) throw new Error("VAD_BOOT_JS tidak ditemukan di app.py");
  const isi = py.slice(awal + 'VAD_BOOT_JS = """'.length);
  const akhir = isi.indexOf('"""');
  let js = isi.slice(0, akhir);

  // Isi placeholder seperti launch() melakukannya.
  const r = fs.readFileSync("config.py", "utf8");
  const m = r.match(/SILENCE_SECONDS\s*=\s*([\d.]+)/);
  const detik = m ? parseFloat(m[1]) : 3.0;
  js = js.replace(/__VAD_BASE__/g, "/gradio_api/file=models/vad")
         .replace(/__SILENCE_SEC__/g, String(detik))
         .replace(/__SUBMIT_PATH__/g, "/gradio_api/call/ingest_clip");
  return { js, detik };
}

// ------------------------------------------------------------ lingkungan tiruan
function buatLingkungan(urutanProb, catatan) {
  const bundle = fs.readFileSync(
    path.join("models", "vad", "vad-web.bundle.min.js"), "utf8");

  // getUserMedia tiruan: bisa digerbangi supaya "menyiapkan" bisa diuji.
  let gerbangMedia = null;
  const media = {
    sudahDibuka: 0, masihTerbuka: 0,
    getUserMedia: async function () {
      if (gerbangMedia) await gerbangMedia;
      media.sudahDibuka++;
      media.masihTerbuka++;
      const track = { stop: function () { media.masihTerbuka--; } };
      return { getTracks: function () { return [track]; } };
    },
  };

  let indeksProb = 0;
  const ctx = {
    console: { log: function () {}, debug: function () {}, warn: function () {}, error: function () {} },
    setTimeout, clearTimeout, setInterval: function () { return 0; }, clearInterval: function () {},
    URL, Blob, ArrayBuffer, Float32Array, Int16Array, Uint8Array, DataView,
    FormData: function () { this.append = function () {}; },
    JSON, Math, Date, Promise, encodeURIComponent, String, Array, Object, Number,
    WebAssembly: { Memory: function () {}, Instance: function () {} },
    navigator: { mediaDevices: { getUserMedia: media.getUserMedia } },
    location: { href: "http://localhost/" },
    fetch: async function (url) {
      const u = String(url);
      catatan.panggilan.push(u);
      if (u.indexOf("silero_vad.onnx") >= 0) {
        // model ONNX tiruan: harus ArrayBuffer sungguhan
        return { ok: true, arrayBuffer: async function () { return new ArrayBuffer(16); } };
      }
      if (u.indexOf("/upload") >= 0) {
        return { ok: true, json: async function () { return ["/tmp/klip.wav"]; } };
      }
      if (/\/call\/[^/]+$/.test(u)) {
        return { ok: true, json: async function () { return { event_id: "e1" }; } };
      }
      return {
        ok: true, body: {},
        text: async function () { return 'data: ["Klip masuk antrian."]\\n\\n'; },
      };
    },
  };

  // AudioContext tiruan: cukup untuk memicu audioWorklet + kirim frame.
  let kirimFrame = null;
  ctx.AudioContext = function () {
    this.audioWorklet = { addModule: async function () {} };
    this.close = function () {};
  };
  ctx.MediaStreamAudioSourceNode = function () { this.connect = function () {}; };
  ctx.AudioWorkletNode = function (c, nama, opsi) {
    const self = this;
    this.port = {
      onmessage: null,
      // tidak dipakai; frame dikirim manual di bawah
    };
    this.connect = function () {};
    this.disconnect = function () {};
    kirimFrame = async function (skor) {
      indeksProb = Math.min(indeksProb, urutanProb.length - 1);
      if (self.port.onmessage) {
        // FrameProcessor milik AudioNodeVAD memakai modelProcessFunc yang
        // kita ganti lewat ort tiruan di bawah.
        await self.port.onmessage({ data: { message: "AUDIO_FRAME", data: new Float32Array(FRAME).buffer } });
      }
    };
  };

  const dokumen = {
    currentScript: { src: "http://localhost/gradio_api/file=models/vad/vad-web.bundle.min.js" },
    baseURI: "http://localhost/",
    getElementsByTagName: function () { return [{ src: "http://localhost/models/vad/vad-web.bundle.min.js" }]; },
    querySelector: function () { return null; },
    querySelectorAll: function () { return []; },
    getElementById: function () { return null; },
    createElement: function () { return { style: {}, setAttribute: function () {} }; },
    body: { appendChild: function () {} },
    addEventListener: function () {},
  };

  ctx.document = dokumen;
  ctx.self = ctx;
  ctx.window = ctx;
  vm.createContext(ctx);

  // ort tiruan: model VAD mengembalikan probabilitas dari `urutanProb`.
  ctx.ort = {
    env: { wasm: { wasmPaths: "" } },
    Tensor: function (t, d) { this.type = t; this.data = d; },
    InferenceSession: {
      create: async function () {
        return {
          inputNames: ["input", "state", "sr"],
          outputNames: ["output", "stateN"],
          run: async function () {
            const skor = urutanProb[Math.min(indeksProb, urutanProb.length - 1)];
            indeksProb++;
            return {
              hn: new ctx.ort.Tensor("float32", Array(128).fill(0)),
              cn: new ctx.ort.Tensor("float32", Array(128).fill(0)),
              output: { data: [skor] },
            };
          },
        };
      },
    },
  };

  vm.runInContext(bundle, ctx);
  return { ctx, media, kirimFrame: () => kirimFrame, setGerbangMedia: (p) => { gerbangMedia = p; } };
}

// Jalankan skrip GARUDA di lingkungan tiruan.
function pasangSkrip(js, ctx) {
  vm.runInContext(js.replace(/^[\s\S]*?<script>/, "").replace(/<\/script>[\s\S]*$/, ""), ctx);
}

// ---------------------------------------------------------------- skenario uji
const bicara = (n) => Array(n).fill(0.9);
const sunyi = (n) => Array(n).fill(0.05);

async function skenario(detik, prob, opsi) {
  opsi = opsi || {};
  const { js } = ambilSkrip();
  const catatan = { panggilan: [] };
  const env = buatLingkungan(prob, catatan);
  pasangSkrip(js, env.ctx);

  await env.ctx.__GARUDA_SET_LISTENING__(true);
  // Tunggu MicVAD.new selesai (ia async dan memanggil getUserMedia)
  for (let i = 0; i < 50 && !env.kirimFrame(); i++) await new Promise((r) => setTimeout(r, 10));

  const jumlahFrame = prob.length;
  for (let k = 0; k < jumlahFrame; k++) {
    if (env.kirimFrame()) await env.kirimFrame()();
    await new Promise((r) => setTimeout(r, 0));
  }
  await new Promise((r) => setTimeout(r, 60));

  const unggahan = catatan.panggilan.filter((u) => u.indexOf("/upload") >= 0).length;
  const status = env.ctx.__GARUDA_VAD_STATUS__;
  return { unggahan, status, media: env.media, catatan, ctx: env.ctx, env };
}

async function main() {
  console.log("=".repeat(70));
  console.log("Uji gerbang sunyi VAD (bundle asli, skrip dari app.py)");
  console.log("=".repeat(70));

  const { js, detik } = ambilSkrip();
  const sil = detik;

  // --- statis: tidak boleh ada pola klik tombol rekam --------------------
  check("skrip tidak menyebut recBtn/record-button",
    !/recBtn|record-button|stopBtn|\.click\(\)/.test(js),
    "pola desain lama harus hilang");
  check("skrip memakai gerbang sunyi sendiri",
    /SILENCE_FRAMES/.test(js) && /onFrameProcessed/.test(js));
  check("skrip mengubah Float32Array jadi WAV",
    /RIFF/.test(js) && /setInt16/.test(js) && /audio\/wav/.test(js));
  check("skrip mengirim ke /gradio_api/upload",
    js.indexOf("/gradio_api/upload") >= 0);
  check("redemptionFrames pendek (bukan " + sil + " detik)",
    /REDEMPTION_FRAMES\s*=\s*8\b/.test(js),
    "harus 8, bukan dihitung dari SILENCE_SECONDS");

  // --- sunyi total tidak menghasilkan klip ------------------------------
  const s1 = await skenario(sil, sunyi(200));
  check("sunyi 20s tidak menghasilkan satupun klip", s1.unggahan === 0,
    `${s1.unggahan} unggahan`);

  // --- satu transmisi pendek -------------------------------------------
  const s2 = await skenario(sil, [...bicara(5), ...sunyi(80)]);
  check("0.5s bicara menghasilkan tepat 1 klip", s2.unggahan === 1,
    `${s2.unggahan} unggahan`);

  // --- dua transmisi berjeda pendek: digabung --------------------------
  const s3 = await skenario(sil, [...bicara(3), ...sunyi(12), ...bicara(3), ...sunyi(80)]);
  check("dua transmisi berjeda pendek digabung jadi 1 klip", s3.unggahan === 1,
    `${s3.unggahan} unggahan`);

  // --- dua transmisi berjeda panjang: terpisah -------------------------
  const jedaPanjang = Math.ceil((sil + 1) / FRAME_SEC);
  const s4 = await skenario(sil, [...bicara(3), ...sunyi(jedaPanjang), ...bicara(3), ...sunyi(80)]);
  check("dua transmisi berjeda " + sil + "s+ jadi 2 klip", s4.unggahan === 2,
    `${s4.unggahan} unggahan`);

  // --- mikrofon dilepas saat Hentikan ---------------------------------
  const s5 = await skenario(sil, [...bicara(3), ...sunyi(80)]);
  await s5.ctx.__GARUDA_SET_LISTENING__(false);
  await new Promise((r) => setTimeout(r, 30));
  check("Hentikan melepas mikrofon", s5.media.masihTerbuka === 0,
    `${s5.media.masihTerbuka} masih terbuka`);
  check("Hentikan mengubah status jadi nonaktif", s5.ctx.__GARUDA_VAD_STATUS__ === "nonaktif",
    s5.ctx.__GARUDA_VAD_STATUS__);

  // --- Hentikan saat masih menyiapkan: mikrofon tetap dilepas ----------
  {
    const { js: skrip } = ambilSkrip();
    const catatan = { panggilan: [] };
    const env = buatLingkungan(sunyi(10), catatan);
    let lepas;
    const tunggu = new Promise((r) => { lepas = r; });
    env.setGerbangMedia(tunggu);   // getUserMedia menggantung
    pasangSkrip(skrip, env.ctx);

    const mulai = env.ctx.__GARUDA_SET_LISTENING__(true);
    await new Promise((r) => setTimeout(r, 20));
    await env.ctx.__GARUDA_SET_LISTENING__(false);   // Hentikan saat menyiapkan
    lepas();                                          // getUserMedia selesai
    await mulai;
    await new Promise((r) => setTimeout(r, 40));

    check("Hentikan saat menyiapkan tidak meninggalkan mikrofon terbuka",
      env.media.masihTerbuka === 0, `${env.media.masihTerbuka} masih terbuka`);
    check("Hentikan saat menyiapkan tidak mengklaim aktif",
      env.ctx.__GARUDA_VAD_STATUS__ !== "aktif", env.ctx.__GARUDA_VAD_STATUS__);
  }

  // --- Mulai dua kali tidak membuka dua stream -------------------------
  {
    const { js: skrip } = ambilSkrip();
    const catatan = { panggilan: [] };
    const env = buatLingkungan(sunyi(10), catatan);
    pasangSkrip(skrip, env.ctx);
    await Promise.all([
      env.ctx.__GARUDA_SET_LISTENING__(true),
      env.ctx.__GARUDA_SET_LISTENING__(true),
    ]);
    await new Promise((r) => setTimeout(r, 40));
    check("Mulai dua kali hanya membuka 1 stream mikrofon",
      env.media.sudahDibuka === 1, `${env.media.sudahDibuka} stream dibuka`);
  }

  console.log(`\nHASIL: ${lulus}/${lulus + gagal} PASS`);
  process.exit(gagal === 0 ? 0 : 1);
}

main().catch((e) => { console.error("gagal menjalankan uji:", e); process.exit(1); });
