"""HTTP smoke check for a local Cognito server."""
import http.cookiejar
import json
import os
import urllib.error
import urllib.request
import uuid

BASE=os.getenv("LINGUAAI_TEST_BASE","http://127.0.0.1:8000")
jar=http.cookiejar.CookieJar()
opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def request(path,method="GET",body=None,csrf=None,expected=200):
    payload=json.dumps(body).encode() if body is not None else None
    headers={"Content-Type":"application/json"} if payload else {}
    if csrf: headers["X-CSRF-Token"]=csrf
    req=urllib.request.Request(BASE+path,data=payload,headers=headers,method=method)
    try: response=opener.open(req,timeout=12)
    except urllib.error.HTTPError as exc: response=exc
    raw=response.read()
    data=json.loads(raw) if raw else {}
    if response.status!=expected: raise AssertionError(f"{method} {path}: expected {expected}, got {response.status}: {data}")
    return data


def main():
    page=opener.open(BASE+"/",timeout=12).read().decode()
    assert "Learning library" in page and "CO301" not in page
    assert "Interview practice" in page and "voice-start" in page
    assert "HR interview practice" in page and "Technical interview practice" in page
    session=request("/api/session"); csrf=session["csrf_token"]
    email=f"lingua-smoke-{uuid.uuid4().hex[:10]}@example.test"; password="StrongPassword-932!"
    invalid=request("/api/auth/register","POST",{"email":"not-an-email","display_name":"Smoke","password":"SensitiveValue-123"},csrf,422)
    assert "SensitiveValue-123" not in json.dumps(invalid)
    data=request("/api/auth/register","POST",{"email":email,"display_name":"Smoke Learner","password":password},csrf,201)
    csrf=data["csrf_token"]
    dup=request("/api/auth/register","POST",{"email":email,"display_name":"Smoke Learner","password":password},csrf,400)
    assert "already exists" in dup["detail"]
    wrong=request("/api/auth/login","POST",{"email":email,"password":"WrongPassword-932!"},csrf,401)
    assert "incorrect" in wrong["detail"]
    assert request("/api/profile")["email"]==email
    profile=request("/api/profile","PUT",{"display_name":"Smoke Learner","preferred_language":"es"},csrf)
    assert profile["preferred_language"]=="es"
    found=request("/api/library/search?q=polite%20request")["results"]
    assert found and found[0]["id"]=="polite-requests"
    detail=request("/api/library/be-verb")
    assert detail["id"]=="be-verb" and "exercise" in detail and "answer" not in detail
    # Live AI chat now fails clearly when the provider is not configured; library search remains available above.
    try:
        request("/api/chat","POST",{"message":"Find a guide for a polite request","language":"en","history":[]},csrf)
    except AssertionError as exc:
        # The smoke runner expects 200 by default; provider-free setup should explicitly report 503.
        assert "503" in str(exc), str(exc)
    else:
        raise AssertionError("Expected provider-free chat to return a clear 503 instead of a scripted fallback.")
    partial=request("/api/library/be-verb/assessment","POST",{"answer":"is"},csrf)
    assert partial["quality"]=="almost"
    assessment=request("/api/library/be-verb/assessment","POST",{"answer":"are"},csrf)
    assert assessment["score"]==1.0
    progress=request("/api/progress")
    assert any(item["resource"]["id"]=="be-verb" for item in progress["completed"])
    assert len(progress["recent_attempts"])>=2
    request("/api/library/greetings/feedback","POST",{"helpful":True},csrf)
    request("/api/library/greetings/feedback","POST",{"helpful":False},csrf)
    assert request("/api/recommendations")["recommendations"]
    assert request("/api/conversations")["conversations"]==[]
    if request("/api/health")["tutoring"]!="configured":
        for m in ("interview","interview_hr","interview_tech","mock_interview","roleplay","casual","english"):
            body={"mode":m,"level":"beginner","language":"en","role":"intern"}
            if m=="mock_interview": body.update({"education_level":"undergraduate","education_field":"computer","role":"Software Developer Intern"})
            offline=request("/api/conversations","POST",body,csrf,201)
            assert offline["settings"]["offline_fallback"] is True
            ended=request(f"/api/conversations/{offline['id']}/end","POST",{},csrf,200)
            assert ended["report_status"]=="ready"
            assert ended["report"]["rubric_version"]=="offline-1.1"
            assert "spoken_confidence" in ended["report"]
    request("/api/conversations","DELETE",{},csrf)
    request("/api/auth/logout","POST",{},"invalid-token",403)
    request("/api/auth/logout","POST",{},csrf)
    csrf=request("/api/session")["csrf_token"]
    login=request("/api/auth/login","POST",{"email":email,"password":password},csrf)
    assert login["user"]["email"]==email
    request("/api/auth/logout","POST",{},login["csrf_token"])
    request("/api/progress",expected=401)
    request("/api/conversations",expected=401)
    print("API smoke check passed: static UI, search, offline assistant, auth edge cases, profile, progress, conversation auth/configuration, CSRF, login, and logout.")


if __name__=="__main__": main()
