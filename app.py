import os
import json
import urllib.parse
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
            # If all chats were deleted, create a fresh empty one
            if len(st.session_state.sessions) == 0:
                st.session_state.sessions = [{"title": "New Chat", "messages": []}]
                st.session_state.current_session_idx = 0
            # Adjust active index safely if needed
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
MODEL_ID = "gemini-2.5-flash"

SYSTEM_PROMPT = """
You are SQOODS PRIME, an advanced, friendly, and highly intelligent AI assistant developed by Anish Shaikh.
- If asked your name, state you are SQOODS PRIME.
- If asked who created you, credit Anish Shaikh.
- Be warm, friendly, approachable, and give brilliant coding and factual assistance.
"""

# --- 3. MAIN CHAT INTERFACE ---
st.title("⚡ SQOODS PRIME")
st.caption("Live Search Grounding • Multi-Chat • Voice • Copy & Share Enabled")

current_session = st.session_state.sessions[st.session_state.current_session_idx]
messages = current_session["messages"]

# Display prior chat history
for i, message in enumerate(messages):
    with st.chat_message(message["role"]):
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

# User input handling
if user_prompt := st.chat_input("Ask SQOODS PRIME anything..."):
    if current_session["title"].startswith("New Chat") and len(messages) == 0:
        current_session["title"] = user_prompt[:25] + ("..." if len(user_prompt) > 25 else "")

    messages.append({"role": "user", "content": user_prompt})
    with st.chat_message("user"):
        st.markdown(user_prompt)

    with st.chat_message("assistant"):
        with st.spinner("SQOODS PRIME is thinking..."):
            try:
                chat_history = []
                for msg in messages:
                    role = "user" if msg["role"] == "user" else "model"
                    chat_history.append(
                        types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])])
                    )

                response = client.models.generate_content(
                    model=MODEL_ID,
                    contents=chat_history,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        tools=[{"google_search": {}}]
                    )
                )
                bot_reply = response.text
                st.markdown(bot_reply)
                messages.append({"role": "assistant", "content": bot_reply})
                st.rerun()
            except Exception as e:
                st.error(f"Error communicating with core: {e}")
