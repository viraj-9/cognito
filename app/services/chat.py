"""Chat orchestration. Provider errors are explicit; offline help uses the catalogue."""
from .language_model import predict_language
from .provider import tutoring_reply
from .resources import search_resources
from .text import LANGUAGE_NAMES, clean_text


def respond(message: str, preferred_language: str, model: dict | None) -> dict:
    text=clean_text(message,2000)
    if not text:
        raise ValueError("Write a message before sending it.")
    language=preferred_language if preferred_language in LANGUAGE_NAMES and preferred_language!="unknown" else None
    detected=predict_language(text,model)
    response_language=language or detected["language"]
    found=search_resources(text,limit=3)
    if response_language=="unknown": response_language="en"
    if found:
        return {"reply":None,"language":response_language,"language_name":LANGUAGE_NAMES[response_language],"topic":found[0]["title"],"resources":found,"tutor_available":True}
    return {"reply":None,"language":response_language,"language_name":LANGUAGE_NAMES[response_language],"topic":None,"resources":[],"tutor_available":True}


def generate_reply(message: str, response_language: str, context: list[dict], history: list[dict] | None = None) -> str:
    return tutoring_reply(message,response_language,context,history=history)


