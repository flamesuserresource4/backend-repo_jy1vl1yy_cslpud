import os
from typing import List, Optional, Any, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from bson import ObjectId

from database import db, create_document, get_documents

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------- Helpers ---------
class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if isinstance(v, ObjectId):
            return v
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)

def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            out[k] = str(v)
        else:
            out[k] = v
    return out


# --------- Schemas ---------
class ConversationCreate(BaseModel):
    title: str = Field(..., description="Conversation title")
    created_by: Optional[str] = Field(None)

class ConversationOut(BaseModel):
    id: str
    title: str
    created_by: Optional[str] = None

class MessageCreate(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str

class SendMessageRequest(BaseModel):
    content: str

class MessageOut(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str


# --------- Base routes ---------
@app.get("/")
def read_root():
    return {"message": "AI Chat Backend is running"}

@app.get("/api/hello")
def hello():
    return {"message": "Hello from the backend API!"}


# --------- Conversation routes ---------
@app.post("/api/conversations", response_model=ConversationOut)
def create_conversation(payload: ConversationCreate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    data = payload.model_dump()
    inserted_id = create_document("conversation", data)
    return {"id": inserted_id, **data}

@app.get("/api/conversations", response_model=List[ConversationOut])
def list_conversations():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    docs = get_documents("conversation", {}, limit=100)
    out = []
    for d in docs:
        d = serialize_doc(d)
        out.append({"id": d.get("_id"), "title": d.get("title"), "created_by": d.get("created_by")})
    # newest first
    out.sort(key=lambda x: x["id"], reverse=True)
    return out


# --------- Message routes ---------
@app.get("/api/conversations/{conversation_id}/messages", response_model=List[MessageOut])
def get_messages(conversation_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation id")
    docs = db["message"].find({"conversation_id": conversation_id}).sort("created_at", 1)
    out: List[MessageOut] = []
    for d in docs:
        d = serialize_doc(d)
        out.append(
            {
                "id": d.get("_id"),
                "conversation_id": d.get("conversation_id"),
                "role": d.get("role"),
                "content": d.get("content"),
            }
        )
    return out

@app.post("/api/conversations/{conversation_id}/messages", response_model=MessageOut)
def add_message(conversation_id: str, payload: MessageCreate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation id")

    # Ensure conversation exists
    conv = db["conversation"].find_one({"_id": ObjectId(conversation_id)})
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg_data = payload.model_dump()
    msg_data["conversation_id"] = conversation_id
    inserted_id = create_document("message", msg_data)

    return {
        "id": inserted_id,
        "conversation_id": conversation_id,
        "role": payload.role,
        "content": payload.content,
    }


# --------- Chat endpoint that generates assistant reply ---------
@app.post("/api/conversations/{conversation_id}/send", response_model=MessageOut)
def send_message(conversation_id: str, req: SendMessageRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if not ObjectId.is_valid(conversation_id):
        raise HTTPException(status_code=400, detail="Invalid conversation id")

    # store user message
    user_msg = MessageCreate(role="user", content=req.content)
    _ = add_message(conversation_id, user_msg)

    # generate assistant reply (simple built-in bot)
    reply_text = generate_ai_reply(req.content)

    assistant_msg = MessageCreate(role="assistant", content=reply_text)
    created = add_message(conversation_id, assistant_msg)
    return created


# --------- Simple AI reply generator (no external API) ---------
def generate_ai_reply(prompt: str) -> str:
    prompt = (prompt or "").strip()
    if not prompt:
        return "I'm here! Ask me anything."

    # Simple heuristics
    lower = prompt.lower()
    if any(g in lower for g in ["hello", "hi", "hey"]):
        return "Hey there! How can I help you today?"
    if lower.startswith("/help") or "help" in lower:
        return "I can answer questions, summarize, or brainstorm ideas. Just type your message!"
    if lower.startswith("/summarize"):
        text = prompt[len("/summarize"):].strip()
        return f"Summary: {summarize(text)}"
    if lower.startswith("/todo"):
        items = [i.strip() for i in prompt.split(" ")[1:]]
        bullets = "\n".join(f"• {i}" for i in items if i)
        return f"Here’s your checklist:\n{bullets}" if bullets else "Provide items after /todo"

    # default: reflective response
    return f"You said: '{prompt}'. Here's a helpful thought: {reflect(prompt)}"


def summarize(text: str) -> str:
    # very light 'summary'
    words = text.split()
    if len(words) <= 12:
        return text
    return " ".join(words[:12]) + " …"


def reflect(text: str) -> str:
    # simple reflection by extracting key words
    words = [w.strip('.,!?') for w in text.split()]
    key = ", ".join(sorted(set([w for w in words if len(w) > 5]))[:6])
    if key:
        return f"key points → {key}"
    return "sounds interesting!"


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
