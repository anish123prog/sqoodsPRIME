import io
import os
import json
import time
import random
import sqlite3
import hashlib
import urllib.parse
from PIL import Image
import streamlit as st
import streamlit.components.v1 as components
from google import genai
from google.genai import types
from google.genai.errors import APIError
from huggingface_hub import InferenceClient

st.set_page_config(page_title="SQOODS PRIME", page_icon="⚡", layout="centered")

MAX_IMAGE_SIDE = 1280
DB_PATH = "sqoods_prime.db"
HF_IMAGE_MODEL = "black-forest-labs/FLUX.1-schnell"

# --- CUSTOM MOBILE & BRAND POLISH ---
st.markdown(
    """
    <style>
        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 5rem;
            max-width: 780px;
        }
        .stChatMessage {
            border-radius: 14px;
            padding: 0.25rem 0.4rem;
        }
        @media (max-width: 640px) {
            .block-container {
                padding-top: 1rem;
                padding-left: 0.75rem;
                padding-right: 0.75rem;
            }
            h1 {
                font-size: 1.65rem !important;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# --- SECRET LOADER HELPER ---
def get_clean_secret(name: str) -> str:
    raw = None
    try:
        if name in st.secrets:
            raw = st.secrets[name]
    except Exception:
        pass
    if not raw:
        raw = os.environ.get(name, "")
    cleaned = str(raw).replace("\n", "").replace("\r", "").replace(" ", "").strip("'").strip('"')
    if "=" in cleaned:
        cleaned = cleaned.split("=")[-1]
    return cleaned.strip()


# --- DATABASE (SQLITE) HELPERS ---
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_chats (
            username TEXT PRIMARY KEY,
            sessions_json TEXT NOT NULL,
            current_idx INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


def hash_password(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()


def register_user(username: str, password: str) -> tuple[bool, str]:
    clean_user = username.strip().lower()
    if len(clean_user) < 3:
        return False, "Username must be at least 3 characters."
    if len(password) < 4:
        return False, "Password must be at least 4 characters."

    salt = os.urandom(16).hex()
    pw_hash = hash_password(password, salt)

    conn = get_conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, salt) VALUES (?, ?, ?)",
            (clean_user, pw_hash, salt),
        )
        default_sessions = [{"title": "New Chat", "messages": []}]
        cur.execute(
            "INSERT INTO user_chats (username, sessions_json, current_idx) VALUES (?, ?, 0)",
            (clean_user, json.dumps(default_sessions)),
        )
        conn.commit()
        return True, "Account created! You are signed in."
    except sqlite3.IntegrityError:
        return False, "That username is already taken."
    finally:
        conn.close()


def authenticate_user(username: str, password: str) -> tuple[bool, str]:
    clean_user = username.strip().lower()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT password_hash, salt FROM users WHERE username = ?", (clean_user,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return False, "User not found."

    if hash_password(password, row["salt"]) == row["password_hash"]:
        return True, "Signed in!"
    return False, "Incorrect password."


def save_user_chats(username: str, sessions: list, current_idx: int):
    if not username:
        return
    serializable_sessions = []
    for s in sessions:
        s_msgs = []
        for m in s.get("messages", []):
            imgs_clean = []
            for img in m.get("images", []):
                imgs_clean.append({
                    "name": img.get("name", "Photo"),
                    "mime_type": img.get("mime_type", "image/jpeg"),
                    "bytes_hex": img["bytes"].hex() if isinstance(img.get("bytes"), (bytes, bytearray)) else "",
                })
            s_msgs.append({
                "role": m["role"],
                "content": m.get("content", ""),
                "images": imgs_clean,
            })
        serializable_sessions.append({
            "title": s.get("title", "New Chat"),
            "messages": s_msgs,
        })

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO user_chats (username, sessions_json, current_idx)
        VALUES (?, ?, ?)
        ON CONFLICT(username) DO UPDATE SET
            sessions_json = excluded.sessions_json,
            current_idx = excluded.current_idx
        """,
        (username, json.dumps(serializable_sessions), current_idx),
    )
    conn.commit()
    conn.close()


def load_user_chats(username: str) -> tuple[list, int]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT sessions_json, current_idx FROM user_chats WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return [{"title": "New Chat", "messages": []}], 0

    raw_sessions = json.loads(row["sessions_json"])
    restored_sessions = []
    for s in raw_sessions:
        r_msgs = []
        for m in s.get("messages", []):
            r_imgs = []
            for img in m.get("images", []):
                hex_str = img.get("bytes_hex", "")
                r_imgs.append({
                    "name": img.get("name", "Photo"),
                    "mime_type": img.get("mime_type", "image/jpeg"),
                    "bytes": bytes.fromhex(hex_str) if hex_str else b"",
                })
            r_msgs.append({
                "role": m["role"],
                "content": m.get("content", ""),
                "images": r_imgs,
            })
        restored_sessions.append({
            "title": s.get("title", "New Chat"),
            "messages": r_msgs,
        })

    if not restored_sessions:
        restored_sessions = [{"title": "New Chat", "messages": []}]
    idx = min(max(0, int(row["current_idx"])), len(restored_sessions) - 1)
    return restored_sessions, idx


init_db()

# --- SESSION STATE INITIALIZATION ---
if "auth_user" not in st.session_state:
    st.session_state.auth_user = None
if "sessions" not in st.session_state:
    st.session_state.sessions = [{"title": "New Chat", "messages": []}]
if "current_session_idx" not in st.session_state:
    st.session_state.current_session_idx = 0
if "input_counter" not in st.session_state:
    st.session_state.input_counter = 0
if "queued_prompt" not in st.session_state:
    st.session_state.queued_prompt = None

# --- 1. SIDEBAR: ACCOUNT, MODES & SESSION MANAGEMENT ---
st.sidebar.title("⚡ SQOODS PRIME")
st.sidebar.caption("Developed by Anish Shaikh")

if not st.session_state.auth_user:
    st.sidebar.info("Sign in to save your chats to your account.")
    auth_tab_in, auth_tab_up = st.sidebar.tabs(["Sign In", "Sign Up"])

    with auth_tab_in:
        in_user = st.text_input("Username", key="login_user")
        in_pass = st.text_input("Password", type="password", key="login_pass")
        if st.button("🔐 Sign In", use_container_width=True):
            ok, msg = authenticate_user(in_user, in_pass)
            if ok:
                clean_u = in_user.strip().lower()
                st.session_state.auth_user = clean_u
                loaded_s, loaded_i = load_user_chats(clean_u)
                st.session_state.sessions = loaded_s
                st.session_state.current_session_idx = loaded_i
                st.rerun()
            else:
                st.sidebar.error(msg)

    with auth_tab_up:
        up_user = st.text_input("Choose Username", key="reg_user")
        up_pass = st.text_input("Choose Password (4+ chars)", type="password", key="reg_pass")
        if st.button("✨ Create Account", use_container_width=True):
            ok, msg = register_user(up_user, up_pass)
            if ok:
                clean_u = up_user.strip().lower()
                st.session_state.auth_user = clean_u
                st.session_state.sessions = [{"title": "New Chat", "messages": []}]
                st.session_state.current_session_idx = 0
                st.rerun()
            else:
                st.sidebar.error(msg)
else:
    st.sidebar.success(f"👤 Signed in as **{st.session_state.auth_user}**")
    if st.sidebar.button("🚪 Sign Out", use_container_width=True):
        save_user_chats(
            st.session_state.auth_user,
            st.session_state.sessions,
            st.session_state.current_session_idx,
        )
        st.session_state.auth_user = None
        st.session_state.sessions = [{"title": "New Chat", "messages": []}]
        st.session_state.current_session_idx = 0
        st.rerun()

st.sidebar.divider()

use_search = st.sidebar.toggle(
    "🔍 Live Google Search",
    value=True,
    help="Turn off during book doubts or coding to save API quota.",
)

image_gen_mode = st.sidebar.toggle(
    "🎨 Image Gen Mode (HF)",
    value=False,
    help="Generate AI images via Hugging Face, or start any prompt with /image.",
)

col_new, col_clear = st.sidebar.columns(2)
with col_new:
    if st.button("➕ New Chat", use_container_width=True):
        st.session_state.sessions.append({"title": f"Chat {len(st.session_state.sessions) + 1}", "messages": []})
        st.session_state.current_session_idx = len(st.session_state.sessions) - 1
        if st.session_state.auth_user:
            save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
        st.rerun()

with col_clear:
    if st.button("🧹 Clear All", use_container_width=True, help="Reset all chats"):
        st.session_state.sessions = [{"title": "New Chat", "messages": []}]
        st.session_state.current_session_idx = 0
        if st.session_state.auth_user:
            save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
        st.rerun()

st.sidebar.divider()
st.sidebar.subheader("💬 Your Saved Chats")

for idx, session in enumerate(st.session_state.sessions):
    col_chat, col_del = st.sidebar.columns([4, 1])

    button_label = f"📌 {session['title']}" if idx == st.session_state.current_session_idx else session["title"]
    with col_chat:
        if st.button(button_label, key=f"chat_btn_{idx}", use_container_width=True):
            st.session_state.current_session_idx = idx
            if st.session_state.auth_user:
                save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
            st.rerun()

    with col_del:
        if st.button("🗑️", key=f"del_btn_{idx}", help="Delete this chat", use_container_width=True):
            st.session_state.sessions.pop(idx)
            if len(st.session_state.sessions) == 0:
                st.session_state.sessions = [{"title": "New Chat", "messages": []}]
                st.session_state.current_session_idx = 0
            elif st.session_state.current_session_idx >= len(st.session_state.sessions):
                st.session_state.current_session_idx = len(st.session_state.sessions) - 1
            if st.session_state.auth_user:
                save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
            st.rerun()

# --- 2. API KEY SETUP ---
api_key = get_clean_secret("GEMINI_API_KEY")
hf_token = get_clean_secret("HF_TOKEN")

if not api_key:
    st.error("🚨 GEMINI_API_KEY not found! Please check your Streamlit Secrets.")
    st.stop()

client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
MODEL_CANDIDATES = ["gemini-2.5-flash", "gemini-3.5-flash-lite"]

SYSTEM_PROMPT = """
You are SQOODS PRIME, an advanced, friendly, and highly intelligent AI assistant developed by Anish Shaikh.
- If asked your name, state you are SQOODS PRIME.
- If asked who created you, credit Anish Shaikh.
- Be warm, friendly, approachable, and give brilliant coding, academic, and factual assistance.
- You have native multimodal vision capabilities: when users attach photos of books, notes, diagrams, math problems, or questions, you directly inspect and understand the visual content (text, diagrams, equations, and layout) and explain things clearly step-by-step without giving robotic disclaimers about lacking eyes or needing external OCR.
"""


def prepare_image_part(img_item: dict) -> types.Part:
    """Resize large images to save tokens and convert into a Gemini Part object."""
    img_bytes = img_item["bytes"]
    mime_type = img_item.get("mime_type", "image/jpeg")
    try:
        pil_img = Image.open(io.BytesIO(img_bytes))
        width, height = pil_img.size
        longest_side = max(width, height)
        if longest_side > MAX_IMAGE_SIDE:
            scale = MAX_IMAGE_SIDE / float(longest_side)
            new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
            pil_img = pil_img.resize(new_size, Image.Resampling.LANCZOS)

        fmt = (pil_img.format or "JPEG").upper()
        if fmt == "JPG":
            fmt = "JPEG"
        if fmt not in ("JPEG", "PNG", "WEBP"):
            fmt = "JPEG"
        if fmt == "JPEG" and pil_img.mode not in ("RGB", "L"):
            pil_img = pil_img.convert("RGB")

        buf = io.BytesIO()
        pil_img.save(buf, format=fmt, quality=85 if fmt == "JPEG" else None)
        return types.Part.from_bytes(data=buf.getvalue(), mime_type=f"image/{fmt.lower()}")
    except Exception:
        return types.Part.from_bytes(data=img_bytes, mime_type=mime_type)


def generate_hf_image(prompt: str, token: str) -> bytes:
    """Generate an image using Hugging Face InferenceClient and return PNG bytes."""
    hf_client = InferenceClient(provider="hf-inference", api_key=token)
    pil_img = hf_client.text_to_image(prompt, model=HF_IMAGE_MODEL)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    return buf.getvalue()


def generate_with_resilience(contents_payload, enable_search: bool = True, max_retries: int = 3):
    """Retry 429 rate limits with backoff+jitter and fall back across models on 503 spikes."""
    base_delay = 2
    tools = [{"google_search": {}}] if enable_search else None
    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        tools=tools,
    )

    last_error = None
    for model_name in MODEL_CANDIDATES:
        for attempt in range(max_retries):
            try:
                return client.models.generate_content(
                    model=model_name,
                    contents=contents_payload,
                    config=config,
                )
            except APIError as e:
                last_error = e
                if e.code == 429:
                    if attempt == max_retries - 1:
                        raise
                    wait_time = (base_delay * (2 ** attempt)) + random.uniform(0, 1)
                    time.sleep(wait_time)
                elif e.code == 503:
                    if attempt < max_retries - 1:
                        time.sleep(1.2 * (attempt + 1))
                        continue
                    break
                else:
                    raise
            except Exception as err:
                last_error = err
                err_str = str(err)
                if "503" in err_str or "UNAVAILABLE" in err_str:
                    if attempt < max_retries - 1:
                        time.sleep(1.2 * (attempt + 1))
                        continue
                    break
                raise

    raise last_error


def build_notes_markdown(session_data: dict) -> str:
    """Format active chat history as downloadable Markdown notes."""
    lines = [f"# {session_data['title']} — SQOODS PRIME Notes\n"]
    for msg in session_data["messages"]:
        who = "You" if msg["role"] == "user" else "SQOODS PRIME"
        lines.append(f"### {who}\n{msg.get('content', '')}\n")
    return "\n".join(lines)


# --- 3. MAIN CHAT INTERFACE ---
title_col, dl_col = st.columns([5, 1])
with title_col:
    st.title("⚡ SQOODS PRIME")
    st.caption("Live Search • Multi-Photo Doubts • HF Image Gen • Account Saved Chats • Voice")

current_session = st.session_state.sessions[st.session_state.current_session_idx]
messages = current_session["messages"]

with dl_col:
    st.write("")
    if messages:
        st.download_button(
            label="📥",
            data=build_notes_markdown(current_session),
            file_name="sqoods_prime_notes.md",
            mime="text/markdown",
            help="Download chat notes (.md)",
            use_container_width=True,
        )

# Display prior chat history
for i, message in enumerate(messages):
    with st.chat_message(message["role"]):
        if message.get("images"):
            valid_imgs = [img for img in message["images"] if img.get("bytes")]
            if valid_imgs:
                img_cols = st.columns(min(len(valid_imgs), 3))
                for idx_img, img_data in enumerate(valid_imgs):
                    with img_cols[idx_img % len(img_cols)]:
                        st.image(img_data["bytes"], caption=img_data.get("name", "Photo"), use_container_width=True)
                        if message["role"] == "assistant":
                            st.download_button(
                                label="⬇️ Download Image",
                                data=img_data["bytes"],
                                file_name=img_data.get("name", f"sqoods_image_{i}.png"),
                                mime=img_data.get("mime_type", "image/png"),
                                key=f"dl_gen_img_{i}_{idx_img}",
                            )

        if message.get("content"):
            st.markdown(message["content"])

        if message["role"] == "assistant":
            js_safe_text = json.dumps(message["content"])
            encoded_text = urllib.parse.quote(message["content"])
            wa_url = f"https://api.whatsapp.com/send?text={encoded_text}"
            gmail_url = f"mailto:?subject=SQOODS%20PRIME%20Response&body={encoded_text}"

            toolbar_html = f"""
            <div style="display:flex; flex-wrap:wrap; gap:6px; align-items:center; font-family:sans-serif; margin-top:2px;">
                <button id="copy-btn-{i}" onclick="copyText_{i}()" title="Copy"
                    style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; background:#f0f2f6; color:#31333F; border:1px solid #d0d3d8; border-radius:8px; cursor:pointer; font-size:15px; padding:0;">
                    📋
                </button>
                <button onclick="toggleShare_{i}()" title="Share"
                    style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; background:#f0f2f6; color:#31333F; border:1px solid #d0d3d8; border-radius:8px; cursor:pointer; font-size:15px; padding:0;">
                    📤
                </button>
                <div id="share-menu-{i}" style="display:none; gap:6px; align-items:center;">
                    <a href="{wa_url}" target="_blank" onclick="closeShare_{i}()" title="WhatsApp"
                        style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; background:#25D366; color:white; border-radius:8px; font-size:15px;">
                        💬
                    </a>
                    <a href="{gmail_url}" target="_blank" onclick="closeShare_{i}()" title="Gmail"
                        style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; background:#EA4335; color:white; border-radius:8px; font-size:15px;">
                        ✉️
                    </a>
                </div>
                <button onclick="speakText_{i}()" title="Listen"
                    style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; background:#f0f2f6; color:#31333F; border:1px solid #d0d3d8; border-radius:8px; cursor:pointer; font-size:15px; padding:0;">
                    🔊
                </button>
                <button onclick="window.speechSynthesis.cancel();" title="Stop"
                    style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; background:#f0f2f6; color:#31333F; border:1px solid #d0d3d8; border-radius:8px; cursor:pointer; font-size:15px; padding:0;">
                    ⏹️
                </button>
            </div>
            <script>
                const msgText_{i} = {js_safe_text};

                function copyText_{i}() {{
                    navigator.clipboard.writeText(msgText_{i}).then(() => {{
                        const btn = document.getElementById("copy-btn-{i}");
                        btn.innerText = "✅";
                        setTimeout(() => {{ btn.innerText = "📋"; }}, 2000);
                    }});
                }}

                function toggleShare_{i}() {{
                    const menu = document.getElementById("share-menu-{i}");
                    menu.style.display = (menu.style.display === "none" || !menu.style.display) ? "inline-flex" : "none";
                }}

                function closeShare_{i}() {{
                    const menu = document.getElementById("share-menu-{i}");
                    if (menu) menu.style.display = "none";
                }}

                function speakText_{i}() {{
                    window.speechSynthesis.cancel();
                    const utterance = new SpeechSynthesisUtterance(msgText_{i});
                    window.speechSynthesis.speak(utterance);
                }}
            </script>
            """
            components.html(toolbar_html, height=46)

# Quick study preset row
p1, p2, p3 = st.columns(3)
has_context = len(messages) > 0
with p1:
    if st.button("✨ Simple", use_container_width=True, help="Explain simply"):
        st.session_state.queued_prompt = (
            "Explain the topic or answer above in super simple, student-friendly language with a clear example."
            if has_context
            else "Teach me a core science or math concept in simple, student-friendly language with an example."
        )
        st.rerun()
with p2:
    if st.button("📝 Key Points", use_container_width=True, help="Exam summary"):
        st.session_state.queued_prompt = (
            "Summarize the key points, definitions, and exam takeaways from our discussion in crisp bullet points."
            if has_context
            else "Give me concise exam revision bullet points on a key Class 9 science or physics topic."
        )
        st.rerun()
with p3:
    if st.button("❓ Quiz Me", use_container_width=True, help="Practice questions"):
        st.session_state.queued_prompt = (
            "Give me 3 quick practice questions based on what we just covered, without spoiling the answers right away."
            if has_context
            else "Give me 3 practice multiple-choice questions on physics numericals or sound waves to test myself."
        )
        st.rerun()

# Chat input with '+' multi-photo upload support
placeholder_txt = (
    "Describe the image you want SQOODS PRIME to generate..."
    if image_gen_mode
    else "Ask SQOODS PRIME, type '/image ...', or tap '+' for book photos..."
)
chat_payload = st.chat_input(
    placeholder_txt,
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "webp"],
    key=f"chat_input_{st.session_state.input_counter}",
)

queued = st.session_state.queued_prompt
if queued and not chat_payload:
    st.session_state.queued_prompt = None
    raw_text = queued.strip()
    uploaded_files = []
elif chat_payload:
    st.session_state.queued_prompt = None
    raw_text = (chat_payload.text or "").strip()
    uploaded_files = list(chat_payload.files or [])
else:
    raw_text = ""
    uploaded_files = []

if raw_text or uploaded_files:
    stored_images = []
    for f in uploaded_files:
        stored_images.append({
            "name": f.name,
            "bytes": f.getvalue(),
            "mime_type": f.type or "image/jpeg",
        })

    is_image_request = (
        not stored_images
        and (image_gen_mode or raw_text.lower().startswith("/image "))
    )
    clean_img_prompt = (
        raw_text[7:].strip() if raw_text.lower().startswith("/image ") else raw_text
    )

    display_text = raw_text if raw_text else "Please help me understand and solve the doubts in these photos."
    effective_prompt = (
        raw_text
        if raw_text
        else "Please read the attached book/study photos carefully and explain the concepts or solve the doubts step-by-step."
    )

    if current_session["title"].startswith("New Chat") and len(messages) == 0:
        title_source = clean_img_prompt if is_image_request else (raw_text if raw_text else f"Photo Doubt ({len(stored_images)})")
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
        if is_image_request:
            if not hf_token:
                st.warning("🎨 **HF_TOKEN Missing:** Add `HF_TOKEN` in your Streamlit Secrets to enable image generation.")
            elif not clean_img_prompt:
                st.warning("Please enter an image prompt after `/image`.")
            else:
                with st.spinner("🎨 Generating image with Hugging Face..."):
                    try:
                        gen_bytes = generate_hf_image(clean_img_prompt, hf_token)
                        gen_item = {
                            "name": "sqoods_generated.png",
                            "bytes": gen_bytes,
                            "mime_type": "image/png",
                        }
                        caption_reply = f"Generated image for: **{clean_img_prompt}**"
                        st.image(gen_bytes, caption=clean_img_prompt, use_container_width=True)
                        st.markdown(caption_reply)
                        messages.append({
                            "role": "assistant",
                            "content": caption_reply,
                            "images": [gen_item],
                        })
                        if st.session_state.auth_user:
                            save_user_chats(
                                st.session_state.auth_user,
                                st.session_state.sessions,
                                st.session_state.current_session_idx,
                            )
                        st.session_state.input_counter += 1
                        st.rerun()
                    except Exception as e:
                        st.error(f"Image generation error: {e}")
        else:
            with st.spinner("SQOODS PRIME is thinking..."):
                try:
                    recent_messages = messages[-12:]
                    base_offset = len(messages) - len(recent_messages)
                    gemini_contents = []

                    for rel_idx, msg in enumerate(recent_messages):
                        abs_idx = base_offset + rel_idx
                        role = "user" if msg["role"] == "user" else "model"
                        parts = []

                        if msg["role"] == "user" and abs_idx == len(messages) - 1 and msg.get("images"):
                            for img_item in msg["images"]:
                                if img_item.get("bytes"):
                                    parts.append(prepare_image_part(img_item))
                            parts.append(types.Part.from_text(text=effective_prompt))
                        else:
                            parts.append(types.Part.from_text(text=msg["content"]))

                        gemini_contents.append(types.Content(role=role, parts=parts))

                    response = generate_with_resilience(gemini_contents, enable_search=use_search)
                    bot_reply = response.text
                    st.markdown(bot_reply)
                    messages.append({"role": "assistant", "content": bot_reply})
                    if st.session_state.auth_user:
                        save_user_chats(
                            st.session_state.auth_user,
                            st.session_state.sessions,
                            st.session_state.current_session_idx,
                        )
                    st.session_state.input_counter += 1
                    st.rerun()
                except APIError as e:
                    if e.code == 429:
                        st.warning(
                            "⏳ **Quota / Rate Limit Reached:** Your Gemini API key hit its request or token limit. "
                            "Please wait a minute and try again, or check your quota in Google AI Studio."
                        )
                    else:
                        st.error(f"Error communicating with core: {e}")
                except Exception as e:
                    err_text = str(e)
                    if "429" in err_text or "RESOURCE_EXHAUSTED" in err_text:
                        st.warning(
                            "⏳ **Quota / Rate Limit Reached:** Your Gemini API key hit its request or token limit. "
                            "Please wait a minute and try again, or check your quota in Google AI Studio."
                        )
                    else:
                        st.error(f"Error communicating with core: {e}")
