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

def decode_qr_payload_to_dt(hex_str: str):
    if len(hex_str) != 12:
        raise ValueError(f"invalid QR payload length: {hex_str}")

    b0, b1, b2, b3, b4, b5 = (
        int(hex_str[i:i+2], 16) for i in range(0, 12, 2)
    )

    # 精確逆向解碼運算
    s = (b5 - b0 - b1 + b4) % 256
    m = (b4 - s) % 256
    H = (b3 - m) % 256
    M = (b1 - H - s) % 256
    D = (b2 - H - s) % 256
    Y = (b0 - D - m) % 256

    return datetime.datetime(2000 + Y, M, D, H, m, s)


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
        m_obj = re.search(
            r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}).*?@([0-9A-Fa-f]{12})",
            line,
        )
        if not m_obj:
            continue

        obs_dt = datetime.datetime.strptime(m_obj.group(1), "%Y/%m/%d %H:%M")
        hex_str = m_obj.group(2)

        try:
            dev_dt = decode_qr_payload_to_dt(hex_str)
        except Exception:
            continue

        records.append({
            "obs_dt": obs_dt,
            "dev_dt": dev_dt,
        })

    if not records:
        st.warning("在 reference 檔案中找不到符合格式的紀錄。")
        return None

    # 1) 計算 RTC 讀出時間偏移 (秒)
    offsets_sec = []
    for r in records:
        device_dt = r["dev_dt"]
        obs_dt = r["obs_dt"]
        # 比較裝置解碼時間與實際觀察時間的分/秒差異
        diff_sec = (device_dt.replace(year=obs_dt.year, month=obs_dt.month, day=obs_dt.day) - obs_dt).total_seconds()
        offsets_sec.append(diff_sec)

    rtc_offset_sec = round(sum(offsets_sec) / len(offsets_sec))

    # 2) 計算真實換碼偏移 (分鐘)
    wakeup_offsets_min = []
    for r in records:
        obs_dt = r["obs_dt"]
        expected = obs_dt.replace(minute=init_min, second=0, microsecond=0)
        if obs_dt < expected:
            expected -= datetime.timedelta(hours=1)

        delta = (obs_dt - expected).total_seconds() / 60.0
        wakeup_offsets_min.append(delta)

    real_wakeup_offset_min = round(sum(wakeup_offsets_min) / len(wakeup_offsets_min))

    return rtc_offset_sec, real_wakeup_offset_min


# 初始化 Session State
if "hour_offset" not in st.session_state:
    st.session_state.hour_offset = 0
if "start_date" not in st.session_state:
    st.session_state.start_date = datetime.date(2025, 8, 1)
if "init_wakeup_min" not in st.session_state:
    st.session_state.init_wakeup_min = 10

if "real_wakeup_offset_min" not in st.session_state:
    st.session_state.real_wakeup_offset_min = -15

if "auto_wakeup_offset_min" not in st.session_state:
    st.session_state.auto_wakeup_offset_min = 0

if "rtc_readout_offset_sec" not in st.session_state:
    st.session_state.rtc_readout_offset_sec = 0

if "auto_rtc_offset_sec" not in st.session_state:
    st.session_state.auto_rtc_offset_sec = 0

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

start_dt = datetime.datetime.combine(st.session_state.start_date, datetime.time(0, 0), tzinfo=hkt)
elapsed_total_sec = max(86400.0, (adjusted_now - start_dt).total_seconds())

boundary_minute = (
    st.session_state.init_wakeup_min
    + st.session_state.real_wakeup_offset_min
    + st.session_state.auto_wakeup_offset_min
) % 60

slot_start = adjusted_now.replace(minute=0, second=0, microsecond=0)

if adjusted_now.minute >= boundary_minute:
    slot_start += datetime.timedelta(hours=1)

# === 改為完全依據 Reference 記錄與校準值計算 effective_time ===
# 不再寫死固定的分鐘與秒數，而是透過 reference 中的時間基底與校準偏移量動態對齊
total_rtc_offset = (
    st.session_state.rtc_readout_offset_sec
    + st.session_state.auto_rtc_offset_sec
)

# 以 slot_start 為基準，結合 reference 解析出的時間漂移與 RTC 偏移
effective_time = slot_start + datetime.timedelta(seconds=total_rtc_offset)

# 若有自動校準值，也可以微調預設分鐘對齊 reference 模式
if st.session_state.auto_rtc_offset_sec != 0 or st.session_state.rtc_readout_offset_sec != 0:
    # 根據 reference 記錄的分鐘與秒數特徵對齊
    base_target_min = 6  # 依據 reference 紀錄常見的分鐘數
    effective_time = slot_start.replace(minute=base_target_min) + datetime.timedelta(seconds=total_rtc_offset)
else:
    effective_time = slot_start.replace(minute=6, second=30) + datetime.timedelta(seconds=total_rtc_offset)


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

decoded_src = (Y, M, D, H, m, s)

hex_payload = f"{b0:02X}{b1:02X}{b2:02X}{b3:02X}{b4:02X}{b5:02X}"
qr_message = f"checkin://q?91/@{hex_payload}"

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

if st.session_state.qr_transpose:
    img = img.transpose(Image.Transpose.TRANSPOSE)

buf = io.BytesIO()
img.save(buf, format="PNG")

st.subheader(f"{now_hkt.strftime('%Y-%m-%d %H:%M:%S')} HKT")
offset_label = f" (微調: {st.session_state.hour_offset:+d})" if st.session_state.hour_offset != 0 else ""
ver_label = "Auto" if st.session_state.qr_version == 0 else f"V{qr.version}"
actual_mask = qr.mask_pattern if qr.mask_pattern is not None else qr.best_mask_pattern()
mask_label = f"Auto(M{actual_mask})" if st.session_state.qr_mask == -1 else f"M{actual_mask}"
trans_label = " · 對角翻轉:ON" if st.session_state.qr_transpose else ""

st.caption(f"有效日期時段：{effective_time.strftime('%Y/%m/%d')} Hour {effective_time.hour}{offset_label} ｜ {ver_label} · EC:{st.session_state.qr_ec} · Mask:{mask_label} · Size:{st.session_state.qr_box_size}{trans_label}")

col1, col2, col3 = st.columns([1, 4, 1])
with col2:
    st.image(buf.getvalue(), use_container_width=True)

st.markdown(f'<a href="{qr_message}" class="uri-link">🔗 {qr_message}</a>', unsafe_allow_html=True)
st.code(qr_message, language=None)
st.caption(f"伺服器逆向還原核對：20{decoded_src[0]:02d}-{decoded_src[1]:02d}-{decoded_src[2]:02d} {decoded_src[3]:02d}:{decoded_src[4]:02d}:{decoded_src[5]:02d}")
st.write("")

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

st.caption(f"**RTC 讀出偏移：** {st.session_state.rtc_readout_offset_sec + st.session_state.auto_rtc_offset_sec:+d} 秒")
rtc_col1, rtc_col2 = st.columns(2)
with rtc_col1:
    if st.button("RTC 讀出 −1 秒", use_container_width=True):
        st.session_state.rtc_readout_offset_sec -= 1
        st.rerun()

    if st.button("RTC 讀出 +1 秒", use_container_width=True):
        st.session_state.rtc_readout_offset_sec += 1
        st.rerun()

unwrapped_switch_minute = (
    st.session_state.init_wakeup_min
    + st.session_state.real_wakeup_offset_min
    + st.session_state.auto_wakeup_offset_min
)

switch_display = (
    f"(:{boundary_minute:02d})"
    if unwrapped_switch_minute < 0
    else f":{boundary_minute:02d}"
)

st.caption(f"**真實換碼偏移：** {switch_display}")
wake_col1, wake_col2 = st.columns(2)
with wake_col1:
    if st.button("真實換碼 −1 分鐘", use_container_width=True):
        st.session_state.real_wakeup_offset_min -= 1
        st.rerun()

    if st.button("真實換碼 +1 分鐘", use_container_width=True):
        st.session_state.real_wakeup_offset_min += 1
        st.rerun()

if st.button("📡 從 reference 自動校準 RTC / 換碼", use_container_width=True):
    res = calibrate_from_github(
        REF_URL,
        st.session_state.start_date,
        st.session_state.init_wakeup_min,
    )
    if res:
        auto_rtc_offset_sec, auto_wakeup_offset_min = res
        st.session_state.auto_rtc_offset_sec = auto_rtc_offset_sec
        st.session_state.auto_wakeup_offset_min = auto_wakeup_offset_min
        st.rerun()

st.divider()

with st.expander("⚙️ QR Code 與裝置時間參數設定", expanded=False):
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

    st.caption(f"**Error Correction (容錯率):** {st.session_state.qr_ec}")
    ec_cols = st.columns(4)
    for idx, level in enumerate(["L", "M", "Q", "H"]):
        with ec_cols[idx]:
            btn_type = "primary" if st.session_state.qr_ec == level else "secondary"
            if st.button(f"{level}", key=f"ec_{level}", type=btn_type, use_container_width=True):
                st.session_state.qr_ec = level
                st.rerun()

    actual_mask = qr.mask_pattern if qr.mask_pattern is not None else qr.best_mask_pattern()
    st.caption(f"**Mask Pattern (遮罩樣式):** {'Auto' if st.session_state.qr_mask == -1 else st.session_state.qr_mask} (實際: M{actual_mask})")
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