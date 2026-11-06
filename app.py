import streamlit as st
from google import genai
from google.genai import types

st.set_page_config(page_title="SQOODS AI", page_icon="⚡", layout="centered")

st.title("⚡ SQOODS - Quantum Neural Core")
st.caption("Live Search Grounding Enabled")

# Initialize Gemini Client using your API key
API_KEY = "AQ.Ab8RN6KfPhIn4UbpWtLVXCy3QfDNFf6DnBCXdmY15B6aC2WcVg"
client = genai.Client(api_key=API_KEY)
MODEL_ID = "gemini-2.5-flash"

# Maintain chat history in session state
if "messages" not in st.session_state:
    st.session_state.messages = []

# Display prior chat bubbles
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# User input field
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