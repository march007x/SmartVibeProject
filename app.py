# =============================================================
# SmartVibe v2.3 — เฝ้าระวังความเสียหายโครงสร้างจากการสั่นสะเทือน
# =============================================================

import time
import streamlit as st
import pandas as pd
import numpy as np
import requests
from scipy.signal import welch
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="SmartVibe v2.3", layout="wide")
st.title("SmartVibe v2.3: เฝ้าระวังโครงสร้างอาคารจากการสั่นสะเทือน")

# -------------------------------------------------------------
# ⚙️ ตั้งค่า Firebase — แก้ที่นี่ที่เดียว
# -------------------------------------------------------------
FIREBASE_DOMAIN = "ffffff-4d86d-default-rtdb.asia-southeast1.firebasedatabase.app"
DB_PATH = "History3F"          # ต้องตรงกับ DB_PATH ในโค้ด ESP32

# 🔑 Database secret
AUTH_TOKEN = "lKPFU3F0od6iwURlOuHuPmfDIKKL09KIpLj62wzR"
FIREBASE_URL = f"https://{FIREBASE_DOMAIN}/{DB_PATH}.json"
QUERY = ('?orderBy="$key"&limitToLast=450' if not AUTH_TOKEN
         else f'?auth={AUTH_TOKEN}&orderBy="$key"&limitToLast=450')

# -------------------------------------------------------------
NOMINAL_FS   = 20.0  # ปรับเป็น 20Hz ให้ตรงกับ 50ms ของฝั่ง ESP32
SEARCH_LO    = 3.0
SEARCH_HI    = 15.0
HISTORY_SIZE = 7
MIN_CONSEC   = 3
REFRESH_MS   = 1500
SINE_SHARP   = 40

FLOOR_NAMES = ["ชั้น 1 (ฐานราก)", "ชั้น 2 (กลาง)", "ชั้น 3 (ยอด)"]

ss = st.session_state
ss.setdefault('http_session', requests.Session())
ss.setdefault('last_uptime', 0)
ss.setdefault('stuck_counter', 0)
ss.setdefault('base_T21', None)
ss.setdefault('base_T32', None)
ss.setdefault('T_hist21', [])
ss.setdefault('T_hist32', [])
for i in range(3):
    ss.setdefault(f'base_fn{i}', None)
    ss.setdefault(f'fn_hist{i}', [])
    ss.setdefault(f'amp_hist{i}', [])      
    ss.setdefault(f'rms_ch{i}', 0.0)
    ss.setdefault(f'status{i}', 'green')
    ss.setdefault(f'consec{i}', 0)
    ss.setdefault(f'consec_dir{i}', None)

with st.sidebar:
    st.header("⚙️ การตั้งค่า")
    st.caption(f"📡 DB: `{FIREBASE_DOMAIN.split('.')[0]}`")
    st.caption(f"📂 path: `/{DB_PATH}`")
    st.markdown("---")
    MODE = st.radio("โหมดการวิเคราะห์",
                    ["อัตโนมัติ (แนะนำ)", "ติดตาม fn (White Noise/Sweep)",
                     "ไซน์คงที่ (Transmissibility)"], index=0)
    st.caption("โหมด fn: Health = (fn/fn₀)² × 100 = % ค่า k ที่เหลือ")
    st.caption("โหมดไซน์: Health = ความคล้ายของอัตราส่วนการสั่นระหว่างชั้น "
               "เทียบ baseline (100% = โครงสร้างไม่เปลี่ยน)")
    G2Y = st.slider("🟢→🟡 (Health < กี่ %)", 70, 99, 90, 1)
    Y2R = st.slider("🟡→🔴 (Health < กี่ %)", 40, 95, 70, 1)
    Y2G = st.slider("🟡→🟢 (ขาฟื้น ≥ กี่ %)", 70, 100, 94, 1)
    R2Y = st.slider("🔴→🟡 (ขาฟื้น ≥ กี่ %)", 45, 99, 75, 1)
    st.markdown("---")
    RMS_MIN = st.number_input("RMS ขั้นต่ำ (ยามตรวจแรงกระตุ้น)", 0.0, 1.0, 0.010, 0.005)
    st.markdown("---")
    st.caption("💡 แนะนำ: ตั้งแอปเป็น **White Noise** แล้วใช้โหมดติดตาม fn "
               "จะแม่นและตีความง่ายที่สุด")

# ---------------- ฟังก์ชัน ----------------
def fetch_data():
    try:
        res = ss.http_session.get(FIREBASE_URL + QUERY, timeout=2.5)
        if res.status_code == 401:
            st.sidebar.error("401 Unauthorized — token ผิด หรือกฎความปลอดภัยไม่อนุญาต")
            return pd.DataFrame()
        if res.status_code != 200:
            st.sidebar.error(f"HTTP {res.status_code} — ตรวจ URL/token/กฎความปลอดภัย")
            return pd.DataFrame()
        data = res.json()
        if not data:
            return pd.DataFrame()
        flat = {}
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            if 'uptime_ms' in v:
                flat[k] = v
            else:
                for sk, sv in v.items():
                    if isinstance(sv, dict) and 'uptime_ms' in sv:
                        flat[sk] = sv
        if not flat:
            return pd.DataFrame()
        df = pd.DataFrame.from_dict(flat, orient='index')
        df['uptime_ms'] = pd.to_numeric(df['uptime_ms'], errors='coerce')
        df = df.dropna(subset=['uptime_ms'])
        return (df.sort_values('uptime_ms').drop_duplicates('uptime_ms')
                  .reset_index(drop=True))
    except Exception as e:
        st.sidebar.error(f"fetch error: {e}")
        return pd.DataFrame()

def estimate_fs(t_ms):
    dt = np.diff(t_ms)
    dt = dt[(dt >= 5) & (dt <= 150)] 
    return float(1000.0 / np.median(dt)) if len(dt) >= 10 else NOMINAL_FS

def resample_uniform(t_ms, sig, fs):
    t = (t_ms - t_ms[0]) / 1000.0
    if t[-1] <= 0:
        return sig
    return np.interp(np.arange(0.0, t[-1], 1.0 / fs), t, sig)

def compute_psd(sig, fs):
    sig = sig - np.mean(sig)
    nperseg = min(256, max(64, len(sig) // 2))
    return welch(sig, fs=fs, nperseg=nperseg, window='hann')

def peak_frequency(fw, psd):
    mask = (fw >= SEARCH_LO) & (fw <= SEARCH_HI)
    if not mask.any():
        return None, 0.0
    band = psd[mask]
    idx = np.where(mask)[0][int(np.argmax(band))]
    sharp = float(psd[idx] / (np.median(band) + 1e-20))
    if idx <= 0 or idx >= len(psd) - 1:
        return float(fw[idx]), sharp
    y0, y1, y2 = (np.log(psd[j] + 1e-20) for j in (idx - 1, idx, idx + 1))
    denom = y0 - 2 * y1 + y2
    d = float(np.clip(0.5 * (y0 - y2) / denom, -0.5, 0.5)) if abs(denom) > 1e-12 else 0.0
    return float(fw[idx] + d * (fw[1] - fw[0])), sharp

def band_amplitude(fw, psd, center, half=0.5):
    m = (fw >= center - half) & (fw <= center + half)
    return float(np.sqrt(np.sum(psd[m]))) if m.any() else 0.0

def push_hist(key, val, size=HISTORY_SIZE):
    h = ss[key]
    h.append(val)
    if len(h) > size:
        h.pop(0)
    ss[key] = h
    return float(np.median(h))

def similarity_pct(now, base):
    if base is None or now is None or base <= 0 or now <= 0:
        return None
    return float(100.0 * min(now, base) / max(now, base))

def update_status(pct, ch):
    s, c = ss[f'status{ch}'], ss[f'consec{ch}']
    new_s = s
    if s == 'green':
        c = c + 1 if pct < G2Y else 0
        if c >= MIN_CONSEC:
            new_s, c = 'yellow', 0
    elif s == 'yellow':
        cur = 'up' if pct >= Y2G else ('down' if pct < Y2R else None)
        if cur != ss[f'consec_dir{ch}']:
            c = 0
        ss[f'consec_dir{ch}'] = cur
        if cur is not None:
            c += 1
            if c >= MIN_CONSEC:
                new_s, c = ('green' if cur == 'up' else 'red'), 0
        else:
            c = 0
    elif s == 'red':
        c = c + 1 if pct >= R2Y else 0
        if c >= MIN_CONSEC:
            new_s, c = 'yellow', 0
    ss[f'status{ch}'] = new_s
    ss[f'consec{ch}'] = c
    return new_s, c

def render_status(status, pct, cnt):
    if status == 'green':
        st.success(f"🟢 ปกติ: {pct:.1f}%")
    elif status == 'yellow':
        st.warning(f"🟡 เฝ้าระวัง: {pct:.1f}%  [{cnt}/{MIN_CONSEC}]")
    else:
        st.error(f"🔴 อันตราย: {pct:.1f}%  [{cnt}/{MIN_CONSEC}]")

# ---------------- Main ----------------
def main():
    t0 = time.perf_counter()
    df = fetch_data()
    if df.empty or len(df) <= 100:
        st.info("⏳ กำลังรอข้อมูลจากเซ็นเซอร์...")
        return

    cur = df['uptime_ms'].iloc[-1]
    if cur == ss.last_uptime:
        ss.stuck_counter += 1
    else:
        ss.stuck_counter, ss.last_uptime = 0, cur
    if ss.stuck_counter >= 4:
        st.error("🚨 ข้อมูลหยุดนิ่ง — เซ็นเซอร์อาจเน็ตหลุด หรือบอร์ดค้าง")

    t_ms = df['uptime_ms'].values.astype(float)
    fs = estimate_fs(t_ms)

    fns, sharps, psds_chart, freqs_chart = [], [], [], None
    spectra = []
    for i in range(3):
        col = f'AccX_CH{i}'
        if col not in df.columns:
            fns.append(None); sharps.append(0.0); spectra.append(None)
            psds_chart.append(None)
            continue
        sig = resample_uniform(t_ms, df[col].values.astype(float), fs)
        ss[f'rms_ch{i}'] = float(np.sqrt(np.mean((sig - np.mean(sig)) ** 2)))
        fw, psd = compute_psd(sig, fs)
        spectra.append((fw, psd))
        fn_raw, sh = peak_frequency(fw, psd)
        fns.append(push_hist(f'fn_hist{i}', fn_raw) if fn_raw else None)
        sharps.append(sh)
        valid = fw >= 0.5
        if freqs_chart is None:
            freqs_chart = fw[valid]
        psds_chart.append(psd[valid])

    excitation_ok = all(ss[f'rms_ch{i}'] >= RMS_MIN for i in range(3))

    valid_fns = [f for f in fns if f is not None]
    all_same_freq = (len(valid_fns) == 3 and
                     max(valid_fns) - min(valid_fns) < 0.15)
    very_sharp = float(np.median([s for s in sharps if s > 0] or [0])) > SINE_SHARP
    cvs = [(np.std(ss[f'fn_hist{i}']) / (np.mean(ss[f'fn_hist{i}']) + 1e-12) * 100)
           if len(ss[f'fn_hist{i}']) >= 3 else 99 for i in range(3)]
    frozen_cv = all(c < 0.3 for c in cvs)
    sine_detected = very_sharp or (all_same_freq and frozen_cv)

    if MODE.startswith("อัตโนมัติ"):
        active_mode = "sine" if sine_detected else "fn"
    elif MODE.startswith("ติดตาม fn"):
        active_mode = "fn"
    else:
        active_mode = "sine"

    amps_med = [None, None, None]
    f_drive = float(np.median(valid_fns)) if valid_fns else None
    for i in range(3):
        if spectra[i] is None:
            continue
        fw, psd = spectra[i]
        center = f_drive if (active_mode == "sine" and f_drive) else (fns[i] or f_drive)
        if center:
            a_raw = band_amplitude(fw, psd, center)
            amps_med[i] = push_hist(f'amp_hist{i}', a_raw)

    mode_label = ("🎵 โหมดไซน์คงที่ — วัด Transmissibility ระหว่างชั้น"
                  if active_mode == "sine" else
                  "🌊 โหมดติดตาม fn — วัดความถี่ธรรมชาติ")
    st.info(f"📡 fs จริง ≈ **{fs:.1f} Hz**  |  {mode_label}  |  แรงกระตุ้น: "
            f"{'✅ ปกติ' if excitation_ok else '⚠️ ต่ำเกินไป — พักการตัดสิน'}")

    if sine_detected and active_mode == "fn":
        st.error("🎵 ตรวจพบการกระตุ้นแบบ **ไซน์ความถี่เดียว** แต่โหมดปัจจุบันคือติดตาม fn — "
                 "ค่า fn ที่เห็นคือความถี่ลำโพง ไม่ใช่ของตึก ระบบจะมองไม่เห็นความเสียหาย! "
                 "เปลี่ยนแอปเป็น White Noise/Sweep หรือสลับไปโหมดไซน์คงที่ใน sidebar")
    if sine_detected and active_mode == "sine" and f_drive:
        st.caption(f"ความถี่ลำโพงที่ตรวจพบ ≈ {f_drive:.2f} Hz — "
                   "ระบบใช้อัตราส่วนแอมพลิจูดระหว่างชั้น ณ ความถี่นี้เป็นตัวชี้วัดแทน fn")

    T21_med = T32_med = None
    if active_mode == "sine" and all(a is not None for a in amps_med):
        if amps_med[0] > 1e-12 and amps_med[1] > 1e-12:
            T21_med = push_hist('T_hist21', amps_med[1] / amps_med[0])
            T32_med = push_hist('T_hist32', amps_med[2] / amps_med[1])

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🔒 ล็อก Baseline (โครงสร้างสมบูรณ์ + ลำโพงเปิด)",
                     type="primary", key="btn_lock"):
            ok = excitation_ok and all(f is not None for f in fns)
            if active_mode == "sine":
                ok = ok and (T21_med is not None)
            if ok:
                for i in range(3):
                    ss[f'base_fn{i}'] = fns[i]
                    ss[f'status{i}'] = 'green'
                    ss[f'consec{i}'] = 0
                    ss[f'consec_dir{i}'] = None
                ss.base_T21, ss.base_T32 = T21_med, T32_med
                st.success("ล็อก baseline แล้ว: fn = "
                           f"{[f'{f:.2f}' for f in fns]} Hz"
                           + (f" | T21={T21_med:.3f}, T32={T32_med:.3f}"
                              if T21_med else ""))
                st.rerun()
            else:
                st.warning("ยังล็อกไม่ได้ — สัญญาณอ่อน/หาพีคไม่เจอ ตรวจลำโพงก่อน")
    with c2:
        if st.button("ล้างค่าทั้งหมด", key="btn_reset"):
            for i in range(3):
                ss[f'base_fn{i}'] = None
                ss[f'fn_hist{i}'] = []
                ss[f'amp_hist{i}'] = []
                ss[f'status{i}'] = 'green'
                ss[f'consec{i}'] = 0
                ss[f'consec_dir{i}'] = None
            ss.base_T21 = ss.base_T32 = None
            ss.T_hist21, ss.T_hist32 = [], []
            st.rerun()

    st.markdown("---")

    healths = [None, None, None]
    if active_mode == "fn":
        for i in range(3):
            if fns[i] and ss[f'base_fn{i}']:
                healths[i] = float(np.clip(
                    (fns[i] / ss[f'base_fn{i}']) ** 2 * 100.0, 0, 110))
    else:
        healths[1] = similarity_pct(T21_med, ss.base_T21)
        healths[2] = similarity_pct(T32_med, ss.base_T32)

    amp_max = max([a for a in amps_med if a], default=0.0)

    cols = st.columns(3)
    for i in range(3):
        with cols[i]:
            st.subheader(FLOOR_NAMES[i])
            st.markdown(f"RMS: `{ss[f'rms_ch{i}']:.4f}`")
            if fns[i] is None:
                st.warning("ไม่มีข้อมูลช่องนี้ / หาพีคไม่เจอ")
                continue

            if amps_med[i] is not None:
                ratio_txt = None
                if i > 0 and amps_med[0]:
                    ratio_txt = f"× {amps_med[i]/amps_med[0]:.2f} ของชั้น 1"
                st.metric("แอมพลิจูดการแกว่ง", f"{amps_med[i]:.4f}",
                          delta=ratio_txt, delta_color="off")
                if amp_max > 0:
                    st.progress(min(int(amps_med[i] / amp_max * 100), 100))
                    st.caption(f"แรง {amps_med[i]/amp_max*100:.0f}% "
                               "ของชั้นที่แกว่งแรงสุด")

            if active_mode == "fn":
                base = ss[f'base_fn{i}']
                st.metric("ความถี่ธรรมชาติ fn", f"{fns[i]:.2f} Hz",
                          delta=(f"{fns[i]-base:+.2f} Hz" if base else None),
                          delta_color="normal")
            else:
                if i == 0:
                    st.metric("บทบาท", "จุดอ้างอิง (ฐาน)")
                    st.caption("โหมดไซน์วัดการเปลี่ยนแปลง 'ระหว่างชั้น' — "
                               "ชั้นฐานใช้เป็นตัวหาร")
                    continue
                T_now = T21_med if i == 1 else T32_med
                T_base = ss.base_T21 if i == 1 else ss.base_T32
                label = "T ชั้น2/ชั้น1" if i == 1 else "T ชั้น3/ชั้น2"
                if T_now is not None:
                    st.metric(f"Transmissibility ({label})", f"{T_now:.3f}",
                              delta=(f"{T_now-T_base:+.3f}" if T_base else None),
                              delta_color="normal")

            pct = healths[i]
            if pct is not None:
                st.metric("Health เทียบ Baseline", f"{pct:.1f}%")
                st.progress(min(int(pct), 100))
                if excitation_ok:
                    status, cnt = update_status(pct, i)
                else:
                    st.info("⏸️ แรงกระตุ้นต่ำ — คงสถานะเดิม")
                    status, cnt = ss[f'status{i}'], ss[f'consec{i}']
                render_status(status, pct, cnt)
            else:
                st.info("กด 🔒 ล็อก Baseline ขณะโครงสร้างสมบูรณ์และลำโพงเปิด")

    st.markdown("---")
    st.subheader("แอมพลิจูดการแกว่งแต่ละชั้น")
    if all(a is not None for a in amps_med):
        amp_df = pd.DataFrame(
            {"แอมพลิจูด": amps_med},
            index=["ชั้น 1", "ชั้น 2", "ชั้น 3"])
        try:
            st.bar_chart(amp_df, y_label="แอมพลิจูด (RMS ณ ความถี่กระตุ้น)",
                         horizontal=True)
        except TypeError:
            st.bar_chart(amp_df)
        st.caption("💡 ปกติชั้นบนจะแกว่งแรงกว่าชั้นล่างเสมอ (พฤติกรรมโหมดที่ 1) — "
                   "แอมพลิจูดใช้ดูพฤติกรรม ส่วนการตัดสินความเสียหายใช้ Health % "
                   "เพราะแอมพลิจูดเดี่ยวๆ เปลี่ยนตามระดับเสียงลำโพงได้")

    st.markdown("---")
    st.subheader("กราฟ FFT แยกตามชั้น")
    if freqs_chart is not None and all(p is not None for p in psds_chart):
        chart_df = pd.DataFrame({"ชั้น 1": psds_chart[0], "ชั้น 2": psds_chart[1],
                                 "ชั้น 3": psds_chart[2]}, index=freqs_chart)
        st.line_chart(chart_df[chart_df.index <= 20],
                      x_label="Frequency (Hz)", y_label="PSD")

    with st.expander("ℹ️ debug"):
        dts = np.diff(t_ms)
        good = dts[(dts >= 5) & (dts <= 150)]
        st.write(f"URL: {FIREBASE_DOMAIN}/{DB_PATH}.json")
        st.write(f"จุด: {len(df)} | dt median: {np.median(good):.1f} ms | "
                 f"fs: {fs:.2f} Hz | sharpness: {[f'{s:.0f}' for s in sharps]} | "
                 f"sine_detected: {sine_detected}")
        st.write(f"fn: {[f'{f:.2f}' if f else '—' for f in fns]} | "
                 f"amp: {[f'{a:.4f}' if a else '—' for a in amps_med]} | "
                 f"T21: {T21_med if T21_med else '—'} | "
                 f"T32: {T32_med if T32_med else '—'}")
        st.write(f"⏱️ เวลาประมวลผล: {(time.perf_counter()-t0)*1000:.0f} ms "
                 f"(ต้อง < {REFRESH_MS} ms)")

try:
    main()
except Exception:
    st.error("เกิดข้อผิดพลาดระหว่างประมวลผล:")
    st.exception(Exception)
    raise

st_autorefresh(interval=REFRESH_MS, limit=None, key="smartvibe_autorefresh")
