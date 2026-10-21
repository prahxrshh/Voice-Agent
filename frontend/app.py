import streamlit as st
import requests
from streamlit_mic_recorder import speech_to_text

BACKEND = "http://127.0.0.1:8000"
st.set_page_config(page_title="Voice Configuration Agent", page_icon="🎙️", layout="wide")
st.title("🎙️ Voice Configuration Agent")
st.caption("Prototype: Voice/Text → Intent → Approved Command → Safety → Dry Run → Confirmation → Simulator → Verification → Rollback → Audit")

if "pending" not in st.session_state: st.session_state.pending = None
if "last_result" not in st.session_state: st.session_state.last_result = None
if "request_text" not in st.session_state: st.session_state.request_text = ""

with st.sidebar:
    st.header("Demo Controls")
    st.write("These are simulator controls only.")
    fail = st.checkbox("Force verification failure", value=False)
    if st.button("Apply failure setting"):
        try:
            r = requests.post(f"{BACKEND}/failure-mode", json={"enabled": fail}, timeout=5)
            if r.ok: st.success(f"Failure mode: {r.json()['failure_mode']}")
            else: st.error("Could not update failure mode.")
        except Exception as e: st.error(f"Backend not reachable: {e}")
    st.divider()
    st.write("**Try these requests:**")
    st.code("Show BGP neighbours on CMG-02")
    st.code("Show interface status on ge-0/0/1 of CMG-02")
    st.code("Set MTU 1500 on ge-0/0/1 of CMG-02")
    st.code("Enable interface ge-0/0/2 on CMG-02")
    st.code("Disable interface ge-0/0/1 on CMG-02")

st.subheader("1. Give the configuration request")
typed = st.text_input("Type a request", value=st.session_state.request_text,
                       placeholder="Example: Set MTU 1500 on ge-0/0/1 of CMG-02")
st.session_state.request_text = typed
st.write("Or speak:")
spoken = speech_to_text(language="en", start_prompt="Network configuration request",
                        just_once=True, use_container_width=True, key="voice")
if spoken:
    st.session_state.request_text = spoken
    st.info(f"Voice converted to text: **{spoken}**")

if st.button("🚀 Send request", type="primary"):
    request_text = st.session_state.request_text.strip()
    if not request_text:
        st.warning("Please type or speak a request.")
    else:
        try:
            r = requests.post(f"{BACKEND}/request", json={"request": request_text}, timeout=10)
            data = r.json()
            st.session_state.last_result = data
            st.session_state.pending = data if data.get("requires_confirmation") else None
        except Exception as e:
            st.error(f"Backend not reachable. Start FastAPI first. Error: {e}")

result = st.session_state.last_result
if result:
    st.divider()
    st.subheader("2. Agent result")
    if not result.get("ok"):
        st.error(result.get("message", "Request failed."))
        if result.get("intent"): st.write(f"**Detected Intent:** `{result['intent']}`")
        if result.get("needs_information"):
            st.write("Missing information:")
            for item in result["needs_information"]: st.write(f"- {item}")
    else:
        st.write(f"**Intent:** `{result.get('intent', 'Unknown')}`")
        command = result.get("command")
        if command: st.write(f"**Approved command:** `{command.get('id', 'Unknown')}`")
        st.write(f"**Device:** `{result.get('device', 'Unknown')}`")
        if result.get("interface"): st.write(f"**Interface:** `{result['interface']}`")
        if "answer" in result:
            st.success("Read-only operation — no confirmation required.")
            st.json(result["answer"])
        if result.get("requires_confirmation"):
            st.warning("⚠️ Configuration change detected. Nothing has been applied yet.")
            st.subheader("🧪 Dry Run")
            st.code(result.get("dry_run", ""))
            st.write(f"Current value: **{result.get('old_value')}**")
            st.write(f"Requested value: **{result.get('new_value')}**")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ CONFIRM & APPLY", type="primary"):
                    try:
                        r = requests.post(f"{BACKEND}/confirm",
                                          json={"token": result["token"], "confirm": True}, timeout=10)
                        st.session_state.last_result = r.json()
                        st.session_state.pending = None
                        st.rerun()
                    except Exception as e: st.error(f"Confirmation failed: {e}")
            with c2:
                if st.button("❌ CANCEL"):
                    try:
                        r = requests.post(f"{BACKEND}/confirm",
                                          json={"token": result["token"], "confirm": False}, timeout=10)
                        st.session_state.last_result = r.json()
                        st.session_state.pending = None
                        st.rerun()
                    except Exception as e: st.error(f"Cancellation failed: {e}")
        if result.get("status") == "success":
            st.success(result.get("message", "Configuration applied successfully."))
            st.write(f"Expected: **{result.get('expected')}**")
            st.write(f"Actual: **{result.get('actual')}**")
        if result.get("status") == "rollback":
            st.error("❌ Verification failed")
            st.warning(result.get("message", "Configuration was rolled back."))
            st.write(f"Restored value: **{result.get('restored')}**")

st.divider()
left, right = st.columns(2)
with left:
    st.subheader("🖥️ Simulated device state")
    if st.button("Refresh device"): st.rerun()
    try:
        devices_response = requests.get(f"{BACKEND}/devices", timeout=5)
        st.json(devices_response.json())
    except Exception: st.info("Start the backend to see device state.")
with right:
    st.subheader("📜 Audit log")
    try:
        logs_response = requests.get(f"{BACKEND}/audit", timeout=5)
        st.dataframe(logs_response.json(), use_container_width=True)
    except Exception: st.info("Start the backend to see audit logs.")
