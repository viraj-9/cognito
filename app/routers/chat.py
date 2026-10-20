"""Assistant endpoint for provider-backed natural language conversation."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from typing import Literal
from ..security import limit_request
from ..services.chat import respond, generate_reply
from ..services.provider import ProviderUnavailable
from ..settings import settings

router=APIRouter(prefix="/api",tags=["assistant"])


class ChatHistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=2000)


class ChatBody(BaseModel):
    message: str = Field(min_length=1,max_length=2000)
    language: str = Field(default="auto",pattern="^(auto|en|es|fr|hi|hinglish|mr)$")
    history: list[ChatHistoryMessage] = Field(default_factory=list, max_length=16)


@router.post("/chat")
def chat_endpoint(body:ChatBody,request:Request):
    limit_request(request,"chat",30,60)
    if not settings.provider_configured:
        raise HTTPException(
            503,
            "Live AI chat is not connected. Check the LINGUAAI_AI_BASE_URL, LINGUAAI_AI_API_KEY, and LINGUAAI_AI_MODEL values in the project-root .env file, then fully restart the server. The learning library still works without an AI key.",
        )
    model=getattr(request.app.state,"language_model",None)
    preferred=body.language if body.language!="auto" else "auto"
    try:
        info=respond(body.message,preferred,model)
        effective_language=info["language"] if preferred=="auto" else preferred
        history=[item.model_dump() for item in body.history]
        reply=generate_reply(body.message,effective_language,info["resources"],history=history)
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from None
    except ProviderUnavailable as exc:
        raise HTTPException(503,str(exc)) from None
    return {**info,"reply":reply,"tutor_available":True}
