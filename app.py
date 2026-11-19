import io
import os
import json
import time
import urllib.parse
from PIL import Image
import streamlit as st
import streamlit.components.v1 as components
from google import genai
from google.genai import types

st.set_page_config(page_title="SQOODS PRIME", page_icon="⚡", layout="centered")

# --- 1. SIDEBAR & SESSION MANAGEMENT ---
st.sidebar.title("⚡ SQOODS PRIME")
st.sidebar.caption("Developed by Anish Shaikh")

if "sessions" not in st.session_state:
    st.session_state.sessions = [{"title": "New Chat", "messages": []}]
if "current_session_idx" not in st.session_state:
    st.session_state.current_session_idx = 0
if "input_counter" not in st.session_state:
    st.session_state.input_counter = 0

if st.sidebar.button("➕ New Chat", use_container_width=True):
    st.session_state.sessions.append({"title": f"Chat {len(st.session_state.sessions) + 1}", "messages": []})
    st.session_state.current_session_idx = len(st.session_state.sessions) - 1
    st.rerun()

st.sidebar.divider()
st.sidebar.subheader("💬 Your Saved Chats")

for idx, session in enumerate(st.session_state.sessions):
    col_chat, col_del = st.sidebar.columns([4, 1])

    button_label = f"📌 {session['title']}" if idx == st.session_state.current_session_idx else session['title']
    with col_chat:
        if st.button(button_label, key=f"chat_btn_{idx}", use_container_width=True):
            st.session_state.current_session_idx = idx
            st.rerun()

    with col_del:
        if st.button("🗑️", key=f"del_btn_{idx}", help="Delete this chat", use_container_width=True):
            st.session_state.sessions.pop(idx)
            if len(st.session_state.sessions) == 0:
                st.session_state.sessions = [{"title": "New Chat", "messages": []}]
                st.session_state.current_session_idx = 0
            elif st.session_state.current_session_idx >= len(st.session_state.sessions):
                st.session_state.current_session_idx = len(st.session_state.sessions) - 1
            st.rerun()

# --- 2. API KEY SETUP ---
raw_key = None
try:
    if "GEMINI_API_KEY" in st.secrets:
        raw_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

if not raw_key:
    raw_key = os.environ.get("GEMINI_API_KEY", "")

cleaned_val = str(raw_key).replace("\n", "").replace("\r", "").replace(" ", "").strip("'").strip('"')
if "=" in cleaned_val:
    cleaned_val = cleaned_val.split("=")[-1]
api_key = cleaned_val.strip()

if not api_key:
    st.error("🚨 API Key not found! Please check your Streamlit Secrets.")
    st.stop()

client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
MODEL_CANDIDATES = [
    "gemini-2.5-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash-lite",
]

SYSTEM_PROMPT = """
You are SQOODS PRIME, an advanced, friendly, and highly intelligent AI assistant developed by Anish Shaikh.
- If asked your name, state you are SQOODS PRIME.
- If asked who created you, credit Anish Shaikh.
- Be warm, friendly, approachable, and give brilliant coding, academic, and factual assistance.
- You have native multimodal vision capabilities: when users attach photos of books, notes, diagrams, math problems, or questions, you directly inspect and understand the visual content (text, diagrams, equations, and layout) and explain things clearly step-by-step without giving robotic disclaimers about lacking eyes or needing external OCR.
"""


def prepare_image_part(img_item: dict) -> types.Part:
    """Convert stored image bytes into a Gemini Part object."""
    img_bytes = img_item["bytes"]
    mime_type = img_item.get("mime_type", "image/jpeg")
    try:
        pil_img = Image.open(io.BytesIO(img_bytes))
        fmt = (pil_img.format or "JPEG").upper()
        if fmt == "JPG":
            fmt = "JPEG"
        buf = io.BytesIO()
        pil_img.save(buf, format=fmt)
        return types.Part.from_bytes(data=buf.getvalue(), mime_type=f"image/{fmt.lower()}")
    except Exception:
        return types.Part.from_bytes(data=img_bytes, mime_type=mime_type)


def generate_with_fallback(contents_payload):
    """Retry on transient 503/high-demand errors and fall back across available models."""
    last_error = None
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=[{"google_search": {}}],
    )

    for model_name in MODEL_CANDIDATES:
        for attempt in range(2):
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=contents_payload,
                    config=config,
                )
            except Exception as err:
                last_error = err
                err_str = str(err)
                is_transient = any(
                    token in err_str
                    for token in ("503", "UNAVAILABLE", "high demand", "429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND")
                )
                if not is_transient:
                    raise
                if attempt == 0 and ("503" in err_str or "UNAVAILABLE" in err_str or "429" in err_str):
                    time.sleep(1.5)
                    continue
                break

    raise last_error


# --- 3. MAIN CHAT INTERFACE ---
st.title("⚡ SQOODS PRIME")
st.caption("Live Search Grounding • Multi-Photo Doubts • Multi-Chat • Voice • Copy & Share")

current_session = st.session_state.sessions[st.session_state.current_session_idx]
messages = current_session["messages"]

# Display prior chat history
for i, message in enumerate(messages):
    with st.chat_message(message["role"]):
        if message.get("images"):
            img_cols = st.columns(min(len(message["images"]), 3))
            for idx_img, img_data in enumerate(message["images"]):
                with img_cols[idx_img % len(img_cols)]:
                    st.image(img_data["bytes"], caption=img_data.get("name", "Photo"), use_container_width=True)

        if message.get("content"):
            st.markdown(message["content"])

        if message["role"] == "assistant":
            js_safe_text = json.dumps(message["content"])
            encoded_text = urllib.parse.quote(message["content"])
            wa_url = f"https://api.whatsapp.com/send?text={encoded_text}"
            gmail_url = f"mailto:?subject=SQOODS%20PRIME%20Response&body={encoded_text}"

            toolbar_html = f"""
            <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center; font-family:sans-serif; margin-top:4px;">
                <button id="copy-btn-{i}" onclick="copyText_{i}()"
                    style="background:#f0f2f6; color:#31333F; border:1px solid #d0d3d8; border-radius:6px; padding:4px 10px; cursor:pointer; font-size:12px;">
                    📋 Copy
                </button>
                <a href="{wa_url}" target="_blank"
                    style="text-decoration:none; background:#25D366; color:white; border-radius:6px; padding:4px 10px; font-size:12px; font-weight:500;">
                    💬 WhatsApp
                </a>
                <a href="{gmail_url}" target="_blank"
                    style="text-decoration:none; background:#EA4335; color:white; border-radius:6px; padding:4px 10px; font-size:12px; font-weight:500;">
                    ✉️ Gmail
                </a>
                <button onclick="speakText_{i}()"
                    style="background:#f0f2f6; color:#31333F; border:1px solid #d0d3d8; border-radius:6px; padding:4px 10px; cursor:pointer; font-size:12px;">
                    🔊 Listen
                </button>
                <button onclick="window.speechSynthesis.cancel();"
                    style="background:#f0f2f6; color:#31333F; border:1px solid #d0d3d8; border-radius:6px; padding:4px 10px; cursor:pointer; font-size:12px;">
                    ⏹️ Stop
                </button>
            </div>
            <script>
                const msgText_{i} = {js_safe_text};

                function copyText_{i}() {{
                    navigator.clipboard.writeText(msgText_{i}).then(() => {{
                        const btn = document.getElementById("copy-btn-{i}");
                        btn.innerText = "✅ Copied!";
                        setTimeout(() => {{ btn.innerText = "📋 Copy"; }}, 2000);
                    }});
                }}

                function speakText_{i}() {{
                    window.speechSynthesis.cancel();
                    const utterance = new SpeechSynthesisUtterance(msgText_{i});
                    window.speechSynthesis.speak(utterance);
                }}
            </script>
            """
            components.html(toolbar_html, height=42)

# Chat input with '+' multi-photo upload support
chat_payload = st.chat_input(
    "Ask SQOODS PRIME or tap '+' to attach book photos...",
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "webp"],
    key=f"chat_input_{st.session_state.input_counter}",
)

if chat_payload:
    raw_text = (chat_payload.text or "").strip()
    uploaded_files = list(chat_payload.files or [])

    stored_images = []
    for f in uploaded_files:
        stored_images.append({
            "name": f.name,
            "bytes": f.getvalue(),
            "mime_type": f.type or "image/jpeg",
        })

    display_text = raw_text if raw_text else "Please help me understand and solve the doubts in these photos."
    effective_prompt = (
        raw_text
        if raw_text
        else "Please read the attached book/study photos carefully and explain the concepts or solve the doubts step-by-step."
    )

    if current_session["title"].startswith("New Chat") and len(messages) == 0:
        title_source = raw_text if raw_text else f"Photo Doubt ({len(stored_images)})"
        current_session["title"] = title_source[:25] + ("..." if len(title_source) > 25 else "")

    messages.append({
        "role": "user",
        "content": display_text,
        "images": stored_images,
    })

    with st.chat_message("user"):
        if stored_images:
            img_cols = st.columns(min(len(stored_images), 3))
            for idx_img, img_data in enumerate(stored_images):
                with img_cols[idx_img % len(img_cols)]:
                    st.image(img_data["bytes"], caption=img_data["name"], use_container_width=True)
        st.markdown(display_text)

    with st.chat_message("assistant"):
        with st.spinner("SQOODS PRIME is thinking..."):
            try:
                gemini_contents = []
                for idx_m, msg in enumerate(messages):
                    role = "user" if msg["role"] == "user" else "model"
                    parts = []

                    if msg["role"] == "user" and idx_m == len(messages) - 1 and msg.get("images"):
                        for img_item in msg["images"]:
                            parts.append(prepare_image_part(img_item))
                        parts.append(types.Part.from_text(text=effective_prompt))
                    else:
                        parts.append(types.Part.from_text(text=msg["content"]))

                    gemini_contents.append(types.Content(role=role, parts=parts))

                response = generate_with_fallback(gemini_contents)
                bot_reply = response.text
                st.markdown(bot_reply)
                messages.append({"role": "assistant", "content": bot_reply})
                st.session_state.input_counter += 1
                st.rerun()
            except Exception as e:
                st.error(f"Error communicating with core: {e}")
