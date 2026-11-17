import os
import re
import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="SQOODS AI", page_icon="⚡", layout="centered")

st.title("⚡ SQOODS - Quantum Neural Core")
st.caption("Live Search Grounding Enabled")

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

# Maintain chat history
if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if user_prompt := st.chat_input("Enter command for SQOODS..."):
    st.session_state.messages.append({"role": "user", "content": user_prompt})
    with st.chat_message("user"):
        st.markdown(user_prompt)

    with st.chat_message("assistant"):
        with st.spinner("Processing neural matrix..."):
            try:
                response = client.models.generate_content(
                    model=MODEL_ID,
                    contents=user_prompt,
                    config=types.GenerateContentConfig(
                        system_instruction="You are SQOODS, an advanced, elite, and futuristic AI collaborator with a sharp, cyberpunk tone.",
                        tools=[{"google_search": {}}]
                    )
                )
                bot_reply = response.text
                st.markdown(bot_reply)
                st.session_state.messages.append({"role": "assistant", "content": bot_reply})
            except Exception as e:
                st.error(f"Error communicating with core: {e}")
