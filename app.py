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


@st.cache_data
def calibrate_from_github_cached(url: str, start_date: datetime.date):
    try:
        if os.path.exists(LOCAL_REF_PATH):
            with open(LOCAL_REF_PATH, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        else:
            req = urllib.request.urlopen(url, timeout=5)
            lines = req.read().decode("utf-8").splitlines()
    except Exception as e:
        return 10, 0.0, -0.45

    records = []
    start_dt = datetime.datetime.combine(start_date, datetime.time(0, 0))

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

        days = (obs_dt - start_dt).total_seconds() / 86400.0
        sec_in_hour = dev_dt.minute * 60 + dev_dt.second
        records.append({"days": days, "sec": sec_in_hour, "obs_dt": obs_dt, "dev_dt": dev_dt})

    if not records:
        return 10, 0.0, -0.45

    # 線性回歸擬合 base_minute, base_second 與 drift_rate
    n = len(records)
    sum_x = sum(r["days"] for r in records)
    sum_y = sum(r["sec"] for r in records)
    sum_xy = sum(r["days"] * r["sec"] for r in records)
    sum_xx = sum(r["days"] ** 2 for r in records)

    denominator = (n * sum_xx - sum_x ** 2)
    if abs(denominator) > 1e-5:
        drift_per_day = (n * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - drift_per_day * sum_x) / n
    else:
        drift_per_day = -0.4489
        intercept = records[0]["sec"]

    base_minute = int(intercept // 60)
    base_second = intercept % 60

    return base_minute, base_second, drift_per_day


# 初始化 Session State
if "hour_offset" not in st.session_state:
    st.session_state.hour_offset = 0
if "start_date" not in st.session_state:
    st.session_state.start_date = datetime.date(2025, 8, 1)

if "rtc_readout_offset_sec" not in st.session_state:
    st.session_state.rtc_readout_offset_sec = 0
if "real_wakeup_offset_min" not in st.session_state:
    st.session_state.real_wakeup_offset_min = 0

# 自動從 reference 載入並校準所有動態參數
fitted_base_min, fitted_base_sec, fitted_drift = calibrate_from_github_cached(
    REF_URL,
    st.session_state.start_date,
)

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

# 1. 先定義 temp_slot_start 與計算經過天數
temp_slot_start = adjusted_now.replace(minute=0, second=0, microsecond=0)
elapsed_days = max(0.0, (temp_slot_start - start_dt).total_seconds() / 86400.0)

# 2. 計算累積 RTC 讀出偏移秒數與分鐘
current_rtc_drift_sec = round(elapsed_days * fitted_drift)
total_rtc_offset_sec = current_rtc_drift_sec + st.session_state.rtc_readout_offset_sec
rtc_offset_min = total_rtc_offset_sec / 60.0

# 3. 換碼邊界僅由天數與逆向速率決定（確保 2026/11/13 準確對應到 (:55)）
total_span_days = 469.5
boundary_rate = -15.0 / total_span_days
raw_boundary_exact = 10.0 + elapsed_days * boundary_rate + st.session_state.real_wakeup_offset_min

rounded_boundary = round(raw_boundary_exact)
boundary_minute = rounded_boundary % 60

if rounded_boundary < 0:
    switch_display = f"(:{boundary_minute:02d})"
else:
    switch_display = f":{boundary_minute:02d}"

# 4. 根據動態換碼邊界決定當前時段起點
# 4.1. 先算出當前小時的基本整點
base_hour_slot = adjusted_now.replace(minute=0, second=0, microsecond=0)

# 4.2. 計算當前小時的「絕對換碼時間點 (Boundary DateTime)」
# boundary_minute 可以是負數或正常 0~59 分鐘
boundary_dt = base_hour_slot + datetime.timedelta(minutes=boundary_minute)

# 4.3. 直接以絕對時間點比較：當前時間若已達到或超過換碼點，則進入下一個小時時段
if adjusted_now >= boundary_dt:
    slot_start = base_hour_slot + datetime.timedelta(hours=1)
else:
    slot_start = base_hour_slot

if rounded_boundary >= 0:
    slot_start += datetime.timedelta(hours=-1)

# 重新以精確的 slot_start 更新 elapsed_days
elapsed_days = max(0.0, (slot_start - start_dt).total_seconds() / 86400.0)
current_rtc_drift_sec = round(elapsed_days * fitted_drift)
total_rtc_offset_sec = current_rtc_drift_sec + st.session_state.rtc_readout_offset_sec

# 5. 計算直覺對應的有效時間
total_sec_in_hour = (10 * 60) + total_rtc_offset_sec

calc_minute = int(total_sec_in_hour // 60)
calc_second = int(total_sec_in_hour % 60)

while calc_second < 0:
    calc_minute -= 1
    calc_second += 60
while calc_second >= 60:
    calc_minute += 1
    calc_second -= 60

effective_time = slot_start.replace(minute=calc_minute, second=calc_second, microsecond=0)

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

col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    st.image(buf.getvalue(), use_container_width=False)

st.markdown(f'<a href="{qr_message}" class="uri-link">🔗 {qr_message}</a>', unsafe_allow_html=True)
st.code(qr_message, language=None)
st.caption(f"伺服器逆向還原核對：20{decoded_src[0]:02d}-{decoded_src[1]:02d}-{decoded_src[2]:02d} {decoded_src[3]:02d}:{decoded_src[4]:02d}:{decoded_src[5]:02d}")
st.write("")

btn_col1, btn_col2, btn_col3 = st.columns(3, gap="xxsmall")
with btn_col1:
    if st.button("➖ 時段 -1", use_container_width=False):
        st.session_state.hour_offset -= 1
        st.rerun()
with btn_col2:
    if st.button("🔄 刷新/重置", use_container_width=False):
        st.session_state.hour_offset = 0
        st.rerun()
with btn_col3:
    if st.button("➕ 時段 +1", use_container_width=False):
        st.session_state.hour_offset += 1
        st.rerun()

st.divider()

st.caption(f"**RTC 讀出偏移：** {total_rtc_offset_sec:+d} 秒 (自動校準)")
rtc_col1, rtc_col2 = st.columns(2, gap="xxsmall")
with rtc_col1:
    if st.button("RTC 讀出 −1 秒", use_container_width=False):
        st.session_state.rtc_readout_offset_sec -= 1
        st.rerun()
with rtc_col2:
    if st.button("RTC 讀出 +1 秒", use_container_width=False):
        st.session_state.rtc_readout_offset_sec += 1
        st.rerun()

st.caption(f"**真實換碼偏移：** {switch_display} (自動校準)")
wake_col1, wake_col2 = st.columns(2, gap="xxsmall")
with wake_col1:
    if st.button("真實換碼 −1 分鐘", use_container_width=False):
        st.session_state.real_wakeup_offset_min -= 1
        st.rerun()
with wake_col2:
    if st.button("真實換碼 +1 分鐘", use_container_width=False):
        st.session_state.real_wakeup_offset_min += 1
        st.rerun()

st.divider()

with st.expander("⚙️ QR Code 與裝置時間參數設定", expanded=False):
    st.caption(f"**對角線翻轉 (Transpose):** {'開啟 (ON)' if st.session_state.qr_transpose else '關閉 (OFF)'}")
    t_col1, t_col2 = st.columns(2, gap="xxsmall")
    with t_col1:
        if st.button("正常方向", key="trans_off", type="secondary" if st.session_state.qr_transpose else "primary", use_container_width=False):
            st.session_state.qr_transpose = False
            st.rerun()
    with t_col2:
        if st.button("🔄 對角線翻轉", key="trans_on", type="primary" if st.session_state.qr_transpose else "secondary", use_container_width=False):
            st.session_state.qr_transpose = True
            st.rerun()

    st.caption(f"**Version (版本):** {'自動 (Auto)' if st.session_state.qr_version == 0 else f'Version {st.session_state.qr_version} (實際: V{qr.version})'}")
    v_col1, v_col2, v_col3 = st.columns(3, gap="xxsmall")
    with v_col1:
        if st.button("➖ Ver -1", use_container_width=False, disabled=(st.session_state.qr_version <= 0)):
            st.session_state.qr_version -= 1
            st.rerun()
    with v_col2:
        if st.button("Auto (V0)", use_container_width=False):
            st.session_state.qr_version = 0
            st.rerun()
    with v_col3:
        if st.button("➕ Ver +1", use_container_width=False, disabled=(st.session_state.qr_version >= 10)):
            st.session_state.qr_version += 1
            st.rerun()

    st.caption(f"**Error Correction (容錯率):** {st.session_state.qr_ec}")
    ec_cols = st.columns(4, gap="xxsmall")
    for idx, level in enumerate(["L", "M", "Q", "H"]):
        with ec_cols[idx]:
            btn_type = "primary" if st.session_state.qr_ec == level else "secondary"
            if st.button(f"{level}", key=f"ec_{level}", type=btn_type, use_container_width=False):
                st.session_state.qr_ec = level
                st.rerun()

    actual_mask = qr.mask_pattern if qr.mask_pattern is not None else qr.best_mask_pattern()
    st.caption(f"**Mask Pattern (遮罩樣式):** {'Auto' if st.session_state.qr_mask == -1 else st.session_state.qr_mask} (實際: M{actual_mask})")
    mask_row1 = st.columns(5, gap="xxsmall")
    with mask_row1[0]:
        btn_type = "primary" if st.session_state.qr_mask == -1 else "secondary"
        if st.button("Auto", key="mask_auto", type=btn_type, use_container_width=False):
            st.session_state.qr_mask = -1
            st.rerun()
    for m in range(4):
        with mask_row1[m + 1]:
            btn_type = "primary" if st.session_state.qr_mask == m else "secondary"
            if st.button(f"{m}", key=f"mask_{m}", type=btn_type, use_container_width=False):
                st.session_state.qr_mask = m
                st.rerun()

    mask_row2 = st.columns(4, gap="xxsmall")
    for m in range(4, 8):
        with mask_row2[m - 4]:
            btn_type = "primary" if st.session_state.qr_mask == m else "secondary"
            if st.button(f"{m}", key=f"mask_{m}", type=btn_type, use_container_width=False):
                st.session_state.qr_mask = m
                st.rerun()

    st.caption(f"**Module Size (方格像素):** {st.session_state.qr_box_size} px")
    m_col1, m_col2, m_col3 = st.columns(3, gap="xxsmall")
    with m_col1:
        if st.button("➖ Size -2", use_container_width=False, disabled=(st.session_state.qr_box_size <= 4)):
            st.session_state.qr_box_size -= 2
            st.rerun()
    with m_col2:
        if st.button("預設 (24px)", use_container_width=False):
            st.session_state.qr_box_size = 24
            st.rerun()
    with m_col3:
        if st.button("➕ Size +2", use_container_width=False, disabled=(st.session_state.qr_box_size >= 24)):
            st.session_state.qr_box_size += 2
            st.rerun()
