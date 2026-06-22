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
        with st.spinner("Processing..."):
            try:
                # 1. Optimized Web Scraper (Added 5-second timeout and streamlined headers)
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
                response = requests.get(url_input, headers=headers, timeout=5)
                
                # 2. Extract ONLY raw text and strip out heavy scripts/styles immediately
                soup = BeautifulSoup(response.text, 'html.parser')
                for script in soup(["script", "style", "nav", "footer", "header"]):
                    script.extract()
                
                # Get clean text chunks
                page_text = " ".join(soup.get_text().split())[:3000]

                # 3. Configure Gemini
                genai.configure(api_key=api_key_input)
                model = genai.GenerativeModel('gemini-2.5-flash')
                
                # 4. Construct the prompt
                prompt = f"""
                You are a social media manager. Take this raw website text and format it into a single tweet using the exact template structure below. 

                Rules:
                - ALWAYS use abbreviated days of the week (e.g., Mon, Tues, Wed, Thurs, Fri, Sat, Sun).
                - ALWAYS use abbreviated months (e.g., Jan, Feb, Mar, Apr, May, Jun, Jul, Aug, Sep, Oct, Nov, Dec).
                - Do not add text before or after the template. Only output the final tweet text.

                Template:
                [Opponent Team Name] (H) - Sale Details 📢

                Registration (All Members) 📝
                • Opens: [Day Date, Time]
                • Closes: [Day Date, Time]
                • Links sent: [Day Date]

                Sale • [Day Date, Time] 🎟️

                Local & YA Ballot ☑️
                • Opens: [Day Date, Time]
                • Closes: [Day Date, Time]

                Match Date • [Day Date, Time] 🏟️

                Source Text to extract from:
                {page_text}
                """

                # 5. Generate text
                ai_response = model.generate_content(prompt)
                tweet = ai_response.text

                # 6. Output Result to User
                st.success("Generated Successfully!")
                st.text_area("Your Tweet:", value=tweet, height=250)
                st.caption("💡 Highlight the text inside the box above to copy it directly!")

            except requests.exceptions.Timeout:
                st.error("The LFC website took too long to respond. Please try again.")
            except Exception as e:
                st.error(f"An error occurred: {e}")
