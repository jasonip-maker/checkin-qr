import datetime
import io
import re
import urllib.request
import qrcode
from PIL import Image
import streamlit as st

st.set_page_config(page_title="Check-in QR", page_icon="📱", layout="centered")

import os

BASE_MHZ = 16.0
LOCAL_REF_PATH = os.path.join(os.path.dirname(__file__), "reference")
REF_URL = "https://githubusercontent.com"

def calibrate_from_github(url: str, start_date: datetime.date, init_min: int):
    try:
        if os.path.exists(LOCAL_REF_PATH):
            with open(LOCAL_REF_PATH, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        else:
            req = urllib.request.urlopen(url, timeout=5)
            lines = req.read().decode("utf-8").splitlines()
    except Exception as e:
        st.error(f"讀取 reference 檔案失敗：{e}")
        return None

    records = []
    for line in lines:
        m_obj = re.search(r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}).*?@([0-9A-Fa-f]{12})", line)
        if m_obj:
            obs_dt = datetime.datetime.strptime(m_obj.group(1), "%Y/%m/%d %H:%M")
            hex_str = m_obj.group(2)
            b0, b1, b2, b3, b4, b5 = (int(hex_str[i:i+2], 16) for i in range(0, 12, 2))
            dec_Y = -2*b0 - 3*b1 - b2 + b3 + b4 + 3*b5
            dec_M =  2*b0 + 3*b1      - b3 - b4 - 2*b5
            dec_D =  2*b0 + 2*b1 + b2 - b3 - b4 - 2*b5
            dec_H =   -b0 -   b1      + b3      +   b5
            dec_m =    b0 +   b1                -   b5
            dec_s =   -b0 -   b1           + b4 +   b5
            rtc_dt = datetime.datetime(2000 + dec_Y, dec_M, dec_D, dec_H, dec_m, dec_s)
            records.append((obs_dt, rtc_dt))

    if not records:
        st.warning("在 reference 檔案中找不到符合格式的紀錄。")
        return None

    # 1. 偵測真實觸發分鐘（取 >=45 分中最接近整點的換碼邊界，即 :55）
    obs_minutes = [obs.minute for obs, _ in records if obs.minute >= 45]
    wakeup_min = min(obs_minutes) if obs_minutes else 55

    # 2. 優先挑選剛好在換碼邊界 (:55) 觀察到的紀錄，計算最精確的時間分母
    boundary_recs = [(obs, rtc) for obs, rtc in records if obs.minute == wakeup_min]
    target_obs, target_rtc = boundary_recs[0] if boundary_recs else records[0]

    wake_dt = target_obs.replace(minute=wakeup_min, second=0)
    if target_obs.minute < wakeup_min:
        wake_dt -= datetime.timedelta(hours=1)

    # 3. 採用與主程式完全一致的「精確總秒數」作為分母（避免整天數誤差）
    start_dt = datetime.datetime.combine(start_date, datetime.time(0, 0))
    total_sec = max(86400.0, (wake_dt - start_dt).total_seconds())

    # 4. 補償跨日微漂移後，計算第一層「硬體晶振誤差 ppm」
    days_drift = (wake_dt.date() - datetime.date(2026, 11, 12)).days
    hw_fast_sec = (target_rtc + datetime.timedelta(seconds=days_drift) - wake_dt).total_seconds()
    hw_ppm = (hw_fast_sec / total_sec) * 1e6

    # 5. 計算第二層「軟體排程器相對誤差 ppm」（總提前秒數扣除硬體走快秒數）
    total_sw_fast_sec = ((init_min - wakeup_min) % 60) * 60
    sw_rel_sec = total_sw_fast_sec - hw_fast_sec
    sw_ppm = (sw_rel_sec / total_sec) * 1e6

    return round(hw_ppm, 3), round(sw_ppm, 3)

# 初始化 Session State (預設 2025/08/01 起始，共 468 天至 2026/11/12)
if "hour_offset" not in st.session_state:
    st.session_state.hour_offset = 0
if "start_date" not in st.session_state:
    st.session_state.start_date = datetime.date(2025, 8, 1)
if "init_wakeup_min" not in st.session_state:
    st.session_state.init_wakeup_min = 10
if "sw_rel_ppm" not in st.session_state:
    st.session_state.sw_rel_ppm = 5.170    # 第二層：軟體相對硬體再快 210 秒 (+5.170 ppm)
if "hw_drift_ppm" not in st.session_state:
    st.session_state.hw_drift_ppm = 16.929 # 第一層：硬體晶振本身快 690 秒 (+16.929 ppm)
if "qr_version" not in st.session_state:
    st.session_state.qr_version = 0
if "qr_ec" not in st.session_state:
    st.session_state.qr_ec = "L"
if "qr_box_size" not in st.session_state:
    st.session_state.qr_box_size = 24
if "qr_mask" not in st.session_state:
    st.session_state.qr_mask = -1
if "qr_transpose" not in st.session_state:
    st.session_state.qr_transpose = True

EC_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}

hkt = datetime.timezone(datetime.timedelta(hours=8), name="HKT")
now_hkt = datetime.datetime.now(hkt)
adjusted_now = now_hkt + datetime.timedelta(hours=st.session_state.hour_offset)

# 1. 計算自起始日至今的精確運行秒數
start_dt = datetime.datetime.combine(st.session_state.start_date, datetime.time(0, 0), tzinfo=hkt)
elapsed_total_sec = max(86400.0, (adjusted_now - start_dt).total_seconds())
elapsed_days = int(elapsed_total_sec // 86400)

# 2. 第一層：先計算硬體晶振偏移，得出當下「裝置內部硬體 RTC 時鐘」
hw_rtc_fast_sec = (st.session_state.hw_drift_ppm / 1e6) * elapsed_total_sec
hw_clock_now = adjusted_now + datetime.timedelta(seconds=hw_rtc_fast_sec)

# 3. 第二層：在硬體時脈基礎上，疊加軟體排程器相對誤差，找出最近一次 :10 喚醒點
sw_rel_fast_sec = (st.session_state.sw_rel_ppm / 1e6) * elapsed_total_sec
sw_clock_now = hw_clock_now + datetime.timedelta(seconds=sw_rel_fast_sec)
sw_last_wake = sw_clock_now.replace(minute=st.session_state.init_wakeup_min, second=0, microsecond=0)
if sw_clock_now < sw_last_wake:
    sw_last_wake -= datetime.timedelta(hours=1)

# 4. 還原喚醒當刻的硬體 RTC 讀數與真實世界觸發時刻
hw_wake_time = sw_last_wake - datetime.timedelta(seconds=sw_rel_fast_sec)
actual_wakeup_time = hw_wake_time - datetime.timedelta(seconds=hw_rtc_fast_sec)
wakeup_minute = actual_wakeup_time.minute
total_fast_min = round((hw_rtc_fast_sec + sw_rel_fast_sec) / 60)

days_drift = (actual_wakeup_time.date() - datetime.date(2026, 11, 12)).days
effective_time = hw_wake_time - datetime.timedelta(seconds=days_drift)

Y = effective_time.year % 100
M = effective_time.month
D = effective_time.day
H = effective_time.hour
m = effective_time.minute
s = effective_time.second

# 正向編碼計算 (6 Bytes)
b0 = (Y + D + m) % 256
b1 = (M + H + s) % 256
b2 = (D + H + s) % 256
b3 = (H + m) % 256
b4 = (m + s) % 256
b5 = (Y + M + D + H + s) % 256

# 逆向解碼還原驗證
dec_Y = -2*b0 - 3*b1 - b2 + b3 + b4 + 3*b5
dec_M =  2*b0 + 3*b1      - b3 - b4 - 2*b5
dec_D =  2*b0 + 2*b1 + b2 - b3 - b4 - 2*b5
dec_H =   -b0 -   b1      + b3      +   b5
dec_m =    b0 +   b1                -   b5
dec_s =   -b0 -   b1           + b4 +   b5
decoded_src = (dec_Y, dec_M, dec_D, dec_H, dec_m, dec_s)

hex_payload = f"{b0:02X}{b1:02X}{b2:02X}{b3:02X}{b4:02X}{b5:02X}"
qr_message = f"checkin://q?91/@{hex_payload}"

# 產生 QR Code（Auto 模式下 Hour 15 自動切換為 Mask 1，其餘交給 Auto）
target_version = None if st.session_state.qr_version == 0 else st.session_state.qr_version
if st.session_state.qr_mask == -1:
    target_mask = 1 if effective_time.hour == 15 else None
else:
    target_mask = st.session_state.qr_mask

try:
    qr = qrcode.QRCode(
        version=target_version,
        error_correction=EC_MAP[st.session_state.qr_ec],
        box_size=st.session_state.qr_box_size,
        border=2,
        mask_pattern=target_mask,
    )
    qr.add_data(qr_message)
    qr.make(fit=(target_version is None))
except qrcode.exceptions.DataOverflowError:
    qr = qrcode.QRCode(
        version=None,
        error_correction=EC_MAP[st.session_state.qr_ec],
        box_size=st.session_state.qr_box_size,
        border=2,
        mask_pattern=target_mask,
    )
    qr.add_data(qr_message)
    qr.make(fit=True)

img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

# 套用對角線翻轉（沿左上至右下主對角線鏡像 / Transpose）
if st.session_state.qr_transpose:
    img = img.transpose(Image.Transpose.TRANSPOSE)

buf = io.BytesIO()
img.save(buf, format="PNG")

st.subheader(f"{now_hkt.strftime('%Y-%m-%d %H:%M:%S')} HKT")
offset_label = f" (微調: {st.session_state.hour_offset:+d})" if st.session_state.hour_offset != 0 else ""
ver_label = "Auto" if st.session_state.qr_version == 0 else f"V{qr.version}"
mask_label = f"Auto(M{qr.mask_pattern})" if st.session_state.qr_mask == -1 else f"M{qr.mask_pattern}"
trans_label = " · 對角翻轉:ON" if st.session_state.qr_transpose else ""
st.caption(f"有效日期時段：{effective_time.strftime('%Y/%m/%d')} Hour {effective_time.hour}{offset_label} ｜ {ver_label} · EC:{st.session_state.qr_ec} · Mask:{mask_label} · Size:{st.session_state.qr_box_size}{trans_label}")

col1, col2, col3 = st.columns([1, 4, 1])
with col2:
    st.image(buf.getvalue(), use_container_width=True)

st.markdown(f'<a href="{qr_message}" class="uri-link">🔗 {qr_message}</a>', unsafe_allow_html=True)
st.code(qr_message, language=None)
st.caption(f"伺服器逆向還原核對：20{decoded_src[0]:02d}-{decoded_src[1]:02d}-{decoded_src[2]:02d} {decoded_src[3]:02d}:{decoded_src[4]:02d}:{decoded_src[5]:02d}")
st.write("")

# 時段調整按鈕
btn_col1, btn_col2, btn_col3 = st.columns(3)
with btn_col1:
    if st.button("➖ 時段 -1", use_container_width=True):
        st.session_state.hour_offset -= 1
        st.rerun()
with btn_col2:
    if st.button("🔄 刷新/重置", use_container_width=True):
        st.session_state.hour_offset = 0
        st.rerun()
with btn_col3:
    if st.button("➕ 時段 +1", use_container_width=True):
        st.session_state.hour_offset += 1
        st.rerun()

st.divider()
    
# 雙層時鐘漂移微調 (軟體 ppm / 硬體 ppm) 與 GitHub 自動校準
hw_equiv_mhz = BASE_MHZ * (1 + st.session_state.hw_drift_ppm / 1e6)
sw_equiv_mhz = BASE_MHZ * (1 + (st.session_state.hw_drift_ppm + st.session_state.sw_rel_ppm) / 1e6)
st.caption(f"**運行 {elapsed_days} 天（自 {st.session_state.start_date} 起）：** 真實 `:{wakeup_minute:02d}` 換碼 ｜ RTC 讀出 `:{m:02d}:{s:02d}`")

freq_col1, freq_col2 = st.columns(2)
with freq_col1:
    new_hw_ppm = st.number_input(
        f"1. 硬體晶振誤差 ppm ({hw_equiv_mhz:.6f} MHz / 快{round(hw_rtc_fast_sec)}秒)",
        min_value=-200.000, max_value=200.000,
        value=float(st.session_state.hw_drift_ppm), step=0.001, format="%.3f",
    )
    if new_hw_ppm != st.session_state.hw_drift_ppm:
        st.session_state.hw_drift_ppm = new_hw_ppm
        st.rerun()

with freq_col2:
    new_sw_ppm = st.number_input(
        f"2. 軟體相對誤差 ppm ({sw_equiv_mhz:.6f} MHz / 總快{total_fast_min}分)",
        min_value=-200.000, max_value=200.000,
        value=float(st.session_state.sw_rel_ppm), step=0.001, format="%.3f",
    )
    if new_sw_ppm != st.session_state.sw_rel_ppm:
        st.session_state.sw_rel_ppm = new_sw_ppm
        st.rerun()

if st.button("📡 從 reference 自動反推並校準 ppm", use_container_width=True):
    res = calibrate_from_github(REF_URL, st.session_state.start_date, st.session_state.init_wakeup_min)
    if res:
        st.session_state.hw_drift_ppm, st.session_state.sw_rel_ppm = res
        st.rerun()

st.divider()

# QR Code 調整區塊
with st.expander("⚙️ QR Code 與裝置時間參數設定", expanded=False):
    # 1. 對角線翻轉開關
    st.caption(f"**對角線翻轉 (Transpose):** {'開啟 (ON)' if st.session_state.qr_transpose else '關閉 (OFF)'}")
    t_col1, t_col2 = st.columns(2)
    with t_col1:
        if st.button("正常方向", key="trans_off", type="secondary" if st.session_state.qr_transpose else "primary", use_container_width=True):
            st.session_state.qr_transpose = False
            st.rerun()
    with t_col2:
        if st.button("🔄 對角線翻轉", key="trans_on", type="primary" if st.session_state.qr_transpose else "secondary", use_container_width=True):
            st.session_state.qr_transpose = True
            st.rerun()

    # 2. Version 調整
    st.caption(f"**Version (版本):** {'自動 (Auto)' if st.session_state.qr_version == 0 else f'Version {st.session_state.qr_version} (實際: V{qr.version})'}")
    v_col1, v_col2, v_col3 = st.columns(3)
    with v_col1:
        if st.button("➖ Ver -1", use_container_width=True, disabled=(st.session_state.qr_version <= 0)):
            st.session_state.qr_version -= 1
            st.rerun()
    with v_col2:
        if st.button("Auto (V0)", use_container_width=True):
            st.session_state.qr_version = 0
            st.rerun()
    with v_col3:
        if st.button("➕ Ver +1", use_container_width=True, disabled=(st.session_state.qr_version >= 10)):
            st.session_state.qr_version += 1
            st.rerun()

    # 3. Error Correction 容錯率調整
    st.caption(f"**Error Correction (容錯率):** {st.session_state.qr_ec}")
    ec_cols = st.columns(4)
    for idx, level in enumerate(["L", "M", "Q", "H"]):
        with ec_cols[idx]:
            btn_type = "primary" if st.session_state.qr_ec == level else "secondary"
            if st.button(f"{level}", key=f"ec_{level}", type=btn_type, use_container_width=True):
                st.session_state.qr_ec = level
                st.rerun()

    # 4. Mask Pattern 遮罩樣式調整 (Auto + 0~7)
    st.caption(f"**Mask Pattern (遮罩樣式):** {'Auto' if st.session_state.qr_mask == -1 else st.session_state.qr_mask} (實際: {qr.mask_pattern})")
    mask_row1 = st.columns(5)
    with mask_row1[0]:
        btn_type = "primary" if st.session_state.qr_mask == -1 else "secondary"
        if st.button("Auto", key="mask_auto", type=btn_type, use_container_width=True):
            st.session_state.qr_mask = -1
            st.rerun()
    for m in range(4):
        with mask_row1[m + 1]:
            btn_type = "primary" if st.session_state.qr_mask == m else "secondary"
            if st.button(f"{m}", key=f"mask_{m}", type=btn_type, use_container_width=True):
                st.session_state.qr_mask = m
                st.rerun()

    mask_row2 = st.columns(4)
    for m in range(4, 8):
        with mask_row2[m - 4]:
            btn_type = "primary" if st.session_state.qr_mask == m else "secondary"
            if st.button(f"{m}", key=f"mask_{m}", type=btn_type, use_container_width=True):
                st.session_state.qr_mask = m
                st.rerun()

    # 5. Module Size (box_size) 調整
    st.caption(f"**Module Size (方格像素):** {st.session_state.qr_box_size} px")
    m_col1, m_col2, m_col3 = st.columns(3)
    with m_col1:
        if st.button("➖ Size -2", use_container_width=True, disabled=(st.session_state.qr_box_size <= 4)):
            st.session_state.qr_box_size -= 2
            st.rerun()
    with m_col2:
        if st.button("預設 (24px)", use_container_width=True):
            st.session_state.qr_box_size = 24
            st.rerun()
    with m_col3:
        if st.button("➕ Size +2", use_container_width=True, disabled=(st.session_state.qr_box_size >= 24)):
            st.session_state.qr_box_size += 2
            st.rerun()
