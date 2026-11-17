import os
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="SQOODS PRIME", page_icon="⚡", layout="centered")

st.title("⚡ SQOODS PRIME")
st.caption("Developed by Anish Shaikh • Live Search & Advanced Coding Core")

# Fetch key from Streamlit Secrets or environment
raw_key = None
try:
    if "GEMINI_API_KEY" in st.secrets:
        raw_key = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

if not raw_key:
    raw_key = os.environ.get("GEMINI_API_KEY", "")

# Clean up any newlines, spaces, or extra quotes
cleaned_val = str(raw_key).replace("\n", "").replace("\r", "").replace(" ", "").strip("'").strip('"')

if "=" in cleaned_val:
    cleaned_val = cleaned_val.split("=")[-1]

api_key = cleaned_val.strip()

if not api_key:
    st.error("🚨 API Key not found! Please check your Streamlit Secrets.")
    st.stop()

# Initialize Client with v1beta to support system instructions
client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
MODEL_ID = "gemini-2.5-flash"

# Custom personality & capabilities for SQOODS PRIME
SYSTEM_PROMPT = """
You are SQOODS PRIME, an advanced, friendly, and highly intelligent AI assistant developed by Anish Shaikh.

Core Identity & Personality:
- If anyone asks for your name or who you are, proudly state that you are SQOODS PRIME.
- If anyone asks who created, built, or developed you, always credit Anish Shaikh as your creator and developer.
- Communicate in a warm, friendly, approachable, and encouraging tone—like a smart, helpful collaborator rather than a stiff robot.

Capabilities & Standards:
- High-Level Coding: Write clean, well-structured, bug-free, production-grade code with clear explanations and best practices across all programming languages.
- Best-in-Class Answers: Provide accurate, thorough, and easy-to-follow answers tailored to what the user actually needs, utilizing live Google Search whenever up-to-date facts are required.
"""

# Maintain chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if user_prompt := st.chat_input("Ask SQOODS PRIME anything..."):
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    with st.chat_message("user"):
        st.markdown(user_prompt)

    with st.chat_message("assistant"):
        with st.spinner("SQOODS PRIME is thinking..."):
            try:
                # Build conversation history so SQOODS PRIME remembers context
                chat_history = []
                for msg in st.session_state.messages:
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
                st.session_state.messages.append({"role": "assistant", "content": bot_reply})
            except Exception as e:
                st.error(f"Error communicating with core: {e}")
