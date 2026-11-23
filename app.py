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

st.set_page_config(page_title="SQOODS PRIME", page_icon=":material/bolt:", layout="centered")

MAX_IMAGE_SIDE = 1280
DB_PATH = "sqoods_prime.db"

# --- SPACIOUS EXECUTIVE UI POLISH ---
st.markdown(
    """
    <style>
        .block-container {
            padding-top: 2rem;
            padding-bottom: 5.5rem;
            max-width: 760px;
        }
        [data-testid="stSidebar"] .block-container {
            padding-top: 1.5rem;
            padding-bottom: 2rem;
        }
        .stChatMessage {
            border-radius: 14px;
            padding: 0.45rem 0.65rem;
            margin-bottom: 0.5rem;
        }
        div[data-testid="stVerticalBlock"] > div:has(> div[data-testid="stHorizontalBlock"]) {
            margin-bottom: 0.25rem;
        }
        @media (max-width: 640px) {
            .block-container {
                padding-top: 1.25rem;
                padding-left: 1rem;
                padding-right: 1rem;
            }
            h1 {
                font-size: 1.55rem !important;
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
        default_sessions = [{"title": "Chat 1", "pinned": False, "messages": []}]
        cur.execute(
            "INSERT INTO user_chats (username, sessions_json, current_idx) VALUES (?, ?, 0)",
            (clean_user, json.dumps(default_sessions)),
        )
        conn.commit()
        return True, "Account created."
    except sqlite3.IntegrityError:
        return False, "Username already exists."
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
        return False, "Account not found."

    if hash_password(password, row["salt"]) == row["password_hash"]:
        return True, "Authenticated."
    return False, "Invalid password."


def serialize_sessions(sessions: list) -> list:
    serializable = []
    for s in sessions:
        s_msgs = []
        for m in s.get("messages", []):
            imgs_clean = []
            for img in m.get("images", []):
                imgs_clean.append({
                    "name": img.get("name", "Image"),
                    "mime_type": img.get("mime_type", "image/jpeg"),
                    "bytes_hex": img["bytes"].hex() if isinstance(img.get("bytes"), (bytes, bytearray)) else "",
                })
            s_msgs.append({
                "role": m["role"],
                "content": m.get("content", ""),
                "images": imgs_clean,
            })
        serializable.append({
            "title": s.get("title", "Chat"),
            "pinned": bool(s.get("pinned", False)),
            "messages": s_msgs,
        })
    return serializable


def deserialize_sessions(raw_sessions: list) -> list:
    restored = []
    for s in raw_sessions:
        r_msgs = []
        for m in s.get("messages", []):
            r_imgs = []
            for img in m.get("images", []):
                hex_str = img.get("bytes_hex", "")
                r_imgs.append({
                    "name": img.get("name", "Image"),
                    "mime_type": img.get("mime_type", "image/jpeg"),
                    "bytes": bytes.fromhex(hex_str) if hex_str else b"",
                })
            r_msgs.append({
                "role": m["role"],
                "content": m.get("content", ""),
                "images": r_imgs,
            })
        restored.append({
            "title": s.get("title", "Chat"),
            "pinned": bool(s.get("pinned", False)),
            "messages": r_msgs,
        })
    return restored if restored else [{"title": "Chat 1", "pinned": False, "messages": []}]


def save_user_chats(username: str, sessions: list, current_idx: int):
    if not username:
        return
    serializable_sessions = serialize_sessions(sessions)
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
        return [{"title": "Chat 1", "pinned": False, "messages": []}], 0

    restored_sessions = deserialize_sessions(json.loads(row["sessions_json"]))
    idx = min(max(0, int(row["current_idx"])), len(restored_sessions) - 1)
    return restored_sessions, idx


def sort_sessions_keep_active(sessions: list, active_idx: int) -> tuple[list, int]:
    """Keep pinned chats at the top while preserving the active session index."""
    active_obj = sessions[active_idx]
    pinned = [s for s in sessions if s.get("pinned")]
    unpinned = [s for s in sessions if not s.get("pinned")]
    ordered = pinned + unpinned
    new_idx = ordered.index(active_obj)
    return ordered, new_idx


init_db()

# --- SESSION STATE INITIALIZATION ---
if "auth_user" not in st.session_state:
    st.session_state.auth_user = None
if "sessions" not in st.session_state:
    st.session_state.sessions = [{"title": "Chat 1", "pinned": False, "messages": []}]
if "current_session_idx" not in st.session_state:
    st.session_state.current_session_idx = 0
if "input_counter" not in st.session_state:
    st.session_state.input_counter = 0
if "renaming_idx" not in st.session_state:
    st.session_state.renaming_idx = None
if "show_restore" not in st.session_state:
    st.session_state.show_restore = False

# --- DEV QUERY-PARAM BYPASS (?dev=1) ---
if not st.session_state.auth_user and st.query_params.get("dev") == "1":
    st.session_state.auth_user = "dev_tester"
    loaded_s, loaded_i = load_user_chats("dev_tester")
    st.session_state.sessions = loaded_s
    st.session_state.current_session_idx = loaded_i
    st.rerun()

# --- MANDATORY STARTUP ACCOUNT GATE ---
if not st.session_state.auth_user:
    st.markdown(
        "<style>[data-testid='stSidebar'], [data-testid='stSidebarCollapseButton'] {display: none !important;}</style>",
        unsafe_allow_html=True,
    )
    st.title(":material/bolt: SQOODS PRIME")
    st.caption("Anish Shaikh")
    st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

    tab_in, tab_up = st.tabs([":material/login: Sign In", ":material/person_add: Register"])

    with tab_in:
        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
        with st.form("login_form", border=False):
            in_user = st.text_input("Username", key="start_login_user")
            in_pass = st.text_input("Password", type="password", key="start_login_pass")
            st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
            submitted_in = st.form_submit_button(":material/login:", use_container_width=True, type="primary")
        if submitted_in:
            ok, msg = authenticate_user(in_user, in_pass)
            if ok:
                clean_u = in_user.strip().lower()
                st.session_state.auth_user = clean_u
                loaded_s, loaded_i = load_user_chats(clean_u)
                st.session_state.sessions = loaded_s
                st.session_state.current_session_idx = loaded_i
                st.rerun()
            else:
                st.error(msg)

    with tab_up:
        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
        with st.form("register_form", border=False):
            up_user = st.text_input("Username", key="start_reg_user")
            up_pass = st.text_input("Password", type="password", key="start_reg_pass")
            st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
            submitted_up = st.form_submit_button(":material/person_add:", use_container_width=True, type="primary")
        if submitted_up:
            ok, msg = register_user(up_user, up_pass)
            if ok:
                clean_u = up_user.strip().lower()
                st.session_state.auth_user = clean_u
                st.session_state.sessions = [{"title": "Chat 1", "pinned": False, "messages": []}]
                st.session_state.current_session_idx = 0
                st.rerun()
            else:
                st.error(msg)

    st.stop()

# --- 1. SIDEBAR (UNCLUTTERED CONTROLS + SAVED CHATS AT BOTTOM) ---
st.sidebar.title(":material/bolt: SQOODS PRIME")
st.sidebar.caption(f":material/account_circle: {st.session_state.auth_user}")
st.sidebar.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)

top_s1, top_s2 = st.sidebar.columns(2, gap="small")
with top_s1:
    if st.button(":material/add:", use_container_width=True, help="New Chat"):
        st.session_state.sessions.append({"title": f"Chat {len(st.session_state.sessions) + 1}", "pinned": False, "messages": []})
        st.session_state.current_session_idx = len(st.session_state.sessions) - 1
        st.session_state.sessions, st.session_state.current_session_idx = sort_sessions_keep_active(
            st.session_state.sessions, st.session_state.current_session_idx
        )
        save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
        st.rerun()

with top_s2:
    if st.button(":material/logout:", use_container_width=True, help="Sign Out"):
        save_user_chats(
            st.session_state.auth_user,
            st.session_state.sessions,
            st.session_state.current_session_idx,
        )
        st.session_state.auth_user = None
        st.session_state.sessions = [{"title": "Chat 1", "pinned": False, "messages": []}]
        st.session_state.current_session_idx = 0
        st.session_state.renaming_idx = None
        st.session_state.show_restore = False
        st.rerun()

st.sidebar.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
util_s1, util_s2, util_s3 = st.sidebar.columns(3, gap="small")
with util_s1:
    backup_payload = json.dumps({
        "user": st.session_state.auth_user,
        "sessions": serialize_sessions(st.session_state.sessions),
    })
    st.download_button(
        label=":material/cloud_download:",
        data=backup_payload,
        file_name=f"sqoods_{st.session_state.auth_user}_backup.json",
        mime="application/json",
        help="Backup Chats",
        use_container_width=True,
    )

with util_s2:
    if st.button(":material/cloud_upload:", use_container_width=True, help="Restore Backup"):
        st.session_state.show_restore = not st.session_state.show_restore
        st.rerun()

with util_s3:
    if st.button(":material/delete_sweep:", use_container_width=True, help="Clear All Chats"):
        st.session_state.sessions = [{"title": "Chat 1", "pinned": False, "messages": []}]
        st.session_state.current_session_idx = 0
        st.session_state.renaming_idx = None
        save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
        st.rerun()

if st.session_state.show_restore:
    st.sidebar.markdown("<div style='height: 6px;'></div>", unsafe_allow_html=True)
    backup_file = st.sidebar.file_uploader("JSON", type=["json"], label_visibility="collapsed", key="restore_uploader")
    if backup_file is not None:
        try:
            parsed = json.loads(backup_file.getvalue().decode("utf-8"))
            restored = deserialize_sessions(parsed.get("sessions", []))
            st.session_state.sessions = restored
            st.session_state.current_session_idx = 0
            st.session_state.show_restore = False
            save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
            st.rerun()
        except Exception:
            st.sidebar.error("Invalid backup file.")

st.sidebar.divider()

for idx, session in enumerate(st.session_state.sessions):
    is_active = idx == st.session_state.current_session_idx
    is_pinned = bool(session.get("pinned", False))

    if st.session_state.renaming_idx == idx:
        r_col1, r_col2 = st.sidebar.columns([4.5, 1], gap="small")
        with r_col1:
            new_title = st.text_input(
                "Title",
                value=session["title"],
                key=f"rename_input_{idx}",
                label_visibility="collapsed",
            )
        with r_col2:
            if st.button(":material/check:", key=f"save_rename_{idx}", help="Save", use_container_width=True):
                clean_title = new_title.strip()
                if clean_title:
                    session["title"] = clean_title[:40]
                    save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
                st.session_state.renaming_idx = None
                st.rerun()
    else:
        c_main, c_menu = st.sidebar.columns([5.2, 1], gap="small")
        lead_icon = ":material/keep:" if is_pinned else (":material/chat:" if is_active else ":material/chat_bubble_outline:")
        with c_main:
            if st.button(f"{lead_icon} {session['title']}", key=f"chat_btn_{idx}", use_container_width=True):
                st.session_state.current_session_idx = idx
                save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
                st.rerun()

        with c_menu:
            with st.popover(":material/more_horiz:", use_container_width=True):
                if st.button(
                    ":material/keep_off: Unpin" if is_pinned else ":material/keep: Pin",
                    key=f"pop_pin_{idx}",
                    use_container_width=True,
                ):
                    session["pinned"] = not is_pinned
                    st.session_state.sessions, st.session_state.current_session_idx = sort_sessions_keep_active(
                        st.session_state.sessions, st.session_state.current_session_idx
                    )
                    save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
                    st.rerun()

                if st.button(":material/edit: Rename", key=f"pop_edit_{idx}", use_container_width=True):
                    st.session_state.renaming_idx = idx
                    st.rerun()

                if st.button(":material/delete: Delete", key=f"pop_del_{idx}", use_container_width=True):
                    st.session_state.sessions.pop(idx)
                    if len(st.session_state.sessions) == 0:
                        st.session_state.sessions = [{"title": "Chat 1", "pinned": False, "messages": []}]
                        st.session_state.current_session_idx = 0
                    elif st.session_state.current_session_idx >= len(st.session_state.sessions):
                        st.session_state.current_session_idx = len(st.session_state.sessions) - 1
                    save_user_chats(st.session_state.auth_user, st.session_state.sessions, st.session_state.current_session_idx)
                    st.rerun()

# --- 2. API KEY SETUP ---
api_key = get_clean_secret("GEMINI_API_KEY")

if not api_key:
    st.error("GEMINI_API_KEY missing in Streamlit Secrets.")
    st.stop()

client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
MODEL_CANDIDATES = ["gemini-2.5-flash", "gemini-3.5-flash-lite"]

SYSTEM_PROMPT = """
You are SQOODS PRIME, an intelligent AI assistant developed by Anish Shaikh.
- If asked your name, state you are SQOODS PRIME.
- If asked who created you, credit Anish Shaikh.
- Give clear, accurate, and well-structured technical, academic, and general answers.
- When users attach images, inspect them directly and explain clearly without disclaimers about lacking eyes or needing OCR.
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


def build_token_efficient_contents(messages: list, effective_prompt: str) -> list:
    """Compress older messages into a compact digest and keep recent turns full-fidelity."""
    recent_window = 8
    older_messages = messages[:-recent_window] if len(messages) > recent_window else []
    recent_messages = messages[-recent_window:]
    base_offset = len(messages) - len(recent_messages)

    gemini_contents = []

    if older_messages:
        digest_lines = []
        for m in older_messages[-12:]:
            snippet = (m.get("content") or "").replace("\n", " ").strip()
            if snippet:
                role_tag = "User" if m["role"] == "user" else "Assistant"
                digest_lines.append(f"- {role_tag}: {snippet[:140]}")
        if digest_lines:
            digest_text = "Earlier conversation context summary:\n" + "\n".join(digest_lines)
            gemini_contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=digest_text)])
            )
            gemini_contents.append(
                types.Content(role="model", parts=[types.Part.from_text(text="Understood.")])
            )

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
            text_val = msg.get("content") or ("[Attached image]" if msg.get("images") else "")
            if text_val:
                parts.append(types.Part.from_text(text=text_val))

        if parts:
            gemini_contents.append(types.Content(role=role, parts=parts))

    return gemini_contents


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
    lines = [f"# {session_data['title']} — SQOODS PRIME\n"]
    for msg in session_data["messages"]:
        who = "User" if msg["role"] == "user" else "SQOODS PRIME"
        lines.append(f"### {who}\n{msg.get('content', '')}\n")
    return "\n".join(lines)


# --- 3. SPACIOUS HEADER WITH ICON-ONLY SEARCH TOGGLE IN TOP RIGHT ---
title_col, search_col, dl_col = st.columns([5.8, 1.1, 0.85], gap="medium", vertical_alignment="center")
with title_col:
    st.title(":material/bolt: SQOODS PRIME")

with search_col:
    use_search = st.toggle(
        ":material/search:",
        value=True,
        help="Google Search",
    )

current_session = st.session_state.sessions[st.session_state.current_session_idx]
messages = current_session["messages"]

with dl_col:
    if messages:
        st.download_button(
            label=":material/download:",
            data=build_notes_markdown(current_session),
            file_name="sqoods_prime_notes.md",
            mime="text/markdown",
            help="Export Notes",
            use_container_width=True,
        )

st.markdown("<div style='height: 10px;'></div>", unsafe_allow_html=True)

# --- 4. SPACIOUS CHATBOX WITH CRISP INLINE SVG TOOLBAR ---
SVG_COPY = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>'
SVG_CHECK = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#16a34a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>'
SVG_SHARE = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/><line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/></svg>'
SVG_WA = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/></svg>'
SVG_MAIL = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/></svg>'
SVG_SPEAK = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/></svg>'
SVG_STOP = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>'
SVG_MIC = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/></svg>'
SVG_MIC_ACTIVE = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#dc2626" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/></svg>'

for i, message in enumerate(messages):
    with st.chat_message(message["role"]):
        if message.get("images"):
            valid_imgs = [img for img in message["images"] if img.get("bytes")]
            if valid_imgs:
                img_cols = st.columns(min(len(valid_imgs), 3), gap="medium")
                for idx_img, img_data in enumerate(valid_imgs):
                    with img_cols[idx_img % len(img_cols)]:
                        st.image(img_data["bytes"], use_container_width=True)

        if message.get("content"):
            st.markdown(message["content"])

        if message["role"] == "assistant" and message.get("content"):
            js_safe_text = json.dumps(message["content"])
            encoded_text = urllib.parse.quote(message["content"])
            wa_url = f"https://api.whatsapp.com/send?text={encoded_text}"
            gmail_url = f"mailto:?subject=SQOODS%20PRIME&body={encoded_text}"

            toolbar_html = f"""
            <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:6px;">
                <button id="copy-btn-{i}" onclick="copyText_{i}()" title="Copy"
                    style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; background:#f8fafc; color:#334155; border:1px solid #cbd5e1; border-radius:9px; cursor:pointer; padding:0;">
                    {SVG_COPY}
                </button>
                <button onclick="toggleShare_{i}()" title="Share"
                    style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; background:#f8fafc; color:#334155; border:1px solid #cbd5e1; border-radius:9px; cursor:pointer; padding:0;">
                    {SVG_SHARE}
                </button>
                <div id="share-menu-{i}" style="display:none; gap:8px; align-items:center;">
                    <a href="{wa_url}" target="_blank" onclick="closeShare_{i}()" title="WhatsApp"
                        style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; background:#16a34a; color:white; border-radius:9px;">
                        {SVG_WA}
                    </a>
                    <a href="{gmail_url}" target="_blank" onclick="closeShare_{i}()" title="Email"
                        style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; text-decoration:none; background:#2563eb; color:white; border-radius:9px;">
                        {SVG_MAIL}
                    </a>
                </div>
                <button onclick="speakText_{i}()" title="Read Aloud"
                    style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; background:#f8fafc; color:#334155; border:1px solid #cbd5e1; border-radius:9px; cursor:pointer; padding:0;">
                    {SVG_SPEAK}
                </button>
                <button onclick="window.speechSynthesis.cancel();" title="Stop"
                    style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; background:#f8fafc; color:#334155; border:1px solid #cbd5e1; border-radius:9px; cursor:pointer; padding:0;">
                    {SVG_STOP}
                </button>
            </div>
            <script>
                const msgText_{i} = {js_safe_text};
                const svgCopy_{i} = {json.dumps(SVG_COPY)};
                const svgCheck_{i} = {json.dumps(SVG_CHECK)};

                function copyText_{i}() {{
                    navigator.clipboard.writeText(msgText_{i}).then(() => {{
                        const btn = document.getElementById("copy-btn-{i}");
                        btn.innerHTML = svgCheck_{i};
                        setTimeout(() => {{ btn.innerHTML = svgCopy_{i}; }}, 1800);
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

voice_html = f"""
<div style="display:flex; align-items:center; gap:8px; margin-bottom:4px; font-family:sans-serif;">
    <button id="mic-btn" onclick="toggleMic()" title="Voice Input"
        style="width:34px; height:34px; display:inline-flex; align-items:center; justify-content:center; background:#f8fafc; color:#334155; border:1px solid #cbd5e1; border-radius:9px; cursor:pointer; padding:0; flex-shrink:0;">
        {SVG_MIC}
    </button>
    <span id="voice-preview" onclick="copyVoice()" title="Click to copy"
        style="font-size:12.5px; color:#475569; cursor:pointer; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; max-width:560px;"></span>
</div>
<script>
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    let rec = null;
    let listening = false;
    let lastTranscript = "";

    function toggleMic() {{
        const btn = document.getElementById("mic-btn");
        const prev = document.getElementById("voice-preview");
        if (!SpeechRec) {{
            prev.innerText = "Speech recognition not supported in this browser.";
            return;
        }}
        if (listening && rec) {{
            rec.stop();
            listening = false;
            btn.innerHTML = {json.dumps(SVG_MIC)};
            return;
        }}
        rec = new SpeechRec();
        rec.lang = "en-IN";
        rec.interimResults = true;
        rec.maxAlternatives = 1;
        listening = true;
        btn.innerHTML = {json.dumps(SVG_MIC_ACTIVE)};
        prev.innerText = "Listening...";

        rec.onresult = (event) => {{
            let transcript = "";
            for (let i = 0; i < event.results.length; i++) {{
                transcript += event.results[i][0].transcript;
            }}
            lastTranscript = transcript.trim();
            prev.innerText = lastTranscript ? lastTranscript + " (click to copy)" : "Listening...";
        }};

        rec.onerror = () => {{
            listening = false;
            btn.innerHTML = {json.dumps(SVG_MIC)};
            prev.innerText = "Mic permission denied or unavailable.";
        }};

        rec.onend = () => {{
            listening = false;
            btn.innerHTML = {json.dumps(SVG_MIC)};
            if (lastTranscript) {{
                navigator.clipboard.writeText(lastTranscript).catch(() => {{}});
                prev.innerText = lastTranscript + " (copied)";
            }}
        }};

        rec.start();
    }}

    function copyVoice() {{
        if (!lastTranscript) return;
        navigator.clipboard.writeText(lastTranscript).then(() => {{
            document.getElementById("voice-preview").innerText = lastTranscript + " (copied)";
        }});
    }}
</script>
"""
components.html(voice_html, height=44)

chat_payload = st.chat_input(
    "Message SQOODS PRIME...",
    accept_file="multiple",
    file_type=["png", "jpg", "jpeg", "webp"],
    key=f"chat_input_{st.session_state.input_counter}",
)

raw_text = (chat_payload.text or "").strip() if chat_payload else ""
uploaded_files = list(chat_payload.files or []) if chat_payload else []

if raw_text or uploaded_files:
    stored_images = []
    for f in uploaded_files:
        stored_images.append({
            "name": f.name,
            "bytes": f.getvalue(),
            "mime_type": f.type or "image/jpeg",
        })

    display_text = raw_text if raw_text else "Analyze attached images."
    effective_prompt = (
        raw_text
        if raw_text
        else "Examine the attached images carefully and explain or solve the content clearly."
    )

    if current_session["title"].startswith("Chat ") and len(messages) == 0:
        title_source = raw_text if raw_text else "Image Query"
        current_session["title"] = title_source[:24] + ("..." if len(title_source) > 24 else "")

    messages.append({
        "role": "user",
        "content": display_text,
        "images": stored_images,
    })

    with st.chat_message("user"):
        if stored_images:
            img_cols = st.columns(min(len(stored_images), 3), gap="medium")
            for idx_img, img_data in enumerate(stored_images):
                with img_cols[idx_img % len(img_cols)]:
                    st.image(img_data["bytes"], use_container_width=True)
        st.markdown(display_text)

    with st.chat_message("assistant"):
        with st.spinner("Processing..."):
            try:
                gemini_contents = build_token_efficient_contents(messages, effective_prompt)
                response = generate_with_resilience(gemini_contents, enable_search=use_search)
                bot_reply = response.text
                st.markdown(bot_reply)
                messages.append({"role": "assistant", "content": bot_reply})
                save_user_chats(
                    st.session_state.auth_user,
                    st.session_state.sessions,
                    st.session_state.current_session_idx,
                )
                st.session_state.input_counter += 1
                st.rerun()
            except APIError as e:
                if e.code == 429:
                    st.warning("Rate limit reached. Try again shortly.")
                else:
                    st.error(f"Error: {e}")
            except Exception as e:
                err_text = str(e)
                if "429" in err_text or "RESOURCE_EXHAUSTED" in err_text:
                    st.warning("Rate limit reached. Try again shortly.")
                else:
                    st.error(f"Error: {e}")
