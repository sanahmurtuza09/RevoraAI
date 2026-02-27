# RevoraAI — Self Reflection & Clinician Insight Prototype

RevoraAI is a Streamlit prototype exploring how structured reflection can reduce session time spent reconstructing context and help clinicians see patterns faster.

## Features
- Daily check-in (once/day) + event check-ins (anytime)
- Dashboard with date-range filtering
- Clinician Summary (date-range snapshot + AI session prep prompts)
- Export: clinician PDF + dashboard PNG/PDF + data CSV

## Public Demo Note (AI)
AI features are disabled in the public deployment to avoid exposing API keys.
Full AI functionality can be demonstrated live or locally.

## Run locally
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py