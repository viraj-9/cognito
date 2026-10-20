"""Library, assessment, search, progress, and recommendation endpoints."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..database import database
from ..security import get_session, limit_request, require_csrf, require_user
from ..services.recommendations import recommend
from ..services.resources import BY_ID, RESOURCES, public_resource, related_resources, search_path, search_resources, validate_exercise, get_exercise, validate_question

router=APIRouter(prefix="/api",tags=["learning"])


class AssessmentBody(BaseModel):
    answer: str = Field(min_length=1,max_length=500)
    question_id: str | None = Field(default=None, max_length=100)


class FeedbackBody(BaseModel):
    helpful: bool


@router.get("/library")
def library():
    return {"resources":[public_resource(row) for row in RESOURCES],"levels":sorted({row["level"] for row in RESOURCES}),"categories":sorted({row["category"] for row in RESOURCES})}


@router.get("/library/search")
def library_search(q:str="",limit:int=8,level:str|None=None,language:str|None=None):
    if len(q)>180: raise HTTPException(422,"Search text must be 180 characters or fewer.")
    return {"query":q,"results":search_resources(q,limit,level,language)}


@router.get("/library/{resource_id}")
def resource_detail(resource_id:str):
    row=BY_ID.get(resource_id)
    if not row: raise HTTPException(404,"Learning guide not found.")
    return {**public_resource(row,include_exercise=True),"related":related_resources(resource_id)}


@router.get("/library/{resource_id}/question")
def learning_question(resource_id: str, exclude: str = ""):
    if resource_id not in BY_ID:
        raise HTTPException(404, "Learning guide not found.")
    excluded = {item for item in exclude.split(",") if item and len(item) <= 100}
    question = get_exercise(resource_id, excluded)
    return {"question_id": question["id"], "exercise": question["exercise"]}


@router.get("/paths")
def learning_path(start:str,goal:str):
    path=search_path(start,goal)
    if not path: raise HTTPException(404,"No learning path connects those guides.")
    return {"steps":[{"id":rid,"title":BY_ID[rid]["title"]} for rid in path]}


@router.get("/recommendations")
def recommendations(request:Request,level:str|None=None,limit:int=4):
    if level and level not in {row["level"] for row in RESOURCES}: raise HTTPException(422,"Choose a level available in the library.")
    session=get_session(request); user_id=session["user_id"] if session else None
    with database() as conn: items=recommend(conn,user_id,level,limit)
    return {"recommendations":items}


@router.post("/library/{resource_id}/assessment")
def assessment(resource_id:str,body:AssessmentBody,request:Request):
    require_csrf(request); limit_request(request,"assessment",30,60)
    if resource_id not in BY_ID: raise HTTPException(404,"Learning guide not found.")
    try:
        result = validate_question(resource_id, body.question_id, body.answer) if body.question_id else validate_exercise(resource_id, body.answer)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    session=get_session(request)
    if session and session["user_id"] is not None:
        with database() as conn:
            conn.execute("INSERT INTO assessment_attempts(user_id,resource_id,score) VALUES(?,?,?)",(session["user_id"],resource_id,result["score"]))
            if result["quality"]=="complete":
                conn.execute("INSERT INTO progress(user_id,resource_id,status,score,updated_at) VALUES(?,?,'completed',?,strftime('%Y-%m-%dT%H:%M:%fZ','now')) ON CONFLICT(user_id,resource_id) DO UPDATE SET status='completed',score=MAX(COALESCE(progress.score,0),excluded.score),updated_at=excluded.updated_at",(session["user_id"],resource_id,result["score"]))
    return result


@router.post("/library/{resource_id}/feedback")
def recommendation_feedback(resource_id:str,body:FeedbackBody,request:Request):
    require_csrf(request); limit_request(request,"feedback",40,3600); session=require_user(request)
    if resource_id not in BY_ID: raise HTTPException(404,"Learning guide not found.")
    with database() as conn: conn.execute("INSERT INTO recommendation_feedback(user_id,resource_id,helpful) VALUES(?,?,?)",(session["user_id"],resource_id,int(body.helpful)))
    return {"ok":True}


@router.get("/progress")
def progress(request:Request):
    session=require_user(request)
    with database() as conn:
        completed=conn.execute("SELECT p.resource_id,p.score,p.updated_at FROM progress p WHERE p.user_id=? AND p.status='completed' ORDER BY p.updated_at DESC",(session["user_id"],)).fetchall()
        recent=conn.execute("SELECT a.resource_id,a.score,a.created_at FROM assessment_attempts a WHERE a.user_id=? ORDER BY a.created_at DESC LIMIT 6",(session["user_id"],)).fetchall()
    return {"completed":[{"resource":public_resource(BY_ID[r["resource_id"]]),"score":r["score"],"updated_at":r["updated_at"]} for r in completed if r["resource_id"] in BY_ID],"recent_attempts":[{"resource_id":r["resource_id"],"title":BY_ID[r["resource_id"]]["title"],"score":r["score"],"created_at":r["created_at"]} for r in recent if r["resource_id"] in BY_ID]}
