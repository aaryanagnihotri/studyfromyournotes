
import os
import re
import uuid
import io
import math
import time
import requests

from typing import List, Dict, Any

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from google import genai
from google.genai import types


# ---------------- CONFIGURATION ----------------

load_dotenv()

APP_MODE = os.getenv(
    "APP_MODE",
    "hybrid"
).strip().lower()

VALID_MODES = {
    "demo",
    "ollama",
    "ollama_cloud",
    "hybrid",
    "azure",
    "gemini"
}

if APP_MODE not in VALID_MODES:
    raise RuntimeError(
        f"APP_MODE must be one of: {', '.join(sorted(VALID_MODES))}"
    )


app = FastAPI(
    title="Document Q&A Tutor",
    version="2.0.0"
)

app.mount(
    "/static",
    StaticFiles(directory="app/static"),
    name="static"
)

documents: List[Dict[str, Any]] = []


# ---------------- DOCUMENT PROCESSING ----------------

def parse_document(
    filename: str,
    raw: bytes
) -> List[Dict[str, Any]]:

    ext = (
        filename.lower().rsplit(".", 1)[-1]
        if "." in filename else ""
    )

    if ext == "txt":

        text = raw.decode(
            "utf-8-sig",
            errors="replace"
        )

        return [
            {
                "page": 1,
                "text": text
            }
        ]

    if ext == "pdf":

        try:

            from pypdf import PdfReader

            reader = PdfReader(
                io.BytesIO(raw)
            )

            return [
                {
                    "page": i + 1,
                    "text": page.extract_text() or ""
                }
                for i, page in enumerate(reader.pages)
            ]

        except Exception as exc:

            raise HTTPException(
                400,
                f"Could not read PDF: {exc}"
            )

    raise HTTPException(
        400,
        "Only PDF and TXT files are supported."
    )


def make_chunks(
    filename: str,
    pages: List[Dict[str, Any]],
    size: int = 900,
    overlap: int = 150
):

    chunks = []

    for page in pages:

        text = re.sub(
            r"\s+",
            " ",
            page["text"]
        ).strip()

        if not text:
            continue

        start = 0

        while start < len(text):

            end = min(
                len(text),
                start + size
            )

            passage = text[start:end].strip()

            if passage:

                chunks.append({
                    "id": str(uuid.uuid4()),
                    "filename": filename,
                    "page": page["page"],
                    "content": passage
                })

            if end == len(text):
                break

            start = max(
                start + 1,
                end - overlap
            )

    return chunks


# ---------------- KEYWORD RETRIEVAL ----------------

def terms(text: str):

    return set(
        re.findall(
            r"[a-zA-Z0-9]{2,}",
            text.lower()
        )
    )


def demo_retrieve(
    question: str,
    limit: int = 5
):

    question_terms = terms(question)

    scored = []

    for chunk in documents:

        chunk_terms = terms(
            chunk["content"]
        )

        score = (
            len(question_terms & chunk_terms)
            / max(1, len(question_terms))
        )

        if score > 0:

            scored.append(
                (score, chunk)
            )

    scored.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return [
        chunk
        for _, chunk in scored[:limit]
    ]


# ---------------- EMBEDDING GENERATION ----------------

def create_embeddings(
    texts: List[str],
    task_type: str = "RETRIEVAL_DOCUMENT"
) -> List[List[float]]:

    # GEMINI EMBEDDINGS

    if APP_MODE == "gemini":

        api_key = os.getenv(
            "GEMINI_API_KEY"
        )

        if not api_key:

            raise RuntimeError(
                "GEMINI_API_KEY is not configured."
            )

        client = genai.Client(
            api_key=api_key
        )

        model = os.getenv(
            "GEMINI_EMBED_MODEL",
            "gemini-embedding-001"
        )

        try:

            response = client.models.embed_content(
                model=model,
                contents=texts,
                config=types.EmbedContentConfig(
                    task_type=task_type,
                    output_dimensionality=768
                )
            )

            embeddings = [
                item.values
                for item in response.embeddings
            ]

            if len(embeddings) != len(texts):

                raise RuntimeError(
                    "Embedding count mismatch."
                )

            return embeddings

        except Exception as exc:

            raise RuntimeError(
                f"Gemini embedding failed: {exc}"
            ) from exc

    # OLLAMA EMBEDDINGS

    url = os.getenv(
        "OLLAMA_EMBED_URL",
        "http://localhost:11434/api/embed"
    )

    model = os.getenv(
        "OLLAMA_EMBED_MODEL",
        "nomic-embed-text"
    )

    try:

        response = requests.post(
            url,
            json={
                "model": model,
                "input": texts
            },
            timeout=180
        )

        response.raise_for_status()

        embeddings = response.json().get(
            "embeddings",
            []
        )

        if len(embeddings) != len(texts):

            raise RuntimeError(
                "Embedding count mismatch."
            )

        return embeddings

    except requests.RequestException as exc:

        raise RuntimeError(
            f"Ollama embedding generation failed: {exc}"
        ) from exc


# ---------------- COSINE SIMILARITY ----------------

def cosine_similarity(
    vector_a,
    vector_b
):

    if len(vector_a) != len(vector_b):
        return 0.0

    dot_product = sum(
        a * b
        for a, b in zip(vector_a, vector_b)
    )

    magnitude_a = math.sqrt(
        sum(a * a for a in vector_a)
    )

    magnitude_b = math.sqrt(
        sum(b * b for b in vector_b)
    )

    if magnitude_a == 0 or magnitude_b == 0:
        return 0.0

    return dot_product / (
        magnitude_a * magnitude_b
    )


# ---------------- SEMANTIC RETRIEVAL ----------------

def semantic_retrieve(
    question: str,
    limit: int = 5
):

    if not documents:
        return []

    question_embedding = create_embeddings(
        [question],
        task_type="RETRIEVAL_QUERY"
    )[0]

    scored = []

    for chunk in documents:

        embedding = chunk.get(
            "embedding"
        )

        if not embedding:
            continue

        score = cosine_similarity(
            question_embedding,
            embedding
        )

        scored.append(
            (score, chunk)
        )

    scored.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return [
        chunk
        for score, chunk in scored[:limit]
    ]


# ---------------- AZURE INTEGRATION ----------------

def azure_clients():

    try:

        from azure.search.documents import SearchClient
        from azure.core.credentials import AzureKeyCredential
        from azure.storage.blob import BlobServiceClient
        from openai import AzureOpenAI

    except ImportError as exc:

        raise HTTPException(
            500,
            "Azure dependencies missing. Run pip install -r requirements.txt"
        ) from exc

    required = [
        "AZURE_SEARCH_ENDPOINT",
        "AZURE_SEARCH_KEY",
        "AZURE_SEARCH_INDEX",
        "AZURE_STORAGE_CONNECTION_STRING",
        "AZURE_STORAGE_CONTAINER",
        "FOUNDRY_ENDPOINT",
        "FOUNDRY_API_KEY",
        "FOUNDRY_CHAT_DEPLOYMENT"
    ]

    missing = [
        key
        for key in required
        if not os.getenv(key)
    ]

    if missing:

        raise HTTPException(
            500,
            "Azure mode is not configured. Missing: "
            + ", ".join(missing)
        )

    search = SearchClient(
        os.environ["AZURE_SEARCH_ENDPOINT"],
        os.environ["AZURE_SEARCH_INDEX"],
        AzureKeyCredential(
            os.environ["AZURE_SEARCH_KEY"]
        )
    )

    blob_service = BlobServiceClient.from_connection_string(
        os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    )

    blob_container = blob_service.get_container_client(
        os.environ["AZURE_STORAGE_CONTAINER"]
    )

    ai = AzureOpenAI(
        azure_endpoint=os.environ["FOUNDRY_ENDPOINT"],
        api_key=os.environ["FOUNDRY_API_KEY"],
        api_version=os.getenv(
            "FOUNDRY_API_VERSION",
            "2024-10-21"
        )
    )

    return search, blob_container, ai


def azure_index_chunks(
    chunks,
    raw: bytes,
    filename: str
):

    search, container, _ = azure_clients()

    blob_name = (
        f"{uuid.uuid4()}-{filename.replace('/', '_')}"
    )

    container.upload_blob(
        name=blob_name,
        data=raw,
        overwrite=True
    )

    docs = []

    for chunk in chunks:

        docs.append({
            "id": chunk["id"].replace("-", ""),
            "content": chunk["content"],
            "filename": chunk["filename"],
            "page": chunk["page"],
            "blob_name": blob_name
        })

    if docs:

        search.upload_documents(
            documents=docs
        )


# ---------------- AI SYSTEM PROMPT ----------------

SYSTEM_PROMPT = """
You are Document Q&A Tutor, an educational AI assistant.

Answer questions using ONLY the supplied document passages.

Rules:

1. Do not invent facts or use outside knowledge.
2. If the passages do not contain the answer, clearly say that
   the uploaded documents do not provide enough information.
3. Explain concepts in simple, student-friendly language.
4. Cite supporting passages using [1], [2], etc.
5. Keep answers clear and well structured.
"""


def build_context(hits):

    return "\n\n".join(
        f"[{i + 1}] File: {hit['filename']} | Page: {hit['page']}\n"
        f"{hit['content']}"
        for i, hit in enumerate(hits)
    )


# ---------------- GEMINI AI ANSWER GENERATION ----------------

def gemini_answer(
    question: str,
    context: str
):

    api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    if not api_key:

        raise RuntimeError(
            "GEMINI_API_KEY is not configured."
        )

    client = genai.Client(
        api_key=api_key
    )

    model = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.8-flash"
    )

    prompt = (
        f"Question: {question}\n\n"
        f"Source passages:\n{context}"
    )

    for attempt in range(2):

        try:

            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    temperature=0.2
                )
            )

            answer = response.text

            if not answer:

                raise RuntimeError(
                    "Gemini returned an empty answer."
                )

            return answer

        except Exception as exc:

            error_message = str(exc).upper()

            temporary_error = any(
                code in error_message
                for code in [
                    "503",
                    "UNAVAILABLE",
                    "429",
                    "RESOURCE_EXHAUSTED",
                    "500",
                    "INTERNAL"
                ]
            )

            if temporary_error and attempt == 0:

                wait_time = 1

                print(
                    f"Gemini temporarily unavailable. "
                    f"Retrying in {wait_time} second..."
                )

                time.sleep(wait_time)

                continue

            raise RuntimeError(
                f"Gemini request failed: {exc}"
            ) from exc


# ---------------- OLLAMA LOCAL ----------------

def ollama_local_answer(
    question: str,
    context: str
):

    url = os.getenv(
        "OLLAMA_LOCAL_URL",
        "http://localhost:11434/api/chat"
    )

    model = os.getenv(
        "OLLAMA_MODEL",
        "qwen2.5:3b"
    )

    try:

        response = requests.post(
            url,
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n\n"
                            f"Source passages:\n{context}"
                        )
                    }
                ],
                "stream": False,
                "options": {
                    "temperature": 0.2
                }
            },
            timeout=180
        )

        response.raise_for_status()

        data = response.json()

        answer = data.get(
            "message",
            {}
        ).get(
            "content",
            ""
        )

        if not answer:

            raise RuntimeError(
                "Ollama returned an empty answer."
            )

        return answer

    except requests.RequestException as exc:

        raise RuntimeError(
            f"Local Ollama failed: {exc}"
        ) from exc


# ---------------- OLLAMA CLOUD ----------------

def ollama_cloud_answer(
    question: str,
    context: str
):

    api_key = os.getenv(
        "OLLAMA_API_KEY"
    )

    model = os.getenv(
        "OLLAMA_CLOUD_MODEL"
    )

    if not api_key:

        raise RuntimeError(
            "OLLAMA_API_KEY is not configured."
        )

    if not model:

        raise RuntimeError(
            "OLLAMA_CLOUD_MODEL is not configured."
        )

    url = os.getenv(
        "OLLAMA_CLOUD_URL",
        "https://ollama.com/api/chat"
    )

    try:

        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n\n"
                            f"Source passages:\n{context}"
                        )
                    }
                ],
                "stream": False,
                "options": {
                    "temperature": 0.2
                }
            },
            timeout=180
        )

        response.raise_for_status()

        data = response.json()

        answer = data.get(
            "message",
            {}
        ).get(
            "content",
            ""
        )

        if not answer:

            raise RuntimeError(
                "Ollama Cloud returned an empty answer."
            )

        return answer

    except requests.RequestException as exc:

        raise RuntimeError(
            f"Ollama Cloud failed: {exc}"
        ) from exc


# ---------------- AI PROVIDER ROUTER ----------------

def generate_answer(
    question: str,
    context: str
):

    # GEMINI WITH AUTOMATIC OLLAMA FALLBACK

    if APP_MODE == "gemini":

        try:

            answer = gemini_answer(
                question,
                context
            )

            return answer, "gemini"

        except Exception as gemini_error:

            print(
                f"Gemini failed: {gemini_error}"
            )

            print(
                "Switching to local Ollama..."
            )

            try:

                answer = ollama_local_answer(
                    question,
                    context
                )

                print(
                    "Ollama fallback successful."
                )

                return answer, "ollama_fallback"

            except Exception as ollama_error:

                raise HTTPException(
                    502,
                    (
                        f"Gemini failed: {gemini_error}. "
                        f"Ollama fallback also failed: {ollama_error}"
                    )
                )

    # LOCAL OLLAMA

    if APP_MODE == "ollama":

        return (
            ollama_local_answer(
                question,
                context
            ),
            "ollama"
        )

    # OLLAMA CLOUD

    if APP_MODE == "ollama_cloud":

        return (
            ollama_cloud_answer(
                question,
                context
            ),
            "ollama_cloud"
        )

    # HYBRID OLLAMA

    if APP_MODE == "hybrid":

        try:

            answer = ollama_local_answer(
                question,
                context
            )

            return answer, "ollama_local"

        except RuntimeError as local_error:

            if (
                not os.getenv("OLLAMA_API_KEY")
                or not os.getenv("OLLAMA_CLOUD_MODEL")
            ):

                raise HTTPException(
                    503,
                    (
                        "Local Ollama is unavailable and "
                        f"Cloud is not configured. {local_error}"
                    )
                )

            try:

                answer = ollama_cloud_answer(
                    question,
                    context
                )

                return answer, "ollama_cloud"

            except RuntimeError as cloud_error:

                raise HTTPException(
                    502,
                    (
                        "Both Ollama providers failed. "
                        f"{cloud_error}"
                    )
                )

    # AZURE

    if APP_MODE == "azure":

        _, _, ai = azure_clients()

        response = ai.chat.completions.create(
            model=os.environ["FOUNDRY_CHAT_DEPLOYMENT"],
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\n"
                        f"Source passages:\n{context}"
                    )
                }
            ]
        )

        return (
            response.choices[0].message.content or "",
            "azure"
        )

    raise HTTPException(
        500,
        "Unsupported AI provider."
    )


# ---------------- HOME ROUTE ----------------

@app.get(
    "/",
    response_class=HTMLResponse
)
def home():

    with open(
        "app/static/index.html",
        encoding="utf-8"
    ) as file:

        return file.read()


# ---------------- STATUS ROUTE ----------------

@app.get("/api/status")
def status():

    labels = {
        "demo": "DEMO MODE",
        "ollama": "LOCAL OLLAMA AI",
        "ollama_cloud": "OLLAMA CLOUD AI",
        "hybrid": "HYBRID OLLAMA AI",
        "azure": "AZURE AI",
        "gemini": "GEMINI + OLLAMA FALLBACK"
    }

    models = {
        "ollama": os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5:3b"
        ),
        "hybrid": os.getenv(
            "OLLAMA_MODEL",
            "qwen2.5:3b"
        ),
        "ollama_cloud": os.getenv(
            "OLLAMA_CLOUD_MODEL"
        ),
        "gemini": os.getenv(
            "GEMINI_MODEL",
            "gemini-3.8-flash"
        )
    }

    return {
        "mode": APP_MODE,
        "label": labels[APP_MODE],
        "model": models.get(APP_MODE),
        "fallback": (
            os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
            if APP_MODE == "gemini"
            else None
        )
    }


# ---------------- DOCUMENT UPLOAD ROUTE ----------------

@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...)
):

    filename = os.path.basename(
        file.filename or "upload"
    )

    raw = await file.read()

    if not raw:

        raise HTTPException(
            400,
            "The uploaded file is empty."
        )

    pages = parse_document(
        filename,
        raw
    )

    chunks = make_chunks(
        filename,
        pages
    )

    if not chunks:

        raise HTTPException(
            400,
            "No extractable text found in this document."
        )

    if APP_MODE == "azure":

        azure_index_chunks(
            chunks,
            raw,
            filename
        )

    else:

        if APP_MODE != "demo":

            try:

                embeddings = create_embeddings(
                    [
                        chunk["content"]
                        for chunk in chunks
                    ],
                    task_type="RETRIEVAL_DOCUMENT"
                )

                for chunk, embedding in zip(
                    chunks,
                    embeddings
                ):

                    chunk["embedding"] = embedding

            except Exception as exc:

                raise HTTPException(
                    502,
                    f"Could not generate document embeddings: {exc}"
                )

        documents.extend(chunks)

    return {
        "filename": filename,
        "pages": len(pages),
        "chunks": len(chunks),
        "message": (
            f"Added {filename} "
            f"({len(chunks)} searchable chunks)."
        )
    }


# ---------------- QUESTION MODEL ----------------

class Question(BaseModel):

    question: str


# ---------------- ASK QUESTION ROUTE ----------------

@app.post("/api/ask")
def ask(body: Question):

    question = body.question.strip()

    if not question:

        raise HTTPException(
            400,
            "Enter a question."
        )

    if APP_MODE == "azure":

        search, _, _ = azure_clients()

        results = list(
            search.search(
                search_text=question,
                top=5,
                select=[
                    "content",
                    "filename",
                    "page",
                    "blob_name"
                ]
            )
        )

        hits = [
            {
                "filename": result.get(
                    "filename",
                    "document"
                ),
                "page": result.get(
                    "page",
                    1
                ),
                "content": result.get(
                    "content",
                    ""
                )
            }
            for result in results
        ]

    elif APP_MODE == "demo":

        hits = demo_retrieve(
            question,
            limit=5
        )

    else:

        try:

            hits = semantic_retrieve(
                question,
                limit=5
            )

        except Exception as exc:

            raise HTTPException(
                502,
                f"Semantic retrieval failed: {exc}"
            )

    if not hits:

        return {
            "answer": (
                "I couldn't find relevant passages in the uploaded "
                "documents. Try another question or upload the "
                "relevant document."
            ),
            "citations": [],
            "mode": APP_MODE
        }

    citations = [
        {
            "filename": hit["filename"],
            "page": hit["page"],
            "passage": hit["content"]
        }
        for hit in hits[:5]
    ]

    if APP_MODE == "demo":

        passages = [
            f"[{i + 1}] {hit['content']}"
            for i, hit in enumerate(hits[:3])
        ]

        return {
            "answer": (
                "DEMO MODE — this is not an AI-generated answer.\n\n"
                + "\n\n".join(passages)
            ),
            "citations": citations[:3],
            "mode": "demo"
        }

    context = build_context(
        hits[:5]
    )

    answer, provider = generate_answer(
        question,
        context
    )

    return {
        "answer": answer,
        "citations": citations,
        "mode": provider
    }
