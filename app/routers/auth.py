"""Account, cookie session, and learner profile endpoints."""
import re
import sqlite3
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from ..database import database
from ..security import clear_session, create_session, get_session, hash_password, hash_session_token, limit_request, require_csrf, require_user, verify_password

router=APIRouter(prefix="/api",tags=["account"])


class RegisterBody(BaseModel):
    email: str = Field(min_length=6,max_length=254)
    display_name: str = Field(min_length=1,max_length=60)
    password: str = Field(min_length=10,max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls,value):
        value=value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]{2,}",value): raise ValueError("Enter a valid email address.")
        return value

    @field_validator("display_name")
    @classmethod
    def validate_name(cls,value):
        value=" ".join(value.split())
        if not value or any(ord(char)<32 for char in value): raise ValueError("Enter a valid display name.")
        return value

    @field_validator("password")
    @classmethod
    def validate_password(cls,value):
        if not value.strip(): raise ValueError("Choose a password with at least 10 non-space characters.")
        return value


class LoginBody(BaseModel):
    email: str = Field(min_length=3,max_length=254)
    password: str = Field(min_length=1,max_length=128)


class ProfileBody(BaseModel):
    display_name: str = Field(min_length=1,max_length=60)
    preferred_language: str = Field(default="auto",pattern="^(auto|en|es|fr|hi|hinglish|mr)$")


def public_user(row):
    return {"id":row["id"],"email":row["email"],"display_name":row["display_name"],"preferred_language":row["preferred_language"],"created_at":row["created_at"]}


@router.get("/session")
def session_endpoint(request: Request,response: Response):
    session=get_session(request)
    if not session:
        csrf,_=create_session(response,None)
        return {"authenticated":False,"csrf_token":csrf,"user":None}
    user=None
    if session["user_id"] is not None: user={"id":session["user_id"],"email":session["email"],"display_name":session["display_name"],"preferred_language":session["preferred_language"]}
    return {"authenticated":user is not None,"csrf_token":session["csrf_token"],"user":user}


@router.post("/auth/register",status_code=201)
def register(body:RegisterBody,request:Request,response:Response):
    require_csrf(request); limit_request(request,"register",5,3600)
    try:
        with database() as conn:
            existing = conn.execute("SELECT 1 FROM users WHERE email=?", (body.email,)).fetchone()
            if existing:
                raise HTTPException(400, "An account with that email already exists.")
            cursor=conn.execute("INSERT INTO users(email,display_name,password_hash) VALUES(?,?,?)",(body.email,body.display_name,hash_password(body.password)))
            user_id=cursor.lastrowid
            raw=request.cookies.get("lingua_session")
            if raw:
                conn.execute("DELETE FROM sessions WHERE token_hash=?",(hash_session_token(raw),))
    except sqlite3.IntegrityError:
        raise HTTPException(400,"An account with that email already exists.") from None
    csrf,_=create_session(response,user_id)
    with database() as conn: user=conn.execute("SELECT * FROM users WHERE id=?",(user_id,)).fetchone()
    return {"user":public_user(user),"csrf_token":csrf}


@router.post("/auth/login")
def login(body:LoginBody,request:Request,response:Response):
    require_csrf(request); limit_request(request,"login",10,900)
    with database() as conn: user=conn.execute("SELECT * FROM users WHERE email=?",(body.email.strip().lower(),)).fetchone()
    # A dummy hash keeps unknown-account and wrong-password paths comparable.
    if user is None:
        verify_password(body.password,"pbkdf2_sha256$310000$0123456789abcdef0123456789abcdef$"+"0"*64)
        raise HTTPException(401,"Email or password is incorrect.")
    if not verify_password(body.password,user["password_hash"]): raise HTTPException(401,"Email or password is incorrect.")
    raw=request.cookies.get("lingua_session")
    if raw:
        with database() as conn: conn.execute("DELETE FROM sessions WHERE token_hash=?",(hash_session_token(raw),))
    csrf,_=create_session(response,user["id"])
    return {"user":public_user(user),"csrf_token":csrf}


@router.post("/auth/logout")
def logout(request:Request,response:Response):
    require_csrf(request)
    clear_session(response,request.cookies.get("lingua_session"))
    return {"ok":True}


@router.get("/profile")
def profile(request:Request):
    session=require_user(request)
    with database() as conn: user=conn.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()
    return public_user(user)


@router.put("/profile")
def update_profile(body:ProfileBody,request:Request):
    require_csrf(request); session=require_user(request)
    name=" ".join(body.display_name.split())
    if not name or any(ord(char)<32 for char in name): raise HTTPException(422,"Enter a valid display name.")
    with database() as conn:
        conn.execute("UPDATE users SET display_name=?,preferred_language=? WHERE id=?",(name,body.preferred_language,session["user_id"]))
        user=conn.execute("SELECT * FROM users WHERE id=?",(session["user_id"],)).fetchone()
    return public_user(user)
