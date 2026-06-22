import streamlit as st
import google.generativeai as genai
import requests
from bs4 import BeautifulSoup

# App Title & UI Layout
st.set_page_config(page_title="LFC Tweet Generator", page_icon="⚽")
st.title("⚽ LFC Ticket Tweet Generator")
st.write("Paste the Liverpool FC ticket page URL below to generate your formatted tweet.")

# Input fields
url_input = st.text_input("Ticket Page URL:", placeholder="https://www.liverpoolfc.com/tickets/...")
api_key_input = st.text_input("Enter Gemini API Key (Keep it private):", type="password")

if st.button("Generate Tweet ✨"):
    if not url_input or not api_key_input:
        st.error("Please provide both the URL and your Gemini API Key.")
    else:
        with st.spinner("Scraping page data and writing tweet..."):
            try:
                # 1. Scrape the webpage text
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(url_input, headers=headers)
                soup = BeautifulSoup(response.text, 'html.parser')
                # Grab text content, ignoring heavy script/style blocks
                page_text = soup.get_text(separator=' ', strip=True)[:4000] 

                # 2. Configure Gemini
                genai.configure(api_key=api_key_input)
                model = genai.GenerativeModel('gemini-2.5-flash')
                
                # 3. Construct the prompt
                prompt = f"""
                You are a social media manager. Take this raw website text and format it into a single tweet using the exact template structure below. 
                Do not add text before or after the template. Only output the final tweet text.

                Template:
                [Opponent Team Name] (H) - Sale Details 📢

                Registration (All Members) 📝
                • Opens: [Day Date, Time]
                • Closes: [Day Date, Time]
                • Links sent: [Day Date]

                Sale • [Day Date, Time] 🎟️

                Local & YA Ballot 🗳️
                • Opens: [Day Date, Time]
                • Closes: [Day Date, Time]

                Match Date • [Day Date, Time] 🏟️

                Source Text to extract from:
                {page_text}
                """

                # 4. Generate text
                ai_response = model.generate_content(prompt)
                tweet = ai_response.text

                # 5. Output Result to User
                st.success("Generated Successfully!")
                st.text_area("Your Tweet:", value=tweet, height=250)
                st.caption("💡 Highlight the text inside the box above to copy it directly!")

            except Exception as e:
                st.error(f"An error occurred: {e}")
