# Oxydata CV Formatter

A web application that converts uploaded CVs (PDF or DOCX) into the standardized Oxydata CV format using OpenAI GPT-4o.

## Features

- Upload CV in PDF or Word (DOCX) format
- AI-powered content extraction and structuring via GPT-4o
- Generates a professionally formatted DOCX matching the Oxydata template
- Real-time progress updates during processing
- Drag-and-drop file upload interface

## Local Development

### Prerequisites

- Python 3.12+
- An OpenAI API key with access to GPT-4o

### Setup

1. Create a virtual environment and install dependencies:

```bash
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
```

2. Create a `.env` file from the example:

```bash
copy .env.example .env
```

3. Edit `.env` and add your OpenAI API key.

4. Run the development server:

```bash
uvicorn app.main:app --reload --port 8000
```

5. Open http://localhost:8000 in your browser.

### Local testing with the Airtable button

Airtable must fetch the generated DOCX from a **public HTTPS** URL. Your laptop’s `localhost` is not reachable from Airtable, so expose port **8000** with a tunnel and set **`APP_URL`** to that HTTPS origin (no trailing slash).

**Using ngrok** (after install, open a new terminal so `ngrok` is on your PATH):

1. One-time: create a free account at [ngrok](https://ngrok.com), then run `ngrok config add-authtoken <your-token>`.
2. **Terminal 1:** `uvicorn app.main:app --reload --port 8000`
3. **Terminal 2:** `ngrok http 8000`
4. Copy the **https** “Forwarding” URL (e.g. `https://abc123.ngrok-free.app`).
5. In `.env`, set `APP_URL` to that value and **restart** uvicorn so `load_dotenv()` picks it up.
6. In Airtable, point the button’s Open URL formula at the **same** host, e.g.  
   `CONCATENATE("https://abc123.ngrok-free.app/airtable/process?record_id=", RECORD_ID())`

If Airtable still cannot attach the file (some ngrok free tiers interfere with automated downloads), try **Cloudflare Tunnel** instead: install `cloudflared`, run `cloudflared tunnel --url http://localhost:8000`, and use the printed `https://....trycloudflare.com` URL for both `APP_URL` and the Airtable button.

## Deployment on Render

1. Push this repository to GitHub.
2. Create a new Web Service on [Render](https://render.com).
3. Connect your GitHub repo.
4. Render will auto-detect the `render.yaml` configuration.
5. In the Render dashboard, set environment variables (see `.env.example`):  
   `OPENAI_API_KEY`, `AIRTABLE_BASE_ID`, `AIRTABLE_TABLE_ID`, `AIRTABLE_PAT` (or `AIRTABLE_TOKEN`), and `APP_URL` (your service URL, e.g. `https://<name>.onrender.com`, no trailing slash).
6. Deploy.

## Project Structure

```
app/
  main.py              - FastAPI application and routes
  text_extractor.py    - PDF and DOCX text extraction
  cv_parser.py         - GPT-4o integration for structured CV parsing
  docx_generator.py    - Oxydata-formatted DOCX generation
  template_spec.py     - Template formatting constants
static/
  index.html           - Frontend UI
requirements.txt       - Python dependencies
render.yaml            - Render deployment config
```
