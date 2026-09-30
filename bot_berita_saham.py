#!/usr/bin/env python3
"""
bot_berita_saham.py — kirim berita saham Indonesia terbaru (Google News) ke Telegram.

Dijalankan otomatis oleh GitHub Actions tiap ±15 menit (lihat .github/workflows/berita-saham.yml).
Hanya butuh Python 3 (tanpa library tambahan).

Rahasia yang dibaca dari environment (diisi di GitHub > Settings > Secrets):
  TELEGRAM_BOT_TOKEN   token dari @BotFather
  TELEGRAM_CHAT_ID     ID chat tujuan (lihat PANDUAN-BOT-TELEGRAM.txt)

Berita yang sudah pernah dikirim dicatat di terkirim.json supaya tidak dobel.

Uji tanpa kirim:  python3 bot_berita_saham.py --uji contoh.xml --kering
"""
import argparse, hashlib, html, json, os, re, sys, time, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# ---------------------------------------------------------------- pengaturan
PENCARIAN = ["saham", "IHSG", "emiten", "Tbk", "bursa efek indonesia"]  # kata kunci Google News
JENDELA = "when:1d"            # hanya berita 24 jam terakhir
MAKS_PER_JALAN = 15            # maksimal pesan per kali jalan (sisanya digabung jadi 1 ringkasan)
PERTAMA_KALI = 5               # saat pertama dijalankan, kirim 5 terbaru saja (sisanya ditandai sudah)
SIMPAN_MAKS = 3000             # jumlah ID berita yang diingat
BERKAS_STATUS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "terkirim.json")

WIB = timezone(timedelta(hours=7))
UA = {"User-Agent": "Mozilla/5.0 (bot-berita-saham)"}
SAHAM = re.compile(r"\b(saham|emiten|tbk|ihsg|bursa|bei|idx|dividen|rights? issue|hmetd|ipo|buyback|"
                   r"tender offer|private placement|suspensi|arb|ara|lq45|net (buy|sell)|investor asing|"
                   r"sekuritas|obligasi korporasi|laba bersih|kinerja keuangan)\b", re.I)
TICKER = re.compile(r"(?<![A-Za-z0-9])([A-Z]{4})(?![A-Za-z0-9])")
# penanda saham INDONESIA, dan penanda pasar luar negeri (dibuang kecuali juga menyebut pasar Indonesia)
INDONESIA = re.compile(r"\b(ihsg|bei|bursa efek indonesia|tbk|emiten|idx|lq45|idx30|kompas100|ojk|ksei|"
                       r"asing (borong|jual|lepas|buru|net)|investor asing|saham (bumn|bank|batu ?bara|lq45))\b", re.I)
LUAR_NEGERI = re.compile(r"\b(wall street|nasdaq|dow jones|s&p ?500|nikkei|hang seng|kospi|shanghai|shenzhen|"
                         r"ftse|dax|bursa (asia|eropa|as|amerika|global|jepang|china|korea)|saham (as|amerika|global|"
                         r"teknologi as)|apple|nvidia|tesla|microsoft|amazon|alphabet|meta platforms|the fed)\b", re.I)
BUKAN_TICKER = {"IHSG", "BUMN", "RUPS", "APBN", "APBD", "SAHAM", "KSEI", "LQ45", "MSCI", "FTSE", "WIB",
                "UMKM", "LIVE", "INFO", "NEWS", "HARI", "BARU", "JADI", "BISA", "USAI", "YANG", "DARI",
                "AKAN", "LAGI", "RUPIAH", "BURSA", "ASIA", "WALL", "STREET", "HMETD", "OJK", "BEI"}


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- ambil berita
def ambil_rss(q):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": f"{q} {JENDELA}", "hl": "id", "gl": "ID", "ceid": "ID:id"})
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def urai(xml_bytes):
    out = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        judul = (it.findtext("title") or "").strip()
        sumber = (it.findtext("source") or "").strip()
        if sumber and judul.endswith(" - " + sumber):
            judul = judul[: -len(sumber) - 3].strip()
        try:
            waktu = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            waktu = None
        out.append({"judul": judul, "sumber": sumber, "link": (it.findtext("link") or "").strip(),
                    "waktu": waktu})
    return out


def emiten_di(teks):
    return [t for t in dict.fromkeys(TICKER.findall(teks)) if t not in BUKAN_TICKER]


def saham_indonesia(judul):
    """Berita saham Indonesia: bertema saham DAN (menyebut penanda Indonesia/kode emiten ATAU tidak soal luar negeri)."""
    if not SAHAM.search(judul):
        return False
    return bool(INDONESIA.search(judul) or emiten_di(judul)) or not LUAR_NEGERI.search(judul)


def kunci(b):
    # judul yang sama dari media berbeda dianggap satu berita
    norm = re.sub(r"[^a-z0-9]+", " ", b["judul"].lower()).strip()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


# ---------------------------------------------------------------- telegram
def kirim(token, chat_id, teks):
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": teks, "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    for coba in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                info = json.loads(e.read())
            except Exception:
                info = {}
            if e.code == 429:                      # terlalu cepat: tunggu sesuai saran Telegram
                time.sleep(int(info.get("parameters", {}).get("retry_after", 5)) + 1)
                continue
            alasan = info.get("description", str(e))
            saran = {
                401: "TELEGRAM_BOT_TOKEN salah. Salin ulang token dari @BotFather ke GitHub Secrets.",
                400: "TELEGRAM_CHAT_ID salah (chat not found). Ambil ulang angka chat id dari getUpdates.",
                403: "Bot tidak boleh mengirim ke chat ini. Buka bot di Telegram lalu tekan Start "
                     "(atau Unblock), dan pastikan TELEGRAM_CHAT_ID adalah id ANDA dari getUpdates, "
                     "bukan angka di depan token bot.",
            }.get(e.code, "")
            print(f"::error::Telegram menolak (HTTP {e.code}): {alasan}. {saran}")
            raise SystemExit(1)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            log(f"Gangguan jaringan ke Telegram ({e}), coba lagi...")
            time.sleep(3 * (coba + 1))
            continue
    raise RuntimeError("Gagal kirim ke Telegram setelah 3 percobaan")


def format_pesan(b):
    emiten = emiten_di(b["judul"])
    jam = b["waktu"].astimezone(WIB).strftime("%d %b %H:%M WIB") if b["waktu"] else ""
    baris = [f"<b>{html.escape(b['judul'])}</b>",
             " · ".join(x for x in (html.escape(b["sumber"]), jam) if x)]
    if emiten:
        baris.append("Emiten: " + ", ".join(emiten))
    baris.append(f'<a href="{html.escape(b["link"], quote=True)}">Baca berita</a>')
    return "\n".join(baris)


# ---------------------------------------------------------------- data untuk Interstellar Ring 3D
ARSIP_HARI, ARSIP_MAKS = 7, 1500


def data_interstellar(lama, berita):
    """Arsip 7 hari berita saham Indonesia dalam format yang dibaca Interstellar Ring 3D."""
    posts = {p["id"]: p for p in (lama or {}).get("posts", [])}
    for k, b in berita.items():
        if k in posts or not b["waktu"]:
            continue
        posts[k] = {"id": k, "channel": "berita_saham", "time": b["waktu"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "account": b["sumber"], "title": b["judul"][:200], "url": b["link"],
                    "entities": [{"type": "Emiten", "name": t} for t in emiten_di(b["judul"])], "responses": []}
    kini = datetime.now(timezone.utc)
    batas = (kini - timedelta(days=ARSIP_HARI)).strftime("%Y-%m-%dT%H:%M:%SZ")
    daftar = sorted((p for p in posts.values() if p["time"] >= batas), key=lambda p: p["time"])[-ARSIP_MAKS:]
    return {"topic": "Berita Saham Indonesia", "start": batas, "end": kini.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "channels": [{"id": "berita_saham", "name": "Berita Saham", "color": "#2ee6a6"}], "posts": daftar}


# ---------------------------------------------------------------- utama
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--uji", nargs="*", help="pakai file RSS lokal (untuk uji), bukan Google News")
    ap.add_argument("--kering", action="store_true", help="tampilkan pesan saja, jangan kirim")
    a = ap.parse_args()

    token, chat_id = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not a.kering and not (token and chat_id):
        log("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID belum diisi di GitHub Secrets.")
        sys.exit(1)

    status = {"terkirim": [], "pertama": True}
    if os.path.exists(BERKAS_STATUS):
        status = json.load(open(BERKAS_STATUS, encoding="utf-8"))
        status.setdefault("pertama", False)
    sudah = set(status["terkirim"])

    berita, gagal = {}, 0
    sumber_data = [open(f, "rb").read() for f in a.uji] if a.uji else None
    for i, q in enumerate(PENCARIAN if sumber_data is None else range(len(sumber_data))):
        try:
            xmlb = sumber_data[i] if sumber_data is not None else ambil_rss(q)
            for b in urai(xmlb):
                if saham_indonesia(b["judul"]):       # hanya berita saham Indonesia
                    berita.setdefault(kunci(b), b)
        except Exception as e:
            gagal += 1
            log(f"Gagal ambil '{q}': {e}")
    if gagal and gagal == (len(sumber_data) if sumber_data else len(PENCARIAN)):
        log("Semua sumber gagal diambil; coba lagi di jalan berikutnya.")
        sys.exit(0)

    baru = [(k, b) for k, b in berita.items() if k not in sudah]
    baru.sort(key=lambda kb: kb[1]["waktu"] or datetime.min.replace(tzinfo=timezone.utc))  # lama -> baru
    log(f"{len(berita)} berita saham ditemukan, {len(baru)} belum pernah dikirim.")

    kirim_list = baru
    if status.get("pertama"):
        kirim_list = baru[-PERTAMA_KALI:]
    sisa = []
    if len(kirim_list) > MAKS_PER_JALAN:
        sisa, kirim_list = kirim_list[:-MAKS_PER_JALAN], kirim_list[-MAKS_PER_JALAN:]

    def tandai(k):
        status["terkirim"].append(k)

    if sisa:   # berita lebih lama digabung jadi satu pesan ringkas
        teks = f"<b>{len(sisa)} berita saham lain</b> (lebih lama):\n" + "\n".join(
            f'• <a href="{html.escape(b["link"], quote=True)}">{html.escape(b["judul"][:110])}</a>'
            for _, b in sisa[-25:])
        if a.kering:
            print(teks, "\n")
        else:
            kirim(token, chat_id, teks)
        for k, _ in sisa:
            tandai(k)
    for k, b in kirim_list:
        teks = format_pesan(b)
        if a.kering:
            print(teks, "\n")
        else:
            kirim(token, chat_id, teks)
            time.sleep(1.2)                        # sopan ke Telegram (maks ±1 pesan/detik per chat)
        tandai(k)
    if status.get("pertama"):                      # sisanya dianggap sudah, supaya tidak banjir
        for k, _ in baru:
            if k not in set(status["terkirim"]):
                tandai(k)
        status["pertama"] = False

    status["terkirim"] = status["terkirim"][-SIMPAN_MAKS:]
    status["interstellar"] = data_interstellar(status.get("interstellar"), berita)
    status["terakhir_jalan"] = datetime.now(WIB).isoformat(timespec="seconds")
    if not a.kering:
        json.dump(status, open(BERKAS_STATUS, "w", encoding="utf-8"), ensure_ascii=False)
    log(f"Terkirim {len(kirim_list)} pesan" + (f" + 1 ringkasan ({len(sisa)} berita)" if sisa else "") + ".")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:   # tampilkan alasan gagal dengan jelas di halaman Actions
        import traceback
        traceback.print_exc()
        print(f"::error::Bot gagal: {type(e).__name__}: {e}")
        sys.exit(1)
