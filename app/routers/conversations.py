"""Private, user-owned conversation practice sessions."""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..database import database
from ..security import limit_request, require_csrf, require_user
from ..services.provider import ProviderUnavailable, conversation_feedback, conversation_reply, generate_performance_report
from ..services.text import clean_text
from ..settings import settings

router = APIRouter(prefix="/api/conversations", tags=["conversation practice"])
MODES = {"casual", "interview", "interview_hr", "interview_tech", "mock_interview", "english", "roleplay"}
LEVELS = {"beginner", "intermediate", "advanced"}

EDUCATION_LEVEL_LABELS = {
    "secondary": "Class 10 / secondary school",
    "higher_secondary": "Class 12 / higher secondary",
    "diploma": "Diploma",
    "undergraduate": "Undergraduate / currently pursuing a degree",
    "graduate": "Graduate / bachelor’s degree completed",
    "postgraduate": "Postgraduate / master’s degree",
    "other": "Other education or career transition",
}
EDUCATION_FIELD_LABELS = {
    "computer": "Computer Engineering / Computer Science / IT / AI",
    "engineering": "Other Engineering",
    "business": "Business / Management",
    "commerce": "Commerce / Accounting / Finance",
    "arts": "Arts / Humanities / Media",
    "science": "Science / Mathematics",
    "healthcare": "Healthcare / Life Sciences",
    "general": "General / Other",
}


def _scenario_for_options(options: dict) -> str:
    """Provide education context to the mock interviewer without trusting display text."""
    scenario = clean_text(options.get("scenario", ""), 180)
    if options.get("mode") != "mock_interview":
        return scenario
    level = EDUCATION_LEVEL_LABELS.get(options.get("education_level", ""), "Education not specified")
    field = EDUCATION_FIELD_LABELS.get(options.get("education_field", ""), "Field not specified")
    education_context = f"Candidate education: {level}; field of study: {field}."
    return f"{education_context} {scenario}".strip()


class StartBody(BaseModel):
    mode: str = Field(pattern="^(casual|interview|interview_hr|interview_tech|mock_interview|english|roleplay)$")
    level: str = Field(default="intermediate", pattern="^(beginner|intermediate|advanced)$")
    language: str = Field(default="en", pattern="^(en|es|fr|hi|hinglish|mr)$")
    role: str = Field(default="", max_length=100)
    scenario: str = Field(default="", max_length=180)
    education_level: str = Field(default="", max_length=30)
    education_field: str = Field(default="", max_length=50)
    corrections: bool = True


class TurnBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class VoiceMetrics(BaseModel):
    """Small client-computed acoustic summary; raw audio is never uploaded or stored."""
    capture_available: bool = False
    listening_seconds: float = Field(default=0, ge=0, le=3600)
    speech_seconds: float = Field(default=0, ge=0, le=3600)
    silence_ratio: float | None = Field(default=None, ge=0, le=1)
    pitch_median_hz: float | None = Field(default=None, ge=50, le=600)
    pitch_variation: float | None = Field(default=None, ge=0, le=2)
    volume_variation: float | None = Field(default=None, ge=0, le=5)
    word_count: int = Field(default=0, ge=0, le=100000)
    response_count: int = Field(default=0, ge=0, le=1000)


class EndBody(BaseModel):
    voice_metrics: VoiceMetrics | None = None


_FALLBACK_FIRST_QUESTIONS = {
    "casual": "What is something interesting you did recently?",
    "english": "Let's warm up. Could you tell me a little about yourself and your daily routine?",
    "interview": "Please introduce yourself and tell me why this role interests you.",
    "interview_hr": "Tell me about yourself and one strength you would bring to a team.",
    "interview_tech": "What technical project have you worked on recently, and what problem did it solve?",
    "mock_interview": "To begin, please introduce yourself and explain why you are interested in this role.",
    "roleplay": "Let's begin. What would you like help with in this situation?",
}
_FALLBACK_FOLLOW_UPS = {
    "casual": ["That sounds interesting. What did you enjoy most about it?", "What would you like to try next, and why?", "How would you describe that experience to a friend?"],
    "english": ["Thanks for sharing. Could you describe one specific example?", "What was challenging about that, and how did you handle it?", "How would you explain that in two or three clear steps?"],
    "interview": ["What specific actions did you take, and what was the outcome?", "What was the biggest challenge, and how did you handle it?", "What did you learn, and what would you do differently next time?"],
    "interview_hr": ["Tell me about a time you handled a disagreement with a teammate.", "What was your specific contribution, and what was the result?", "What did that experience teach you about working with others?"],
    "interview_tech": ["Walk me through your approach. What trade-offs did you consider?", "What is the time and space complexity of your approach?", "How would your solution change if the input became much larger?"],
    "mock_interview": ["Tell me about a project, course, or practical experience relevant to this role.", "How would you approach a task you have not handled before?", "Describe a challenge you faced while learning or working and how you handled it.", "What skills are you currently improving for this role?"],
    "roleplay": ["What is the most important detail I should know before helping?", "What outcome would make this situation successful for you?", "What would you like to say next in this scenario?"],
}


def _mock_interview_followups(role: str) -> list[str]:
    """Give a useful role-relevant fallback interview when the live provider is unavailable."""
    name = role.casefold()
    if any(term in name for term in ("web developer", "frontend", "front-end", "backend", "back-end", "full-stack", "software developer", "software engineer")):
        return [
            "Walk me through a website or app you have built. Which part did you implement yourself?",
            "How would you debug a page that looks correct on your laptop but breaks on a mobile screen?",
            "What is the difference between frontend and backend, and how have you used each in a project?",
            "How would you investigate a button that appears clickable but does nothing?",
        ]
    if any(term in name for term in ("data analyst", "data scientist", "business intelligence", "research analyst")):
        return [
            "How would you handle missing or duplicate values before analyzing a dataset?",
            "Which charts would you choose to compare trends across categories, and why?",
            "Describe a project where you used Excel, SQL, Python, or a dashboard to find a useful insight.",
            "How would you explain an unexpected result in your analysis to a non-technical stakeholder?",
        ]
    if any(term in name for term in ("cybersecurity", "security analyst", "network support", "network engineer", "cloud engineer", "it support")):
        return [
            "A user reports they cannot access a service. How would you troubleshoot it step by step?",
            "What basic checks would you perform before escalating a security or network incident?",
            "How would you explain least privilege and why it matters?",
            "Tell me how you would document an issue so another engineer can continue the investigation.",
        ]
    if any(term in name for term in ("account", "finance", "audit", "financial", "banking")):
        return [
            "How would you check a transaction that does not match the supporting records?",
            "Why is attention to detail important when handling financial information?",
            "How would you explain a discrepancy to a colleague or customer professionally?",
            "What spreadsheet checks or formulas have you used to validate figures?",
        ]
    if any(term in name for term in ("marketing", "content", "social media", "communications", "public relations")):
        return [
            "How would you adapt a message for two different audiences?",
            "Which metrics would you use to judge whether a campaign or post was successful?",
            "How would you respond if a campaign was not meeting its goal?",
            "Show how you would turn a complicated idea into a clear, short message.",
        ]
    if any(term in name for term in ("hr", "human resources", "recruit", "talent")):
        return [
            "How would you help a candidate who is nervous during an interview?",
            "How would you handle confidential employee information?",
            "Describe how you would organize several tasks with the same deadline.",
            "How would you respond to a disagreement between two team members?",
        ]
    if any(term in name for term in ("sales", "customer support", "customer success", "customer service")):
        return [
            "How would you respond to an upset customer while staying professional?",
            "How would you explain a product or service to someone unfamiliar with it?",
            "What would you do if you did not know the answer to a customer's question?",
            "How would you prioritize several customer requests arriving at the same time?",
        ]
    if any(term in name for term in ("engineer", "technician", "quality", "production", "embedded", "design engineer")):
        return [
            "Describe a technical project or practical task you have completed and your contribution.",
            "How would you investigate a fault when the first test does not reveal the cause?",
            "What checks would you make before handing a technical task over to a teammate?",
            "Tell me about a time you had to learn a tool or process quickly.",
        ]
    return [
        "Tell me about a project, course, or practical experience relevant to this role.",
        "How would you approach a task you have not handled before?",
        "Describe a challenge you faced while learning or working and how you handled it.",
        "What skills are you currently improving for this role?",
    ]


def _offline_reply(messages: list[dict], *, mode: str, language: str, level: str, role: str = "", scenario: str = "") -> str:
    """Transparent, deterministic practice partner used only when the live AI provider fails."""
    count = sum(1 for item in messages if item.get("role") == "user")
    first = _FALLBACK_FIRST_QUESTIONS.get(mode, _FALLBACK_FIRST_QUESTIONS["english"])
    localized_first = {
        "hi": {
            "casual": "हाल ही में आपने कौन-सा दिलचस्प काम किया?",
            "english": "चलिए शुरू करते हैं। अपना परिचय दीजिए और अपनी दिनचर्या के बारे में बताइए।",
            "interview": "कृपया अपना परिचय दीजिए और बताइए कि इस भूमिका में आपकी रुचि क्यों है।",
            "interview_hr": "अपना परिचय दीजिए और टीम के लिए अपनी एक ताकत बताइए।",
            "interview_tech": "आपने हाल ही में कौन-सा तकनीकी प्रोजेक्ट बनाया और उसने कौन-सी समस्या हल की?",
            "mock_interview": "कृपया अपना परिचय दीजिए और बताइए कि आप इस भूमिका में क्यों रुचि रखते हैं।",
            "roleplay": "चलिए शुरू करते हैं। इस स्थिति में आपको किस तरह की मदद चाहिए?",
        },
        "es": {
            "casual": "¿Qué cosa interesante hiciste hace poco?",
            "english": "Vamos a empezar. ¿Puedes contarme algo sobre ti y tu rutina diaria?",
            "interview": "Preséntate y cuéntame por qué te interesa este puesto.",
            "interview_hr": "Háblame de ti y de una fortaleza que aportarías al equipo.",
            "interview_tech": "¿En qué proyecto técnico trabajaste recientemente y qué problema resolvió?",
            "mock_interview": "Preséntate y cuéntame por qué te interesa este puesto.",
            "roleplay": "Empecemos. ¿Qué tipo de ayuda necesitas en esta situación?",
        },
        "fr": {
            "casual": "Quelle chose intéressante as-tu faite récemment ?",
            "english": "Commençons. Peux-tu te présenter et parler de ta routine quotidienne ?",
            "interview": "Présente-toi et explique pourquoi ce poste t’intéresse.",
            "interview_hr": "Parle-moi de toi et d’un point fort que tu apporterais à l’équipe.",
            "interview_tech": "Sur quel projet technique as-tu travaillé récemment et quel problème résolvait-il ?",
            "mock_interview": "Présente-toi et explique pourquoi ce poste t’intéresse.",
            "roleplay": "Commençons. De quelle aide as-tu besoin dans cette situation ?",
        },
    }
    localized_first["hinglish"] = {
        "casual": "Hi, main Cognito hoon. Aajkal aapne kya interesting kiya?",
        "english": "Hi, main Cognito hoon. Chaliye warm up karte hain. Apne baare mein thoda batao.",
        "interview": "Hi, main Cognito hoon. Chaliye interview start karte hain. Pehle apna introduction dijiye.",
        "interview_hr": "Hi, main Cognito hoon. Chaliye HR interview start karte hain. Apne baare mein bataiye.",
        "interview_tech": "Hi, main Cognito hoon. Chaliye technical interview start karte hain. Aapne recently kaunsa technical project banaya?",
        "mock_interview": "Hi, main Cognito hoon. Welcome to your mock interview. Pehle apna introduction dijiye aur batayein ki aap is role mein kyun interested hain.",
        "roleplay": "Hi, main Cognito hoon. Chaliye role-play start karte hain. Aap is situation mein kya karna chahenge?",
    }
    localized_first["mr"] = {
        "casual": "नमस्कार, मी Cognito आहे. अलीकडे तुम्ही कोणते मजेदार किंवा महत्त्वाचे काम केले?",
        "english": "नमस्कार, मी Cognito आहे. चला सुरुवात करूया. तुमच्याबद्दल थोडे सांगा.",
        "interview": "नमस्कार, मी Cognito आहे. चला मुलाखत सुरू करूया. आधी तुमची ओळख करून द्या.",
        "interview_hr": "नमस्कार, मी Cognito आहे. चला HR मुलाखत सुरू करूया. तुमच्याबद्दल सांगा.",
        "interview_tech": "नमस्कार, मी Cognito आहे. चला technical interview सुरू करूया. तुम्ही अलीकडे कोणता technical project केला?",
        "mock_interview": "नमस्कार, मी Cognito आहे. तुमच्या mock interview मध्ये स्वागत आहे. आधी तुमची ओळख करून द्या आणि या role मध्ये तुम्हाला का interest आहे ते सांगा.",
        "roleplay": "नमस्कार, मी Cognito आहे. चला role-play सुरू करूया. या situation मध्ये तुम्हाला काय करायचे आहे?",
    }
    localized_followups = {
        "hinglish": ["Achha, samjha. Kya aap ek specific example de sakte ho?", "Us situation mein sabse bada challenge kya tha aur aapne use kaise handle kiya?", "Aapne us experience se kya seekha, aur next time kya different karoge?"],
        "mr": ["धन्यवाद. तुम्ही एखादे specific example सांगू शकता का?", "त्या situation मधील सर्वात मोठे challenge काय होते आणि तुम्ही ते कसे handle केले?", "त्या अनुभवातून तुम्ही काय शिकलात आणि पुढच्या वेळी काय वेगळे कराल?"],
        "hi": ["धन्यवाद। क्या आप कोई खास उदाहरण दे सकते हैं?", "उसमें सबसे बड़ी चुनौती क्या थी और आपने उसे कैसे संभाला?", "आपने उससे क्या सीखा और अगली बार क्या अलग करेंगे?"],
        "es": ["Gracias. ¿Puedes dar un ejemplo concreto?", "¿Cuál fue el mayor reto y cómo lo resolviste?", "¿Qué aprendiste y qué harías diferente la próxima vez?"],
        "fr": ["Merci. Peux-tu donner un exemple concret ?", "Quel a été le plus grand défi et comment l’as-tu géré ?", "Qu’as-tu appris et que ferais-tu différemment la prochaine fois ?"],
    }
    if count == 0:
        if language in localized_first:
            return localized_first[language].get(mode, localized_first[language]["english"])
        if mode in {"interview", "interview_hr", "interview_tech", "mock_interview"}:
            target = role.strip() or "the role"
            intro = f"Hi, I'm Cognito. Welcome to your mock interview for {target}. " if mode == "mock_interview" else f"Hi, I'm Cognito. Welcome to your practice interview for {target}. "
        elif mode == "roleplay":
            intro = f"Hi, I'm Cognito. Welcome to this role-play: {scenario.strip() or 'everyday conversation'}. "
        else:
            intro = "Hi, I'm Cognito. Welcome to voice practice. "
        return intro + first
    if mode == "mock_interview" and language == "en":
        options = _mock_interview_followups(role)
        transitions = ["I see.", "That helps me understand.", "Got it.", "Thanks for explaining.", "Let's go a little deeper."]
        return f"{transitions[(count - 1) % len(transitions)]} {options[(count - 1) % len(options)]}"
    if language in localized_followups:
        options = localized_followups[language]
        return options[(count - 1) % len(options)]
    followups = _FALLBACK_FOLLOW_UPS.get(mode, _FALLBACK_FOLLOW_UPS["english"])
    next_question = followups[(count - 1) % len(followups)]
    transitions = ["I see.", "That helps me understand.", "Got it.", "Thanks for explaining.", "Let's explore that a little more."]
    transition = transitions[(count - 1) % len(transitions)]
    return f"{transition} {next_question}"


def _offline_performance_report(messages: list[dict], *, mode: str, level: str, role: str = "", scenario: str = "", session_id: str = "", voice_metrics: dict | None = None) -> dict:
    """Generate a clearly labelled, low-confidence transcript heuristic report without provider access."""
    answers = [item.get("content", "").strip() for item in messages if item.get("role") == "user" and isinstance(item.get("content"), str) and item.get("content", "").strip()]
    word_counts = [len(answer.split()) for answer in answers]
    response_count = len(answers)
    average_words = round(sum(word_counts) / response_count, 1) if response_count else 0
    enough = response_count >= 3
    score = min(90, max(50, 48 + min(21, response_count * 4) + min(18, int(average_words)))) if enough else None
    categories = []
    if response_count:
        detail_score = min(95, max(35, 35 + min(60, int(average_words * 4))))
        categories = [
            {"name": "Response detail (text estimate)", "score": detail_score,
             "explanation": f"Your {response_count} response(s) averaged {average_words} words. This measures answer length, not correctness or speaking delivery.",
             "suggestion": "For interview answers, add a specific example and explain the result."},
            {"name": "Practice consistency", "score": min(100, response_count * 20),
             "explanation": f"You completed {response_count} response(s) in this session.",
             "suggestion": "Try to complete at least five answers in a session."},
        ]
    strengths = []
    if response_count:
        strengths.append({"title": "You took part in the practice", "explanation": f"You gave {response_count} answer(s), creating material to review and improve.", "example": answers[0][:220], "why_effective": "Practising aloud regularly helps you build the habit of answering without preparation."})
    if average_words >= 8:
        strengths.append({"title": "You added detail to your answers", "explanation": "Your answers were more than one or two words on average.", "example": max(answers, key=lambda answer: len(answer.split()))[:220], "why_effective": "Specific detail makes it easier for a listener to understand your point."})
    improvements = []
    if response_count < 3:
        improvements.append({"issue": "More practice is needed for a useful pattern", "excerpt": None, "improved_version": None, "why_better": "This report is intentionally not scored because there are fewer than three answers.", "exercise": "Complete at least five interview or conversation responses, then generate a new report."})
    if response_count and average_words < 8:
        improvements.append({"issue": "Add one concrete detail to each answer", "excerpt": answers[-1][:220], "improved_version": None, "why_better": "A brief example or reason can make the response easier to follow.", "exercise": "Try: point → one example → result or reason."})
    if mode.startswith("interview") or mode == "mock_interview":
        improvements.append({"issue": "Structure interview answers", "excerpt": None, "improved_version": None, "why_better": "A clear situation, action, and result makes experience easier to evaluate.", "exercise": "For a behavioural question, practise the STAR structure: Situation, Task, Action, Result."})
    else:
        improvements.append({"issue": "Build on each answer", "excerpt": None, "improved_version": None, "why_better": "Adding a reason, example, or follow-up detail helps keep conversation natural.", "exercise": "Answer the next question in three sentences: point, detail, and why it matters."})
    now = datetime.now(timezone.utc).isoformat()
    limitations = "Offline report: the AI provider was unavailable, so scores and tips are basic transcript heuristics. Voice recordings are not stored. Pronunciation, accent, and grammar accuracy are not measured by the offline fallback. If sufficient local microphone metrics were captured, the separate spoken-confidence panel provides a rough pace, pause, tone-variation and volume-steadiness estimate. Use a working AI provider for a detailed evaluator report."
    if not enough:
        limitations = f"Only {response_count} answer(s) were captured. At least three are recommended. " + limitations
    return {
        "session_id": session_id, "session_type": mode, "overall_score": score,
        "score_summary": (f"Offline estimate based on {response_count} transcript responses; use this as practice guidance, not a formal assessment." if enough else "Not enough answers to assign a meaningful overall score."),
        "category_scores": categories, "strengths": strengths, "improvements": improvements,
        "answer_rewrites": [], "recommended_next_practice": {"mode": mode, "focus": "Answer structure and adding concrete details", "reason": "Repeat the practice with a few more answers and compare your response detail."},
        "spoken_confidence": _spoken_confidence_report(messages, voice_metrics=voice_metrics),
        "evidence_limitations": limitations, "rubric_version": "offline-1.1", "generated_at": now,
        "insufficient_evidence": not enough,
    }


def _safe_metric(value, low=0.0, high=100000.0):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return max(low, min(high, number))


def _spoken_confidence_report(messages: list[dict], *, voice_metrics: dict | None = None, ai_spoken_skills: dict | None = None) -> dict:
    """Report transcript clarity separately from rough local acoustic delivery estimates."""
    import re
    answers = [m.get("content", "").strip() for m in messages if m.get("role") == "user" and isinstance(m.get("content"), str) and m.get("content", "").strip()]
    words = [word for answer in answers for word in answer.split()]
    average_words = len(words) / len(answers) if answers else 0.0
    short_ratio = sum(1 for answer in answers if len(answer.split()) < 5) / len(answers) if answers else 1.0
    fillers = sum(len(re.findall(r"\b(?:um+|uh+|erm|like|you know|basically)\b", answer.casefold())) for answer in answers)

    ai_score = ai_spoken_skills.get("clarity_fluency_score") if isinstance(ai_spoken_skills, dict) else None
    if isinstance(ai_score, (int, float)) and ai_score == ai_score:
        clarity_score = max(0, min(100, int(ai_score)))
        clarity_summary = str(ai_spoken_skills.get("explanation") or "AI feedback from your transcribed answers; pronunciation cannot be verified from text.")[:500]
        clarity_strengths = [x[:220] for x in ai_spoken_skills.get("strengths", [])[:4] if isinstance(x, str)] if isinstance(ai_spoken_skills.get("strengths"), list) else []
        clarity_tips = [x[:220] for x in ai_spoken_skills.get("improvements", [])[:4] if isinstance(x, str)] if isinstance(ai_spoken_skills.get("improvements"), list) else []
    else:
        clarity_score = max(30, min(92, int(53 + min(22, average_words * 1.3) + min(10, len(answers) * 2) - short_ratio * 12 - fillers * 2))) if answers else None
        clarity_summary = f"Transcript estimate from {len(answers)} answer(s); it evaluates answer development and text patterns, not pronunciation. Speech recognition may omit words or fillers." if answers else "No answer transcript was available to evaluate clarity and fluency."
        clarity_strengths = (["You added enough detail to review your ideas."] if average_words >= 10 else ["You completed practice and created answers to review."]) if answers else []
        clarity_tips = (["Try a simple point → example → result structure."] if short_ratio >= .5 else ["Pause briefly between ideas and finish each point clearly."]) if answers else ["Answer a few practice questions to receive useful feedback."]

    metrics = voice_metrics if isinstance(voice_metrics, dict) else {}
    captured = bool(metrics.get("capture_available"))
    listening_s = _safe_metric(metrics.get("listening_seconds"), 0, 3600) or 0.0
    speech_s = _safe_metric(metrics.get("speech_seconds"), 0, 3600) or 0.0
    silence_ratio = _safe_metric(metrics.get("silence_ratio"), 0, 1)
    pitch_variation = _safe_metric(metrics.get("pitch_variation"), 0, 2)
    volume_variation = _safe_metric(metrics.get("volume_variation"), 0, 5)
    word_count = int(_safe_metric(metrics.get("word_count"), 0, 100000) or 0)
    usable_audio = captured and listening_s >= 2 and speech_s >= 1.5 and word_count >= 3
    pace = round(word_count / listening_s * 60) if usable_audio and listening_s else None
    components = []
    if pace is not None:
        pace_score = max(45, min(100, int(100 - max(0, 95 - pace) * .42 - max(0, pace - 175) * .45)))
        components.append((pace_score, .30))
    else:
        pace_score = None
    if silence_ratio is not None and usable_audio:
        pause_score = max(45, min(100, int(100 - abs(silence_ratio - .30) * 85)))
        components.append((pause_score, .25))
    else:
        pause_score = None
    if pitch_variation is not None and usable_audio:
        tone_score = max(55, min(96, int(96 - abs(pitch_variation - .12) * 220)))
        components.append((tone_score, .25))
    else:
        tone_score = None
    if volume_variation is not None and usable_audio:
        volume_score = max(55, min(96, int(96 - abs(volume_variation - .42) * 55)))
        components.append((volume_score, .20))
    else:
        volume_score = None
    acoustic_score = round(sum(score * weight for score, weight in components) / sum(weight for _, weight in components)) if components and usable_audio else None
    confidence_score = round(acoustic_score * .68 + clarity_score * .32) if acoustic_score is not None and clarity_score is not None else None
    if confidence_score is None:
        label = "Voice sample unavailable"
        summary = "Spoken-confidence score was not measured because the browser did not capture enough usable microphone data. Transcript clarity is shown separately."
    elif confidence_score >= 80:
        label = "Confident delivery estimate"
        summary = "Your measured pace, pauses and vocal variation align with a generally steady delivery. This is coaching feedback, not a judgment about your personality or inner confidence."
    elif confidence_score >= 65:
        label = "Building delivery confidence"
        summary = "Your delivery shows some helpful patterns, with room to make pace, pauses or vocal variation more consistent."
    else:
        label = "Delivery to practise"
        summary = "Try a measured pace, brief pauses between points and a clear finish to each answer. Microphone and room conditions can affect these estimates."
    voice_strengths, suggestions = [], []
    if pace is not None:
        if 95 <= pace <= 175:
            voice_strengths.append("Your estimated speaking pace is within a practical conversational range.")
        elif pace < 95:
            suggestions.append("Try joining short phrases more smoothly and keeping a steady rhythm.")
        else:
            suggestions.append("Slow slightly at key points so the listener can follow your ideas.")
    if tone_score is not None and tone_score >= 78:
        voice_strengths.append("Your pitch variation suggests some vocal modulation rather than a completely flat delivery.")
    elif tone_score is not None:
        suggestions.append("Emphasize key words naturally; do not force a different pitch or accent.")
    if pause_score is not None and pause_score < 78:
        suggestions.append("Use short pauses between ideas; leave enough room to think without long gaps.")
    if not suggestions:
        suggestions.append("Keep practising answers aloud and compare estimates across longer sessions.")
    limitations = "Voice delivery is estimated from locally calculated timing, pitch-variation and volume-variation summaries plus the transcript. Cognito does not upload or store raw microphone recordings; the browser speech-recognition service may process audio separately. Room noise, microphone distance, device processing and speech recognition can affect this estimate; it is not a psychological or clinical confidence test."
    if not usable_audio:
        limitations = "No reliable voice-tone score was available for this session. Transcript clarity is text-only feedback. Allow microphone access and answer a few questions aloud for voice scoring. " + limitations
    return {
        "score": confidence_score, "label": label, "summary": summary,
        "clarity_fluency_score": clarity_score,
        "clarity_fluency_summary": clarity_summary,
        "clarity_fluency_strengths": clarity_strengths,
        "clarity_fluency_improvements": clarity_tips,
        "acoustic_delivery_score": acoustic_score,
        "speaking_pace_wpm": pace, "pace_score": pace_score,
        "pause_balance_score": pause_score, "vocal_tone_score": tone_score,
        "volume_steadiness_score": volume_score,
        "voice_sample_available": bool(usable_audio),
        "voice_strengths": voice_strengths, "suggestions": suggestions,
        "limitations": limitations,
    }


def _owned_conversation(conversation_id: str, user_id: int):
    with database() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user_id)).fetchone()
    if row is None:
        raise HTTPException(404, "Conversation session not found.")
    return row


def _public(row, include_messages: bool = False):
    item = {
        "id": row["id"], "mode": row["mode"], "settings": json.loads(row["settings_json"]),
        "status": row["status"], "summary": row["summary"], "created_at": row["created_at"],
        "updated_at": row["updated_at"], "ended_at": row["ended_at"],
        "report_status": row["report_status"] if "report_status" in row.keys() else "none",
    }
    if include_messages:
        item["messages"] = json.loads(row["messages_json"])
    return item


@router.post("", status_code=201)
def start_conversation(body: StartBody, request: Request):
    require_csrf(request)
    user = require_user(request)
    limit_request(request, "conversation_start", 10, 3600)
    session_id = str(uuid.uuid4())
    options = body.model_dump()
    options["role"] = clean_text(options["role"], 100)
    options["scenario"] = clean_text(options["scenario"], 180)
    options["education_level"] = clean_text(options.get("education_level", ""), 30)
    options["education_field"] = clean_text(options.get("education_field", ""), 50)
    if body.mode == "mock_interview" and (options["education_level"] not in EDUCATION_LEVEL_LABELS or options["education_field"] not in EDUCATION_FIELD_LABELS or not options["role"]):
        raise HTTPException(422, "Select your education, field of study, and a matching mock interview role.")
    options["offline_fallback"] = not settings.provider_configured
    options["offline_reason"] = "AI provider is not configured." if not settings.provider_configured else ""
    if options["offline_fallback"]:
        greeting = _offline_reply([], mode=body.mode, language=options["language"], level=options["level"], role=options["role"], scenario=_scenario_for_options(options))
    else:
        try:
            greeting = conversation_reply([], mode=body.mode, language=options["language"], level=options["level"], role=options["role"], scenario=_scenario_for_options(options), corrections=options["corrections"])
        except ProviderUnavailable as exc:
            options["offline_fallback"] = True
            options["offline_reason"] = str(exc)[:400]
            greeting = _offline_reply([], mode=body.mode, language=options["language"], level=options["level"], role=options["role"], scenario=_scenario_for_options(options))
    messages = [{"role": "assistant", "content": greeting}]
    with database() as conn:
        conn.execute("INSERT INTO conversations(id,user_id,mode,settings_json,messages_json) VALUES(?,?,?,?,?)", (session_id, user["user_id"], body.mode, json.dumps(options), json.dumps(messages, ensure_ascii=False)))
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (session_id,)).fetchone()
    return _public(row, include_messages=True)


@router.get("")
def conversation_history(request: Request):
    user = require_user(request)
    with database() as conn:
        rows = conn.execute("SELECT * FROM conversations WHERE user_id=? ORDER BY updated_at DESC LIMIT 30", (user["user_id"],)).fetchall()
    return {"conversations": [_public(row) for row in rows]}


@router.get("/{conversation_id}")
def get_conversation(conversation_id: str, request: Request):
    user = require_user(request)
    return _public(_owned_conversation(conversation_id, user["user_id"]), include_messages=True)


@router.post("/{conversation_id}/messages")
def send_turn(conversation_id: str, body: TurnBody, request: Request):
    require_csrf(request)
    user = require_user(request)
    limit_request(request, "conversation_turn", 24, 60)
    message = clean_text(body.message, 2000)
    if not message:
        raise HTTPException(422, "Write a message before sending it.")
    row = _owned_conversation(conversation_id, user["user_id"])
    if row["status"] != "active":
        raise HTTPException(409, "This session has ended. Start a new practice to continue.")
    options = json.loads(row["settings_json"])
    history = json.loads(row["messages_json"])
    # Keep at most 40 transcript turns in local history and send at most the last 16 messages to the provider.
    next_history = (history + [{"role": "user", "content": message}])[-40:]
    if options.get("offline_fallback"):
        reply = _offline_reply(next_history, mode=row["mode"], language=options["language"], level=options["level"], role=options.get("role", ""), scenario=_scenario_for_options(options))
    else:
        try:
            reply = conversation_reply(next_history, mode=row["mode"], language=options["language"], level=options["level"], role=options["role"], scenario=_scenario_for_options(options), corrections=options["corrections"])
        except ProviderUnavailable as exc:
            options["offline_fallback"] = True
            options["offline_reason"] = str(exc)[:400]
            reply = _offline_reply(next_history, mode=row["mode"], language=options["language"], level=options["level"], role=options.get("role", ""), scenario=_scenario_for_options(options))
    next_history = (next_history + [{"role": "assistant", "content": reply}])[-40:]
    with database() as conn:
        changed = conn.execute("UPDATE conversations SET messages_json=?,settings_json=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=? AND user_id=? AND status='active' AND updated_at=?", (json.dumps(next_history, ensure_ascii=False), json.dumps(options, ensure_ascii=False), conversation_id, user["user_id"], row["updated_at"])).rowcount
        if not changed:
            raise HTTPException(409, "This session changed in another request. Refresh it before continuing.")
    return {"reply": reply, "messages": next_history, "settings": options, "offline_fallback": bool(options.get("offline_fallback"))}


@router.post("/{conversation_id}/end")
def end_conversation(conversation_id: str, request: Request, body: EndBody):
    require_csrf(request)
    user = require_user(request)
    limit_request(request, "conversation_end", 10, 3600)
    row = _owned_conversation(conversation_id, user["user_id"])
    if row["status"] == "completed":
        report = None
        if row["report_status"] == "ready":
            try:
                report = json.loads(row["report_json"]) if row["report_json"] else None
            except (json.JSONDecodeError, TypeError):
                report = None
        return {"session": _public(row, include_messages=True), "feedback": row["summary"], "feedback_available": bool(row["summary"]), "report": report, "report_status": row["report_status"] if "report_status" in row.keys() else "none"}
    history = json.loads(row["messages_json"])
    options = json.loads(row["settings_json"])
    voice_metrics = body.voice_metrics.model_dump() if body.voice_metrics else None
    feedback = None
    report = None
    report_status = "none"
    if options.get("offline_fallback"):
        feedback = "Offline practice completed. Your report uses transcript-level heuristics because the AI provider was unavailable."
        report = _offline_performance_report(history, mode=row["mode"], level=options["level"], role=options.get("role", ""), scenario=_scenario_for_options(options), session_id=conversation_id, voice_metrics=voice_metrics)
        report_status = "ready"
    elif history and settings.provider_configured:
        try:
            feedback = conversation_feedback(history, mode=row["mode"], level=options["level"])
        except ProviderUnavailable:
            feedback = None
        try:
            report = generate_performance_report(
                history, mode=row["mode"], level=options["level"],
                role=options.get("role", ""), scenario=_scenario_for_options(options),
                session_id=conversation_id, voice_metrics=voice_metrics,
            )
            report["spoken_confidence"] = _spoken_confidence_report(history, voice_metrics=voice_metrics, ai_spoken_skills=report.pop("spoken_skills", None))
            report_status = "ready"
        except ProviderUnavailable as exc:
            feedback = feedback or "The AI provider was unavailable at report time. A basic transcript report was generated instead."
            report = _offline_performance_report(history, mode=row["mode"], level=options["level"], role=options.get("role", ""), scenario=_scenario_for_options(options), session_id=conversation_id, voice_metrics=voice_metrics)
            report["evidence_limitations"] = str(exc)[:300] + " " + report["evidence_limitations"]
            report_status = "ready"
    with database() as conn:
        conn.execute(
            "UPDATE conversations SET status='completed',summary=?,report_json=?,report_status=?,"
            "ended_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'),updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id=? AND user_id=? AND status='active'",
            (feedback, json.dumps(report, ensure_ascii=False) if report else None, report_status, conversation_id, user["user_id"]),
        )
        updated = conn.execute("SELECT * FROM conversations WHERE id=? AND user_id=?", (conversation_id, user["user_id"])).fetchone()
    return {"session": _public(updated, include_messages=True), "feedback": feedback, "feedback_available": bool(feedback), "feedback_basis": "text transcript" if feedback else None, "report": report, "report_status": report_status}


@router.post("/{conversation_id}/cancel")
def cancel_conversation(conversation_id: str, request: Request):
    """Cancel an active practice without generating or retaining a performance report."""
    require_csrf(request)
    user = require_user(request)
    limit_request(request, "conversation_cancel", 20, 3600)
    row = _owned_conversation(conversation_id, user["user_id"])
    if row["status"] != "active":
        raise HTTPException(409, "This session is already finished and cannot be cancelled.")
    with database() as conn:
        deleted = conn.execute("DELETE FROM conversations WHERE id=? AND user_id=? AND status='active'", (conversation_id, user["user_id"])).rowcount
    if not deleted:
        raise HTTPException(409, "This session changed before it could be cancelled. Refresh and try again.")
    return {"ok": True, "cancelled": True, "report_generated": False}


@router.get("/{conversation_id}/report")
def get_report(conversation_id: str, request: Request):
    """Retrieve the stored Reality Mode performance report for a completed session."""
    user = require_user(request)
    row = _owned_conversation(conversation_id, user["user_id"])
    if row["status"] != "completed":
        raise HTTPException(409, "This session is still active. End it first to generate a report.")
    status = row["report_status"] if "report_status" in row.keys() else "none"
    if status != "ready" or not row["report_json"]:
        raise HTTPException(404, "No performance report is available for this session.")
    try:
        report = json.loads(row["report_json"])
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(500, "The stored report could not be read. Try ending the session again.")
    return {"report": report, "report_status": "ready"}


@router.delete("")
def clear_history(request: Request):
    require_csrf(request)
    user = require_user(request)
    limit_request(request, "conversation_clear", 4, 3600)
    with database() as conn:
        deleted = conn.execute("DELETE FROM conversations WHERE user_id=?", (user["user_id"],)).rowcount
    return {"ok": True, "deleted": deleted}
