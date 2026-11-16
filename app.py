"""Streamlit app that generates a time-based check-in QR code."""

from __future__ import annotations

import datetime as dt
import io
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Optional

import qrcode
import streamlit as st
from PIL import Image

st.set_page_config(page_title="Check-in QR", page_icon="📱", layout="centered")

# Calibration and timing defaults.
BASE_MHZ = 16.0  # Retained as device metadata; not currently used in calculations.
DEFAULT_START_DATE = dt.date(2025, 8, 1)
DEFAULT_BASE_MINUTE = 10
DEFAULT_BASE_SECOND = 0.0
DEFAULT_DRIFT_PER_DAY = -0.45
FALLBACK_DRIFT_PER_DAY = -0.4489
TOTAL_SWITCH_SPAN_DAYS = 469.5
INITIAL_SWITCH_BOUNDARY_MINUTE = 10.0
INITIAL_QR_BOX_SIZE = 12
DEFAULT_QR_BOX_SIZE = 24

LOCAL_REF_PATH = os.path.join(os.path.dirname(__file__), "reference")
REF_URL = "https://raw.githubusercontent.com/jasonip-maker/checkin-qr/main/reference"
REFERENCE_PATTERN = re.compile(
    r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}).*?@([0-9A-Fa-f]{12})"
)

HKT = dt.timezone(dt.timedelta(hours=8), name="HKT")
EC_MAP = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}


@dataclass(frozen=True)
class CalibrationRecord:
    """One observation used to fit the device's RTC drift."""

    days: float
    seconds_in_hour: int


@dataclass(frozen=True)
class CalibrationResult:
    """Fitted time calibration, including fallback status for the UI."""

    base_minute: int
    base_second: float
    drift_per_day: float
    used_fallback: bool = False
    warning: Optional[str] = None


def decode_qr_payload_to_dt(hex_str: str) -> dt.datetime:
    """Decode the device's six-byte hexadecimal time payload."""
    if len(hex_str) != 12:
        raise ValueError(f"invalid QR payload length: {hex_str}")

    try:
        b0, b1, b2, b3, b4, b5 = (
            int(hex_str[index : index + 2], 16) for index in range(0, 12, 2)
        )
    except ValueError as exc:
        raise ValueError(f"invalid QR payload: {hex_str}") from exc

    # Inverse of the device's six-byte time encoding.
    second = (b5 - b0 - b1 + b4) % 256
    minute = (b4 - second) % 256
    hour = (b3 - minute) % 256
    month = (b1 - hour - second) % 256
    day = (b2 - hour - second) % 256
    year = (b0 - day - minute) % 256

    return dt.datetime(2000 + year, month, day, hour, minute, second)


def encode_dt_to_qr_payload(value: dt.datetime) -> str:
    """Encode a datetime using the device's six-byte QR payload format."""
    year = value.year % 100
    month, day = value.month, value.day
    hour, minute, second = value.hour, value.minute, value.second

    payload_bytes = (
        (year + day + minute) % 256,
        (month + hour + second) % 256,
        (day + hour + second) % 256,
        (hour + minute) % 256,
        (minute + second) % 256,
        (year + month + day + hour + second) % 256,
    )
    return "".join(f"{byte:02X}" for byte in payload_bytes)


def load_reference_records(
    url: str, start_date: dt.date
) -> tuple[list[CalibrationRecord], Optional[str]]:
    """Load and parse calibration observations from the local or remote reference."""
    try:
        if os.path.exists(LOCAL_REF_PATH):
            with open(LOCAL_REF_PATH, "r", encoding="utf-8") as reference_file:
                lines = reference_file.read().splitlines()
        else:
            with urllib.request.urlopen(url, timeout=5) as response:
                lines = response.read().decode("utf-8").splitlines()
    except Exception as exc:
        return [], f"Unable to load calibration reference: {exc}"

    start_dt = dt.datetime.combine(start_date, dt.time.min)
    records: list[CalibrationRecord] = []
    for line in lines:
        match = REFERENCE_PATTERN.search(line)
        if match is None:
            continue

        try:
            observed_dt = dt.datetime.strptime(match.group(1), "%Y/%m/%d %H:%M")
            decoded_dt = decode_qr_payload_to_dt(match.group(2))
        except (ValueError, OverflowError):
            continue

        elapsed_days = (observed_dt - start_dt).total_seconds() / 86400.0
        seconds_in_hour = decoded_dt.minute * 60 + decoded_dt.second
        records.append(CalibrationRecord(elapsed_days, seconds_in_hour))

    if not records:
        return [], "No valid calibration records were found."
    return records, None


def calibrate_from_reference(
    records: list[CalibrationRecord], load_warning: Optional[str] = None
) -> CalibrationResult:
    """Fit base time and RTC drift from the parsed reference observations."""
    if not records:
        return CalibrationResult(
            DEFAULT_BASE_MINUTE,
            DEFAULT_BASE_SECOND,
            DEFAULT_DRIFT_PER_DAY,
            used_fallback=True,
            warning=load_warning or "No valid calibration records were found.",
        )

    count = len(records)
    sum_x = sum(record.days for record in records)
    sum_y = sum(record.seconds_in_hour for record in records)
    sum_xy = sum(record.days * record.seconds_in_hour for record in records)
    sum_xx = sum(record.days**2 for record in records)
    denominator = count * sum_xx - sum_x**2

    if abs(denominator) > 1e-5:
        drift_per_day = (count * sum_xy - sum_x * sum_y) / denominator
        intercept = (sum_y - drift_per_day * sum_x) / count
    else:
        drift_per_day = FALLBACK_DRIFT_PER_DAY
        intercept = records[0].seconds_in_hour

    return CalibrationResult(
        base_minute=int(intercept // 60),
        base_second=intercept % 60,
        drift_per_day=drift_per_day,
    )


@st.cache_data
def calibrate_from_reference_cached(
    url: str, start_date: dt.date
) -> CalibrationResult:
    """Cached reference loading and calibration for Streamlit reruns."""
    records, warning = load_reference_records(url, start_date)
    return calibrate_from_reference(records, warning)


def compute_effective_time(
    adjusted_now: dt.datetime,
    start_dt: dt.datetime,
    drift_per_day: float,
    rtc_readout_offset_sec: int,
    real_wakeup_offset_min: int,
) -> tuple[dt.datetime, int, str]:
    """Determine the active time slot and its corrected device time."""
    initial_slot = adjusted_now.replace(minute=0, second=0, microsecond=0)
    elapsed_days = max(0.0, (initial_slot - start_dt).total_seconds() / 86400.0)

    boundary_rate = -15.0 / TOTAL_SWITCH_SPAN_DAYS
    exact_boundary = (
        INITIAL_SWITCH_BOUNDARY_MINUTE
        + elapsed_days * boundary_rate
        + real_wakeup_offset_min
    )
    rounded_boundary = round(exact_boundary)
    boundary_minute = rounded_boundary % 60
    switch_display = (
        f"(:{boundary_minute:02d})"
        if rounded_boundary < 0
        else f":{boundary_minute:02d}"
    )

    base_hour_slot = initial_slot
    boundary_dt = base_hour_slot + dt.timedelta(minutes=boundary_minute)
    if adjusted_now >= boundary_dt:
        slot_start = base_hour_slot + dt.timedelta(hours=1)
    else:
        slot_start = base_hour_slot

    if rounded_boundary >= 0:
        slot_start -= dt.timedelta(hours=1)

    elapsed_days = max(0.0, (slot_start - start_dt).total_seconds() / 86400.0)
    rtc_drift_seconds = round(elapsed_days * drift_per_day)
    total_rtc_offset_sec = rtc_drift_seconds + rtc_readout_offset_sec

    # timedelta normalizes offsets across minute/hour/day boundaries safely.
    effective_time = slot_start.replace(minute=0, second=0, microsecond=0) + dt.timedelta(
        seconds=DEFAULT_BASE_MINUTE * 60 + total_rtc_offset_sec
    )
    return effective_time, total_rtc_offset_sec, switch_display


def generate_qr_image(
    message: str,
    version: Optional[int],
    error_correction: str,
    box_size: int,
    mask_pattern: Optional[int],
) -> tuple[qrcode.QRCode, Image.Image]:
    """Build a QR code, falling back to automatic version on capacity overflow."""
    qr = qrcode.QRCode(
        version=version,
        error_correction=EC_MAP[error_correction],
        box_size=box_size,
        border=2,
        mask_pattern=mask_pattern,
    )
    qr.add_data(message)
    try:
        qr.make(fit=version is None)
    except qrcode.exceptions.DataOverflowError:
        qr = qrcode.QRCode(
            version=None,
            error_correction=EC_MAP[error_correction],
            box_size=box_size,
            border=2,
            mask_pattern=mask_pattern,
        )
        qr.add_data(message)
        qr.make(fit=True)

    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    return qr, image


def actual_mask_for(qr: qrcode.QRCode) -> int:
    """Return the selected mask, calculating the automatic choice if necessary."""
    return qr.mask_pattern if qr.mask_pattern is not None else qr.best_mask_pattern()


def initialize_session_state() -> None:
    """Populate the Streamlit session with defaults on first app visit."""
    defaults = {
        "hour_offset": 0,
        "start_date": DEFAULT_START_DATE,
        "rtc_readout_offset_sec": 0,
        "real_wakeup_offset_min": 0,
        "qr_version": 0,
        "qr_ec": "L",
        "qr_box_size": INITIAL_QR_BOX_SIZE,
        "qr_mask": -1,
        "qr_transpose": True,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def render_controls() -> None:
    """Render buttons that adjust timing and QR generation settings."""
    btn_col1, btn_col2, btn_col3 = st.columns(3, gap="xxsmall")
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
    st.caption(f"**RTC 讀出偏移：** {st.session_state.rtc_readout_offset_sec:+d} 秒 (自動校準)")
    rtc_col1, rtc_col2 = st.columns(2, gap="xxsmall")
    with rtc_col1:
        if st.button("RTC 讀出 −1 秒", use_container_width=True):
            st.session_state.rtc_readout_offset_sec -= 1
            st.rerun()
    with rtc_col2:
        if st.button("RTC 讀出 +1 秒", use_container_width=True):
            st.session_state.rtc_readout_offset_sec += 1
            st.rerun()

    st.caption(f"**真實換碼偏移：** {st.session_state.switch_display} (自動校準)")
    wake_col1, wake_col2 = st.columns(2, gap="xxsmall")
    with wake_col1:
        if st.button("真實換碼 −1 分鐘", use_container_width=True):
            st.session_state.real_wakeup_offset_min -= 1
            st.rerun()
    with wake_col2:
        if st.button("真實換碼 +1 分鐘", use_container_width=True):
            st.session_state.real_wakeup_offset_min += 1
            st.rerun()

    st.divider()
    with st.expander("⚙️ QR Code 與裝置時間參數設定", expanded=False):
        st.caption(
            f"**對角線翻轉 (Transpose):** "
            f"{'開啟 (ON)' if st.session_state.qr_transpose else '關閉 (OFF)'}"
        )
        transpose_col1, transpose_col2 = st.columns(2, gap="xxsmall")
        with transpose_col1:
            if st.button(
                "正常方向",
                key="trans_off",
                type="secondary" if st.session_state.qr_transpose else "primary",
                use_container_width=True,
            ):
                st.session_state.qr_transpose = False
                st.rerun()
        with transpose_col2:
            if st.button(
                "🔄 對角線翻轉",
                key="trans_on",
                type="primary" if st.session_state.qr_transpose else "secondary",
                use_container_width=True,
            ):
                st.session_state.qr_transpose = True
                st.rerun()

        version_text = (
            "自動 (Auto)"
            if st.session_state.qr_version == 0
            else f"Version {st.session_state.qr_version} (實際: V{st.session_state.qr_actual_version})"
        )
        st.caption(f"**Version (版本):** {version_text}")
        version_col1, version_col2, version_col3 = st.columns(3, gap="xxsmall")
        with version_col1:
            if st.button(
                "➖ Ver -1",
                use_container_width=True,
                disabled=st.session_state.qr_version <= 0,
            ):
                st.session_state.qr_version -= 1
                st.rerun()
        with version_col2:
            if st.button("Auto (V0)", use_container_width=True):
                st.session_state.qr_version = 0
                st.rerun()
        with version_col3:
            if st.button(
                "➕ Ver +1",
                use_container_width=True,
                disabled=st.session_state.qr_version >= 10,
            ):
                st.session_state.qr_version += 1
                st.rerun()

        st.caption(f"**Error Correction (容錯率):** {st.session_state.qr_ec}")
        ec_cols = st.columns(4, gap="xxsmall")
        for index, level in enumerate(("L", "M", "Q", "H")):
            with ec_cols[index]:
                button_type = "primary" if st.session_state.qr_ec == level else "secondary"
                if st.button(level, key=f"ec_{level}", type=button_type, use_container_width=True):
                    st.session_state.qr_ec = level
                    st.rerun()

        actual_mask = st.session_state.qr_actual_mask
        mask_setting = "Auto" if st.session_state.qr_mask == -1 else st.session_state.qr_mask
        st.caption(f"**Mask Pattern (遮罩樣式):** {mask_setting} (實際: M{actual_mask})")
        mask_row1 = st.columns(5, gap="xxsmall")
        with mask_row1[0]:
            button_type = "primary" if st.session_state.qr_mask == -1 else "secondary"
            if st.button("Auto", key="mask_auto", type=button_type, use_container_width=True):
                st.session_state.qr_mask = -1
                st.rerun()
        for mask in range(4):
            with mask_row1[mask + 1]:
                button_type = "primary" if st.session_state.qr_mask == mask else "secondary"
                if st.button(
                    str(mask), key=f"mask_{mask}", type=button_type, use_container_width=True
                ):
                    st.session_state.qr_mask = mask
                    st.rerun()

        mask_row2 = st.columns(4, gap="xxsmall")
        for mask in range(4, 8):
            with mask_row2[mask - 4]:
                button_type = "primary" if st.session_state.qr_mask == mask else "secondary"
                if st.button(
                    str(mask), key=f"mask_{mask}", type=button_type, use_container_width=True
                ):
                    st.session_state.qr_mask = mask
                    st.rerun()

        st.caption(f"**Module Size (方格像素):** {st.session_state.qr_box_size} px")
        size_col1, size_col2, size_col3 = st.columns(3, gap="xxsmall")
        with size_col1:
            if st.button(
                "➖ Size -2",
                use_container_width=True,
                disabled=st.session_state.qr_box_size <= 4,
            ):
                st.session_state.qr_box_size -= 2
                st.rerun()
        with size_col2:
            if st.button("預設 (24px)", use_container_width=True):
                st.session_state.qr_box_size = DEFAULT_QR_BOX_SIZE
                st.rerun()
        with size_col3:
            if st.button(
                "➕ Size +2",
                use_container_width=True,
                disabled=st.session_state.qr_box_size >= DEFAULT_QR_BOX_SIZE,
            ):
                st.session_state.qr_box_size += 2
                st.rerun()


def render_app() -> None:
    """Run the app: calibrate, compute the time, generate the QR, and render UI."""
    initialize_session_state()
    calibration = calibrate_from_reference_cached(REF_URL, st.session_state.start_date)
    if calibration.used_fallback:
        st.warning(f"使用預設 RTC 校準值。{calibration.warning or ''}")

    now_hkt = dt.datetime.now(HKT)
    adjusted_now = now_hkt + dt.timedelta(hours=st.session_state.hour_offset)
    start_dt = dt.datetime.combine(st.session_state.start_date, dt.time.min, tzinfo=HKT)
    effective_time, total_rtc_offset_sec, switch_display = compute_effective_time(
        adjusted_now=adjusted_now,
        start_dt=start_dt,
        drift_per_day=calibration.drift_per_day,
        rtc_readout_offset_sec=st.session_state.rtc_readout_offset_sec,
        real_wakeup_offset_min=st.session_state.real_wakeup_offset_min,
    )
    st.session_state.switch_display = switch_display

    hex_payload = encode_dt_to_qr_payload(effective_time)
    qr_message = f"checkin://q?91/@{hex_payload}"
    target_version = None if st.session_state.qr_version == 0 else st.session_state.qr_version
    if st.session_state.qr_mask == -1:
        target_mask = 1 if effective_time.hour == 15 else None
    else:
        target_mask = st.session_state.qr_mask

    qr, image = generate_qr_image(
        message=qr_message,
        version=target_version,
        error_correction=st.session_state.qr_ec,
        box_size=st.session_state.qr_box_size,
        mask_pattern=target_mask,
    )
    if st.session_state.qr_transpose:
        image = image.transpose(Image.Transpose.TRANSPOSE)

    image_buffer = io.BytesIO()
    image.save(image_buffer, format="PNG")
    decoded_time = decode_qr_payload_to_dt(hex_payload)
    actual_mask = actual_mask_for(qr)

    st.subheader(f"{now_hkt.strftime('%Y-%m-%d %H:%M:%S')} HKT")
    offset_label = (
        f" (微調: {st.session_state.hour_offset:+d})"
        if st.session_state.hour_offset != 0
        else ""
    )
    version_label = "Auto" if st.session_state.qr_version == 0 else f"V{qr.version}"
    mask_label = (
        f"Auto(M{actual_mask})" if st.session_state.qr_mask == -1 else f"M{actual_mask}"
    )
    transpose_label = " · 對角翻轉:ON" if st.session_state.qr_transpose else ""
    st.caption(
        f"有效日期時段：{effective_time.strftime('%Y/%m/%d')} Hour {effective_time.hour}"
        f"{offset_label} ｜ {version_label} · EC:{st.session_state.qr_ec}"
        f" · Mask:{mask_label} · Size:{st.session_state.qr_box_size}px{transpose_label}"
    )

    st.session_state.qr_actual_version = qr.version
    st.session_state.qr_actual_mask = actual_mask
    image_col1, image_col2, image_col3 = st.columns([1, 2, 1])
    with image_col2:
        st.image(image_buffer.getvalue(), use_container_width=False)

    st.markdown(
        f'<a href="{qr_message}" class="uri-link">🔗 {qr_message}</a>',
        unsafe_allow_html=True,
    )
    st.code(qr_message, language=None)
    st.caption(
        "伺服器逆向還原核對："
        f"{decoded_time.year:04d}-{decoded_time.month:02d}-{decoded_time.day:02d} "
        f"{decoded_time.hour:02d}:{decoded_time.minute:02d}:{decoded_time.second:02d}"
    )
    st.write("")

    render_controls()


render_app()
