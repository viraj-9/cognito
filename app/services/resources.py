"""Validated learning catalogue and user-facing resource operations."""
import json
import math
from collections import Counter
from pathlib import Path
from .text import char_ngrams, clean_text
from ..settings import ROOT

DATA_PATH = ROOT / "data" / "resources.json"
LANGUAGE_EXPANSIONS = {
    "hola": "greeting conversation introduction", "bonjour": "greeting conversation introduction",
    "bonjour": "greeting conversation introduction", "salut": "greeting conversation introduction",
    "gramática": "grammar verb sentence", "grammaire": "grammar verb sentence", "gramatica": "grammar verb sentence",
    "palabras": "vocabulary words", "mots": "vocabulary words", "palabras": "vocabulary words",
    "नमस्ते": "greeting conversation", "व्याकरण": "grammar verb sentence", "शब्द": "vocabulary words",
}


def load_resources() -> list[dict]:
    records = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("The learning catalogue is empty or malformed.")
    ids = {row.get("id") for row in records}
    if len(ids) != len(records) or None in ids:
        raise ValueError("Learning resource identifiers must be unique.")
    for row in records:
        required = ("title", "summary", "content", "exercise", "answer")
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in required):
            raise ValueError(f"Learning resource {row.get('id')} has missing fields.")
        if any(parent not in ids for parent in row.get("prerequisites", [])):
            raise ValueError(f"Learning resource {row['id']} references a missing prerequisite.")
    return records


RESOURCES = load_resources()
BY_ID = {row["id"]: row for row in RESOURCES}


def public_resource(row: dict, include_exercise: bool = False) -> dict:
    fields = ("id", "title", "language", "level", "category", "summary", "content", "tags")
    result = {key: row[key] for key in fields}
    if include_exercise:
        result["exercise"] = row["exercise"]
    return result


def _vector(text: str) -> Counter:
    return Counter(char_ngrams(text))


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    dot = sum(value * b.get(key, 0) for key, value in a.items())
    norm = math.sqrt(sum(v*v for v in a.values()) * sum(v*v for v in b.values()))
    return dot / norm if norm else 0.0


def search_resources(query: str, limit: int = 8, level: str | None = None, language: str | None = None) -> list[dict]:
    query = clean_text(query, 180)
    if not query:
        return []
    expanded = query.casefold()
    for phrase, terms in LANGUAGE_EXPANSIONS.items():
        if phrase in expanded:
            expanded += " " + terms
    query_vector = _vector(expanded)
    ranked = []
    for row in RESOURCES:
        if level and row["level"] != level:
            continue
        if language and row["language"] != language:
            continue
        searchable = " ".join([row["title"], row["summary"], row["content"], row["category"], *row["tags"]])
        score = _cosine(query_vector, _vector(searchable))
        if score > 0:
            ranked.append((score, row))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["title"]))
    return [public_resource(row, include_exercise=True) for score, row in ranked[:max(1, min(limit, 20))]]


def related_resources(resource_id: str, limit: int = 5) -> list[dict]:
    row = BY_ID.get(resource_id)
    if not row:
        return []
    base_tags = set(row["tags"])
    candidates = []
    for other in RESOURCES:
        if other["id"] == resource_id:
            continue
        overlap = len(base_tags & set(other["tags"]))
        prerequisite = resource_id in other["prerequisites"] or other["id"] in row["prerequisites"]
        score = overlap + (2 if prerequisite else 0)
        if score:
            candidates.append((score, other))
    candidates.sort(key=lambda item: (-item[0], item[1]["title"]))
    return [public_resource(item, include_exercise=True) for _, item in candidates[:limit]]


def search_path(start: str, goal: str) -> list[str]:
    """A* over prerequisite edges; zero heuristic is admissible for this graph."""
    if start not in BY_ID or goal not in BY_ID:
        return []
    neighbors = {row["id"]: set(row["prerequisites"]) for row in RESOURCES}
    for row in RESOURCES:
        for parent in row["prerequisites"]:
            neighbors[parent].add(row["id"])
    frontier = [(0, 0, start, [start])]
    best = {start: 0}
    while frontier:
        frontier.sort(key=lambda item: (item[0], item[1], item[2]))
        _, cost, current, path = frontier.pop(0)
        if current == goal:
            return path
        for nxt in sorted(neighbors[current]):
            new_cost = cost + 1
            if new_cost < best.get(nxt, 10**9):
                best[nxt] = new_cost
                frontier.append((new_cost, new_cost, nxt, path + [nxt]))
    return []



# Small, curated exercise pools keep lessons fresh without changing their visual template.
# Answers remain server-side; the browser receives only the question ID and prompt.
EXERCISE_BANKS: dict[str, list[dict]] = {
    "greetings": [
        {"id":"greetings-1","exercise":"Complete: Good morning, how ___ you?","answer":"are","partial_answers":["is"]},
        {"id":"greetings-2","exercise":"Complete the introduction: My name ___ Priya.","answer":"is","partial_answers":["am"]},
        {"id":"greetings-3","exercise":"Choose a friendly greeting: (hello / goodbye) when you arrive.","answer":"hello","partial_answers":["hi"]},
        {"id":"greetings-4","exercise":"Complete: Nice to ___ you.","answer":"meet","partial_answers":[]},
        {"id":"greetings-5","exercise":"Ask someone their name: What is ___ name?","answer":"your","partial_answers":["their"]},
    ],
    "subject-pronouns": [
        {"id":"pronouns-1","exercise":"Choose the pronoun: ___ am ready. (I / Me)","answer":"i","partial_answers":["me"]},
        {"id":"pronouns-2","exercise":"Choose the pronoun: Rahul is kind. ___ helps everyone. (He / Him)","answer":"he","partial_answers":["him"]},
        {"id":"pronouns-3","exercise":"Complete: Maya and I are friends. ___ study together. (We / Us)","answer":"we","partial_answers":["us"]},
        {"id":"pronouns-4","exercise":"Complete: The books are new. ___ are on the desk. (They / Them)","answer":"they","partial_answers":["them"]},
        {"id":"pronouns-5","exercise":"Choose: Please call ___ tomorrow. (I / me)","answer":"me","partial_answers":["i"]},
    ],
    "be-verb": [
        {"id":"be-verb-1","exercise":"Complete: She ___ my classmate. (am / is / are)","answer":"is","partial_answers":["are"]},
        {"id":"be-verb-2","exercise":"Complete: We ___ ready to begin. (am / is / are)","answer":"are","partial_answers":["is"]},
        {"id":"be-verb-3","exercise":"Complete: I ___ learning English. (am / is / are)","answer":"am","partial_answers":["is"]},
        {"id":"be-verb-4","exercise":"Complete: They ___ at the library. (am / is / are)","answer":"are","partial_answers":["is"]},
        {"id":"be-verb-5","exercise":"Complete: The weather ___ pleasant today. (am / is / are)","answer":"is","partial_answers":["are"]},
    ],
    "articles": [
        {"id":"articles-1","exercise":"Choose the article: ___ umbrella (a / an)","answer":"an","partial_answers":["a"]},
        {"id":"articles-2","exercise":"Choose the article: ___ university (a / an)","answer":"a","partial_answers":["an"]},
        {"id":"articles-3","exercise":"Choose the article: I saw ___ elephant. (a / an)","answer":"an","partial_answers":["a"]},
        {"id":"articles-4","exercise":"Complete: Please close ___ door beside you. (the / an)","answer":"the","partial_answers":["an"]},
        {"id":"articles-5","exercise":"Choose the article: She bought ___ book. (a / an)","answer":"a","partial_answers":["an"]},
    ],
    "present-simple": [
        {"id":"present-1","exercise":"Complete: He ___ to college every day. (go)","answer":"goes","partial_answers":["go"]},
        {"id":"present-2","exercise":"Complete: They ___ cricket on Sundays. (play)","answer":"play","partial_answers":["plays"]},
        {"id":"present-3","exercise":"Complete: She ___ English at night. (study)","answer":"studies","partial_answers":["study"]},
        {"id":"present-4","exercise":"Complete: My brother ___ coffee every morning. (drink)","answer":"drinks","partial_answers":["drink"]},
        {"id":"present-5","exercise":"Make a question: ___ you like music? (Do / Does)","answer":"do","partial_answers":["does"]},
    ],
    "past-simple": [
        {"id":"past-1","exercise":"Complete: Yesterday, I ___ my friend. (meet)","answer":"met","partial_answers":["meet"]},
        {"id":"past-2","exercise":"Complete: We ___ a movie last night. (watch)","answer":"watched","partial_answers":["watch"]},
        {"id":"past-3","exercise":"Complete: She ___ home late yesterday. (come)","answer":"came","partial_answers":["come"]},
        {"id":"past-4","exercise":"Complete: They ___ the bus this morning. (miss)","answer":"missed","partial_answers":["miss"]},
        {"id":"past-5","exercise":"Complete: He ___ his homework before dinner. (finish)","answer":"finished","partial_answers":["finish"]},
    ],
    "word-order": [
        {"id":"word-order-1","exercise":"Build a sentence: (English / speak / I)","answer":"I speak English","partial_answers":["I English speak"]},
        {"id":"word-order-2","exercise":"Build a sentence: (coffee / likes / she)","answer":"She likes coffee","partial_answers":["She coffee likes"]},
        {"id":"word-order-3","exercise":"Build a sentence: (every day / study / we)","answer":"We study every day","partial_answers":["We every day study"]},
        {"id":"word-order-4","exercise":"Build a sentence: (is / my friend / kind)","answer":"My friend is kind","partial_answers":["My friend kind is"]},
        {"id":"word-order-5","exercise":"Build a sentence: (a book / reads / Rahul)","answer":"Rahul reads a book","partial_answers":["Rahul a book reads"]},
        {"id":"word-order-6","exercise":"Build a sentence: (the bus / at seven / arrives)","answer":"The bus arrives at seven","partial_answers":["The bus at seven arrives"]},
        {"id":"word-order-7","exercise":"Build a sentence: (my homework / finished / I)","answer":"I finished my homework","partial_answers":["I my homework finished"]},
    ],
    "question-words": [
        {"id":"question-words-1","exercise":"Ask about a place: ___ do you live? (Where / When)","answer":"where","partial_answers":["when"]},
        {"id":"question-words-2","exercise":"Ask about a reason: ___ are you late? (Why / Who)","answer":"why","partial_answers":["who"]},
        {"id":"question-words-3","exercise":"Ask about a time: ___ does the class start? (When / Where)","answer":"when","partial_answers":["where"]},
        {"id":"question-words-4","exercise":"Ask about a person: ___ is your teacher? (Who / What)","answer":"who","partial_answers":["what"]},
        {"id":"question-words-5","exercise":"Ask about a method: ___ do you travel to college? (How / Why)","answer":"how","partial_answers":["why"]},
    ],
    "daily-vocabulary": [
        {"id":"vocab-1","exercise":"Choose the best word: I feel tired, so I will ___. (rest / shout)","answer":"rest","partial_answers":["shout"]},
        {"id":"vocab-2","exercise":"Complete: I use a ___ to write notes. (pen / pillow)","answer":"pen","partial_answers":["pillow"]},
        {"id":"vocab-3","exercise":"Choose a morning routine: I ___ my teeth. (brush / cook)","answer":"brush","partial_answers":["cook"]},
        {"id":"vocab-4","exercise":"Complete: A person who teaches students is a ___. (teacher / driver)","answer":"teacher","partial_answers":["driver"]},
        {"id":"vocab-5","exercise":"Choose the travel word: We wait for the train at the ___. (station / kitchen)","answer":"station","partial_answers":["kitchen"]},
        {"id":"vocab-6","exercise":"Complete: Please ___ the window; it is very hot. (open / sleep)","answer":"open","partial_answers":["sleep"]},
    ],
    "polite-requests": [
        {"id":"polite-1","exercise":"Complete a polite request: Could you ___ me, please? (help / helps)","answer":"help","partial_answers":["helps"]},
        {"id":"polite-2","exercise":"Make it polite: ___ you mind repeating that? (Would / Do)","answer":"would","partial_answers":["do"]},
        {"id":"polite-3","exercise":"Complete: Could I ___ your pen, please? (borrow / borrows)","answer":"borrow","partial_answers":["borrows"]},
        {"id":"polite-4","exercise":"Complete: Would you please ___ the door? (close / closes)","answer":"close","partial_answers":["closes"]},
        {"id":"polite-5","exercise":"Choose the polite phrase: (Could you help me? / Give me that.)","answer":"Could you help me?","partial_answers":["could you help me"]},
    ],
    "adjectives": [
        {"id":"adjectives-1","exercise":"Choose the adjective: The room is ___. (quiet / quietly)","answer":"quiet","partial_answers":["quietly"]},
        {"id":"adjectives-2","exercise":"Complete: It was a ___ movie. (funny / funnily)","answer":"funny","partial_answers":["funnily"]},
        {"id":"adjectives-3","exercise":"Choose the adjective: She is a ___ student. (careful / carefully)","answer":"careful","partial_answers":["carefully"]},
        {"id":"adjectives-4","exercise":"Complete: The mountain is very ___. (high / highly)","answer":"high","partial_answers":["highly"]},
        {"id":"adjectives-5","exercise":"Choose the adjective: This is an ___ idea. (interesting / interestingly)","answer":"interesting","partial_answers":["interestingly"]},
    ],
}


def get_exercise(resource_id: str, exclude_ids: set[str] | None = None) -> dict:
    """Return a fresh exercise prompt (never expose the expected answer)."""
    if resource_id not in BY_ID:
        raise KeyError(resource_id)
    pool = EXERCISE_BANKS.get(resource_id)
    if not pool:
        row = BY_ID[resource_id]
        return {"id": f"{resource_id}-default", "exercise": row["exercise"]}
    excluded = exclude_ids or set()
    available = [item for item in pool if item["id"] not in excluded]
    if not available:
        available = pool
    from random import SystemRandom
    selected = SystemRandom().choice(available)
    return {"id": selected["id"], "exercise": selected["exercise"]}


def validate_question(resource_id: str, question_id: str, answer: str) -> dict:
    """Validate an answer against a specific server-side exercise in the question bank."""
    if resource_id not in BY_ID:
        raise KeyError(resource_id)
    pool = EXERCISE_BANKS.get(resource_id, [])
    question = next((item for item in pool if item["id"] == question_id), None)
    if question is None:
        if question_id == f"{resource_id}-default":
            return validate_exercise(resource_id, answer)
        raise ValueError("That practice question has expired. Load the next question and try again.")
    clean = clean_text(answer, 500).casefold().strip(" .!?\"'“”")
    if not clean:
        raise ValueError("Enter an answer before checking it.")
    expected = clean_text(question["answer"]).casefold().strip(" .!?\"'“”")
    partial = {clean_text(item).casefold().strip(" .!?\"'“”") for item in question.get("partial_answers", [])}
    if clean == expected:
        quality, score, message = "complete", 1.0, "That's right. Nice work!"
    elif clean in partial or (len(clean.split()) > 1 and clean in expected):
        quality, score, message = "almost", 0.5, "Almost. Review the explanation, then give it another try."
    else:
        quality, score, message = "retry", 0.0, "Not quite yet. Read the explanation and try once more."
    return {"quality": quality, "score": score, "feedback": message}

def validate_exercise(resource_id: str, answer: str) -> dict:
    if resource_id not in BY_ID:
        raise KeyError(resource_id)
    clean = clean_text(answer, 500).casefold().strip(" .!?\"'“”")
    if not clean:
        raise ValueError("Enter an answer before checking it.")
    row = BY_ID[resource_id]
    expected = clean_text(row["answer"]).casefold().strip()
    partial = {clean_text(item).casefold().strip() for item in row.get("partial_answers", [])}
    if clean == expected:
        quality, score, message = "complete", 1.0, "That's right. Nice work!"
    elif clean in partial or (len(clean.split()) > 1 and clean in expected):
        quality, score, message = "almost", 0.5, "Almost. Review the explanation, then give it another try."
    else:
        quality, score, message = "retry", 0.0, "Not quite yet. Read the explanation and try once more."
    return {"quality": quality, "score": score, "feedback": message}
