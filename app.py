import datetime
import io
import qrcode
import streamlit as st

st.set_page_config(page_title="Check-in QR", page_icon="📱", layout="centered")

st.markdown("""
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
.block-container {padding-top: 2rem; text-align: center;}
</style>
""", unsafe_allow_html=True)

# 初始化 Session State
if "hour_offset" not in st.session_state:
    st.session_state.hour_offset = 0
if "qr_version" not in st.session_state:
    st.session_state.qr_version = 1  # 0 代表 Auto (自動最小版本)
if "qr_ec" not in st.session_state:
    st.session_state.qr_ec = "L"
if "qr_box_size" not in st.session_state:
    st.session_state.qr_box_size = 10

EC_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}

hkt = datetime.timezone(datetime.timedelta(hours=8), name="HKT")
now_hkt = datetime.datetime.now(hkt)
effective_time = now_hkt + datetime.timedelta(minutes=5)
effective_hour = (effective_time.hour + st.session_state.hour_offset) % 24

computed_bytes = [
    0x2C,
    (41 + effective_hour) % 256,
    (42 + effective_hour) % 256,
    (6 + effective_hour) % 256,
    0x24,
    (79 + effective_hour) % 256,
]

hex_payload = "".join(f"{b:02X}" for b in computed_bytes)
qr_message = f"checkin://q?91/@{hex_payload}"

# 產生 QR Code（若指定版本容量不足該容錯率，自動升級至最小可用版本避免報錯）
target_version = None if st.session_state.qr_version == 0 else st.session_state.qr_version
try:
    qr = qrcode.QRCode(
        version=target_version,
        error_correction=EC_MAP[st.session_state.qr_ec],
        box_size=st.session_state.qr_box_size,
        border=2,
    )
    qr.add_data(qr_message)
    qr.make(fit=(target_version is None))
except qrcode.exceptions.DataOverflowError:
    qr = qrcode.QRCode(
        version=None,
        error_correction=EC_MAP[st.session_state.qr_ec],
        box_size=st.session_state.qr_box_size,
        border=2,
    )
    qr.add_data(qr_message)
    qr.make(fit=True)

img = qr.make_image(fill_color="black", back_color="white")

buf = io.BytesIO()
img.save(buf, format="PNG")

st.subheader(f"{now_hkt.strftime('%Y-%m-%d %H:%M:%S')} HKT")
offset_label = f" (微調: {st.session_state.hour_offset:+d})" if st.session_state.hour_offset != 0 else ""
ver_label = "Auto" if st.session_state.qr_version == 0 else f"V{qr.version}"
st.caption(f"有效時段：Hour {effective_hour}{offset_label} ｜ {ver_label} · EC:{st.session_state.qr_ec} · Size:{st.session_state.qr_box_size}")

col1, col2, col3 = st.columns([1, 4, 1])
with col2:
    st.image(buf.getvalue(), use_container_width=True)

st.code(qr_message, language=None)
st.markdown(f'<a href="{qr_message}" class="uri-link">🔗 {qr_message}</a>', unsafe_allow_html=True)
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

# QR Code 參數調整區塊
with st.expander("⚙️ QR Code 參數設定 (Version / 容錯率 / Module Size)", expanded=True):
    # 1. Version 調整
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

    # 2. Error Correction 容錯率調整
    st.caption(f"**Error Correction (容錯率):** {st.session_state.qr_ec}")
    ec_cols = st.columns(4)
    for idx, level in enumerate(["L", "M", "Q", "H"]):
        with ec_cols[idx]:
            btn_type = "primary" if st.session_state.qr_ec == level else "secondary"
            if st.button(f"{level}", key=f"ec_{level}", type=btn_type, use_container_width=True):
                st.session_state.qr_ec = level
                st.rerun()

    # 3. Module Size (box_size) 調整
    st.caption(f"**Module Size (方格像素):** {st.session_state.qr_box_size} px")
    m_col1, m_col2, m_col3 = st.columns(3)
    with m_col1:
        if st.button("➖ Size -2", use_container_width=True, disabled=(st.session_state.qr_box_size <= 4)):
            st.session_state.qr_box_size -= 2
            st.rerun()
    with m_col2:
        if st.button("預設 (10px)", use_container_width=True):
            st.session_state.qr_box_size = 10
            st.rerun()
    with m_col3:
        if st.button("➕ Size +2", use_container_width=True, disabled=(st.session_state.qr_box_size >= 24)):
            st.session_state.qr_box_size += 2
            st.rerun()
