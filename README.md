# Document Q&A Tutor

A portfolio-ready document-grounded Q&A app for AI-901 learning. Students upload PDF/TXT course notes, ask questions, and inspect clickable source citations (filename, page, and the retrieved passage).



- macOS/Linux: Python 3.10+ and `bash`
- Windows: Python 3.10+ and Command Prompt
- Internet connection for the first run, to install Python packages
- No Azure account or credentials are needed for local demo mode

## One-command local startup

### macOS / Linux

From the extracted project folder:

```bash
chmod +x start.sh
./start.sh
```

The script creates `.venv`, installs dependencies, creates `.env` if absent, and starts the app in **LOCAL DEMO** mode. Open:

<http://127.0.0.1:8000>

Upload `sample_docs/AI_Study_Notes.txt`, then ask: `What is retrieval augmented generation?`

Stop the server with `Ctrl+C`.

### Windows

Double-click `start.bat`, or run it from Command Prompt:

```bat
start.bat
```

Then open <http://127.0.0.1:8000>. Stop with `Ctrl+C`.

## Features

- PDF and TXT upload
- PDF page-aware text extraction with `pypdf`
- Page-preserving overlapping text chunks
- Local keyword retrieval in demo mode
- Azure AI Search lexical retrieval in Azure mode
- Microsoft Foundry chat-completion integration in Azure mode
- Azure Blob Storage upload for original documents
- Grounding prompt: answer only from retrieved passages; disclose when evidence is missing
- Clickable citation anchors with filename, page, and full source passage
- Secrets supplied through `.env`, never embedded in source code

## Architecture

```text
                  ┌────────────────────────────┐
                  │ Browser UI                 │
                  │ Upload PDF/TXT + Ask        │
                  └─────────────┬──────────────┘
                                │ HTTP / FastAPI
                  ┌─────────────▼──────────────┐
                  │ Python API                 │
                  │ Parse → page-aware chunks  │
                  └─────────────┬──────────────┘
                                │
                 ┌──────────────┴─────────────────┐
                 │                                │
        LOCAL DEMO MODE                    AZURE MODE
        ┌──────────────────┐       ┌────────────────────────┐
        │ In-memory chunks │       │ Azure Blob Storage     │
        │ Keyword retrieval│       │ Original uploaded file │
        │ Passage response │       └────────────────────────┘
        │ (no model call)  │       ┌────────────────────────┐
        └──────────────────┘       │ Azure AI Search        │
                                   │ Chunk index + retrieval│
                                   └───────────┬────────────┘
                                               │ passages
                                   ┌───────────▼────────────┐
                                   │ Microsoft Foundry      │
                                   │ Deployed chat model    │
                                   │ Grounded answer + [n]  │
                                   └───────────┬────────────┘
                                               │
                                   ┌───────────▼────────────┐
                                   │ Answer + source cards  │
                                   └────────────────────────┘
```

## Configuration

`.env.example` documents all variables. The start scripts create `.env` for local demo. Keep `.env` private and do not commit it. `.gitignore` is included.

For Azure, set `APP_MODE=azure` and fill in the Azure variables. The sample `.env` may contain placeholder values; replace them with real values.

| Variable | Purpose |
|---|---|
| `APP_MODE` | `demo` or `azure` |
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint |
| `AZURE_SEARCH_KEY` | Search service key with index write/read permissions (admin key is simplest for this demo) |
| `AZURE_SEARCH_INDEX` | Existing index name, e.g. `document-qa-chunks` |
| `AZURE_STORAGE_CONNECTION_STRING` | Storage account connection string |
| `AZURE_STORAGE_CONTAINER` | Existing private blob container |
| `FOUNDRY_ENDPOINT` | Azure OpenAI-compatible endpoint for the deployed Foundry model |
| `FOUNDRY_API_KEY` | API key for that model endpoint |
| `FOUNDRY_CHAT_DEPLOYMENT` | Exact deployment name shown in Foundry |
| `FOUNDRY_API_VERSION` | API version supported by the endpoint; default `2024-10-21` |

## Azure setup: real AI answers and hosting

The following are required; none are provisioned by this project.

1. **Azure subscription and resource group**
   - Create or select an Azure subscription.
   - Create a resource group in a region that supports the required services and model.

2. **Azure Blob Storage**
   - Create a Storage Account and a private Blob container named `course-notes` (or change `AZURE_STORAGE_CONTAINER`).
   - Copy its connection string into `AZURE_STORAGE_CONNECTION_STRING`.
   - The demo uploads each original file to Blob Storage in Azure mode.

3. **Azure AI Search**
   - Create an Azure AI Search service.
   - Copy the endpoint and an admin key to `AZURE_SEARCH_ENDPOINT` and `AZURE_SEARCH_KEY`.
   - Create an index matching this schema before starting the app. Example fields:
     - `id`: `Edm.String`, key, not filterable
     - `content`: `Edm.String`, searchable
     - `filename`: `Edm.String`, searchable and filterable
     - `page`: `Edm.Int32`, filterable and sortable
     - `blob_name`: `Edm.String`, filterable
   - Name it `document-qa-chunks`, or set `AZURE_SEARCH_INDEX` to your index name.
   - This implementation uses **keyword search**, not vector search; no embedding deployment is required.

   You can create the index in Azure Portal → Search service → Indexes → Add index. Ensure the field names and types above match exactly. For the `id` field, select it as the key.

4. **Microsoft Foundry model**
   - Open Microsoft Foundry and create/select a project.
   - Deploy a chat-completion model available in your region (for example, a supported GPT-4o-mini deployment, subject to your quota and availability).
   - Copy the model endpoint, API key, and exact deployment name from the model deployment's endpoint/keys page.
   - Put them in `FOUNDRY_ENDPOINT`, `FOUNDRY_API_KEY`, and `FOUNDRY_CHAT_DEPLOYMENT`.
   - The app uses the Azure OpenAI-compatible Python client and the Foundry deployment name as `model`.
   - Check that your selected Foundry endpoint supports the configured API version. If the portal provides a different API version, set `FOUNDRY_API_VERSION` accordingly.

5. **Run in Azure mode**
   - Create a private `.env` based on `.env.example`.
   - Set exactly one active `APP_MODE=azure` line.
   - Install the full requirements and run:
     ```bash
     python -m venv .venv
     source .venv/bin/activate       # Windows: .venv\Scripts\activate
     pip install -r requirements.txt
     python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
     ```
   - Upload documents after the app starts. Azure mode stores originals in Blob Storage and indexes chunks in Azure AI Search; questions retrieve passages and send them to the Foundry model.

### Permissions and security

- For this sample, API keys are used for Search, Blob Storage connection string, and Foundry model endpoint. Keep them in environment variables and never commit them.
- For a production deployment, prefer Microsoft Entra ID / managed identities over long-lived keys:
  - Web app identity: `Storage Blob Data Contributor` on the storage account/container.
  - Search indexing identity: `Search Index Data Contributor` (and appropriate service-level role to manage index schema if the app creates indexes; this sample expects a pre-created index).
  - Search query identity: `Search Index Data Reader`.
  - Foundry model invocation: assign the identity the model's required inference role, commonly `Cognitive Services OpenAI User` where applicable, and configure Entra ID authentication in the client.
- The current code path uses API keys for clarity. Role assignments alone do not replace the key-based environment variables in this implementation.

### Hosting

To host the app, create an Azure App Service (Linux, Python runtime compatible with Python 3.10+) or another Python web host. Deploy the project folder, configure the environment variables in the hosting service's application settings, install `requirements.txt`, and set the startup command to:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Use HTTPS, restrict access to uploaded study materials, configure authentication before public use, and set upload limits/retention policies. This repository does not create or deploy an Azure web app.

## Troubleshooting

- **`python3: command not found`**: install Python 3.10+ and ensure it is on PATH. On Windows use `python`.
- **Port 8000 already in use**: stop the other process or change `--port 8000` in the start script.
- **PDF uploads but no text is found**: scanned/image-only PDFs need OCR; this sample extracts embedded PDF text only.
- **`Could not read PDF`**: verify the PDF is not password-protected or damaged.
- **Azure mode reports missing variables**: check `.env`, remove placeholder values, and ensure only one active `APP_MODE` line exists.
- **Azure Search upload fails**: verify index schema and key permissions. `id` must be the key; field names must match the schema above.
- **Foundry authentication/deployment error**: verify endpoint, key, API version, model deployment name, quota, and regional availability.
- **Package installation fails**: update pip (`python -m pip install --upgrade pip`) and confirm Python 3.10+.

## Cost guidance

- **Local demo:** no Azure consumption charges; package installation uses your internet connection.
- **Azure:** Blob Storage capacity/operations, Azure AI Search service tier, Foundry model input/output tokens, and App Service hosting can incur charges.
- Search and hosting resources may bill while provisioned even when idle. Check the Azure Pricing Calculator and current regional pricing before deployment.
- Start with a small development Search tier and a low-cost model deployment where available. Delete resources after testing. Set budgets and cost alerts; budgets notify but do not automatically cap spend.

## How this demonstrates AI-901 topics

- **Generative AI application pattern:** retrieve relevant knowledge and provide it as model context.
- **Grounding / responsible AI:** constrain the prompt to supplied evidence and expose citations for verification.
- **Prompt engineering:** explicit system instructions, citation format, and abstention behavior.
- **Data preparation:** PDF/TXT parsing, page metadata, chunking, and overlap.
- **Search and retrieval:** lexical indexing and top-k retrieval with Azure AI Search.
- **Cloud AI services:** Blob Storage for source files and Microsoft Foundry for deployed model inference.
- **Application integration:** REST API, frontend, configuration, and secret management.

## Scope and limitations

- Demo retrieval is a transparent keyword-overlap baseline, not semantic search or an LLM.
- Azure retrieval is lexical Azure AI Search; vector/semantic ranking and embeddings are not configured.
- OCR, authentication, multi-user isolation, deletion UI, malware scanning, and production upload controls are outside this portfolio demo.
- Citation page numbers refer to PDF page positions (1-based). TXT files are treated as page 1.
