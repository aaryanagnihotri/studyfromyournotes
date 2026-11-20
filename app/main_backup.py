import os, re, uuid, io
from typing import List, Dict, Any
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()
APP_MODE = os.getenv("APP_MODE", "demo").strip().lower()
if APP_MODE not in {"demo", "azure"}:
    raise RuntimeError("APP_MODE must be 'demo' or 'azure'.")

app = FastAPI(title="Document Q&A Tutor", version="1.0.0")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
documents: List[Dict[str, Any]] = []

def parse_document(filename: str, raw: bytes) -> List[Dict[str, Any]]:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if ext == "txt":
        text = raw.decode("utf-8-sig", errors="replace")
        return [{"page": 1, "text": text}]
    if ext == "pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw))
            return [{"page": i + 1, "text": page.extract_text() or ""} for i, page in enumerate(reader.pages)]
        except Exception as exc:
            raise HTTPException(400, f"Could not read PDF: {exc}")
    raise HTTPException(400, "Only PDF and TXT files are supported.")

def make_chunks(filename: str, pages: List[Dict[str, Any]], size: int = 900, overlap: int = 150):
    chunks = []
    for p in pages:
        text = re.sub(r"\s+", " ", p["text"]).strip()
        if not text:
            continue
        start = 0
        while start < len(text):
            end = min(len(text), start + size)
            passage = text[start:end].strip()
            if passage:
                chunks.append({
                    "id": str(uuid.uuid4()), "filename": filename,
                    "page": p["page"], "content": passage
                })
            if end == len(text):
                break
            start = max(start + 1, end - overlap)
    return chunks

def terms(text: str):
    return set(re.findall(r"[a-zA-Z0-9]{2,}", text.lower()))

def demo_retrieve(question: str, limit: int = 5):
    q = terms(question)
    scored = []
    for chunk in documents:
        t = terms(chunk["content"])
        score = len(q & t) / max(1, len(q))
        if score > 0:
            scored.append((score, chunk))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:limit]]

def azure_clients():
    try:
        from azure.search.documents import SearchClient
        from azure.core.credentials import AzureKeyCredential
        from azure.storage.blob import BlobServiceClient
        from openai import AzureOpenAI
    except ImportError as exc:
        raise HTTPException(500, "Azure dependencies missing. Run: pip install -r requirements.txt") from exc

    required = ["AZURE_SEARCH_ENDPOINT", "AZURE_SEARCH_KEY", "AZURE_SEARCH_INDEX",
                "AZURE_STORAGE_CONNECTION_STRING", "AZURE_STORAGE_CONTAINER",
                "FOUNDRY_ENDPOINT", "FOUNDRY_API_KEY", "FOUNDRY_CHAT_DEPLOYMENT"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise HTTPException(500, "Azure mode is not configured. Missing: " + ", ".join(missing))
    search = SearchClient(os.environ["AZURE_SEARCH_ENDPOINT"], os.environ["AZURE_SEARCH_INDEX"],
                          AzureKeyCredential(os.environ["AZURE_SEARCH_KEY"]))
    blob_service = BlobServiceClient.from_connection_string(os.environ["AZURE_STORAGE_CONNECTION_STRING"])
    blob_container = blob_service.get_container_client(os.environ["AZURE_STORAGE_CONTAINER"])
    ai = AzureOpenAI(
        azure_endpoint=os.environ["FOUNDRY_ENDPOINT"],
        api_key=os.environ["FOUNDRY_API_KEY"],
        api_version=os.getenv("FOUNDRY_API_VERSION", "2024-10-21")
    )
    return search, blob_container, ai

def azure_index_chunks(chunks, raw: bytes, filename: str):
    search, container, _ = azure_clients()
    blob_name = f"{uuid.uuid4()}-{filename.replace('/', '_')}"
    container.upload_blob(name=blob_name, data=raw, overwrite=True)
    docs = []
    for c in chunks:
        docs.append({
            "id": c["id"].replace("-", ""),
            "content": c["content"],
            "filename": c["filename"],
            "page": c["page"],
            "blob_name": blob_name
        })
    if docs:
        search.upload_documents(documents=docs)

@app.get("/", response_class=HTMLResponse)
def home():
    with open("app/static/index.html", encoding="utf-8") as f:
        return f.read()

@app.get("/api/status")
def status():
    return {"mode": APP_MODE, "label": "LOCAL DEMO — no real AI model" if APP_MODE == "demo" else "AZURE — uses configured services"}

@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    filename = os.path.basename(file.filename or "upload")
    raw = await file.read()
    pages = parse_document(filename, raw)
    chunks = make_chunks(filename, pages)
    if not chunks:
        raise HTTPException(400, "No extractable text found in this document.")
    if APP_MODE == "azure":
        azure_index_chunks(chunks, raw, filename)
    else:
        documents.extend(chunks)
    return {"filename": filename, "pages": len(pages), "chunks": len(chunks),
            "message": f"Added {filename} ({len(chunks)} searchable chunks)."}

class Question(BaseModel):
    question: str

@app.post("/api/ask")
def ask(body: Question):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "Enter a question.")
    if APP_MODE == "demo":
        matches = demo_retrieve(question)
        if not matches:
            return {"answer": "I couldn't find a relevant passage in the uploaded notes. Try another wording or upload the relevant notes.",
                    "citations": [], "mode": "demo"}
        passages = []
        for i, m in enumerate(matches[:3], 1):
            passages.append(f"[{i}] {m['content']}")
        answer = ("LOCAL DEMO (not an AI-generated answer): The closest matching passages in your notes are shown below. "
                  "Use the citations to verify the source.\\n\\n" + "\\n\\n".join(passages))
        citations = [{"filename": m["filename"], "page": m["page"], "passage": m["content"]} for m in matches[:3]]
        return {"answer": answer, "citations": citations, "mode": "demo"}

    search, _, ai = azure_clients()
    results = list(search.search(search_text=question, top=5, select=["content", "filename", "page", "blob_name"]))
    hits = []
    for r in results:
        hits.append({"filename": r.get("filename", "document"), "page": r.get("page", 1),
                     "content": r.get("content", "")})
    if not hits:
        return {"answer": "No matching passages were found in Azure AI Search.", "citations": [], "mode": "azure"}
    context = "\\n\\n".join(
        f"[{i+1}] File: {h['filename']} | Page: {h['page']}\\n{h['content']}"
        for i, h in enumerate(hits)
    )
    system_prompt = (
        "You are Document Q&A Tutor for students. Answer only from the supplied source passages. "
        "If the passages do not contain the answer, say so. Do not use outside knowledge. "
        "Cite supporting passages inline using [1], [2], etc. Be clear and concise."
    )
    response = ai.chat.completions.create(
        model=os.environ["FOUNDRY_CHAT_DEPLOYMENT"],
        temperature=0.2,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Question: {question}\\n\\nSource passages:\\n{context}"}
        ]
    )
    return {"answer": response.choices[0].message.content or "", "citations": [
        {"filename": h["filename"], "page": h["page"], "passage": h["content"]} for h in hits
    ], "mode": "azure"}
