"""Comprehensive End-to-End and Security Integration Tests for Cognito."""
import http.cookiejar
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class EndToEndIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_db_file = tempfile.NamedTemporaryFile(prefix="lingua-e2e-", suffix=".sqlite3", delete=False)
        cls.temp_db = Path(cls.temp_db_file.name)
        cls.temp_db_file.close()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]

        cls.base_url = f"http://127.0.0.1:{cls.port}"
        env = os.environ.copy()
        env.update({
            "LINGUAAI_ENV": "development",
            "LINGUAAI_SESSION_SECRET": "a"*32,
            "LINGUAAI_DATABASE": str(cls.temp_db),
            "LINGUAAI_AI_BASE_URL": "",
            "LINGUAAI_AI_API_KEY": "",
            "LINGUAAI_AI_MODEL": "",
            "LINGUAAI_TEST_BASE": cls.base_url,
        })
        cls.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(cls.port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ready = False
        for _ in range(50):
            if cls.proc.poll() is not None:
                raise RuntimeError("Uvicorn exited unexpectedly during test startup.")
            try:
                with urllib.request.urlopen(f"{cls.base_url}/api/ready", timeout=1) as res:
                    if res.status == 200:
                        ready = True
                        break
            except Exception:
                time.sleep(0.2)
        if not ready:
            raise TimeoutError("Test server did not become ready.")

    @classmethod
    def tearDownClass(cls):
        if cls.proc and cls.proc.poll() is None:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        for suffix in ("", "-shm", "-wal"):
            candidate = Path(str(cls.temp_db) + suffix)
            try:
                candidate.unlink()
            except (FileNotFoundError, PermissionError):
                pass

    def _client(self):
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def req(path, method="GET", body=None, csrf=None, expected=200):
            payload = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"} if payload else {}
            if csrf:
                headers["X-CSRF-Token"] = csrf
            r = urllib.request.Request(self.base_url + path, data=payload, headers=headers, method=method)
            try:
                res = opener.open(r, timeout=10)
            except urllib.error.HTTPError as exc:
                res = exc
            raw = res.read()
            data = json.loads(raw) if raw else {}
            if res.status != expected:
                raise AssertionError(f"{method} {path} expected {expected}, got {res.status}: {data}")
            return data
        return req, jar

    def test_01_static_ui_and_privacy_checks(self):
        req, _ = self._client()
        with urllib.request.urlopen(f"{self.base_url}/", timeout=10) as res:
            html = res.read().decode("utf-8")
        self.assertIn("One bot. <em>Every language.</em>", html)
        self.assertIn("Casual conversation", html)
        self.assertIn("HR interview practice", html)
        self.assertIn("Technical interview practice", html)
        self.assertIn("Mock interview (by education)", html)
        self.assertIn("Hinglish", html)
        self.assertIn("मराठी", html)
        self.assertIn("Cancel conversation", html)
        self.assertIn('id="voice-speed"', html)
        self.assertNotIn("CO301", html)

    def test_02_auth_full_journey_and_isolation(self):
        req_a, jar_a = self._client()
        req_b, jar_b = self._client()

        # Session initial
        s_a = req_a("/api/session")
        csrf_a = s_a["csrf_token"]
        self.assertFalse(s_a["authenticated"])

        email_a = f"learner-a-{uuid.uuid4().hex[:8]}@example.com"
        email_b = f"learner-b-{uuid.uuid4().hex[:8]}@example.com"
        pass_a = "SecretPass-1234!"
        pass_b = "SecretPass-5678!"

        # Register A
        reg_a = req_a("/api/auth/register", "POST", {"email": email_a, "display_name": "Learner Alpha", "password": pass_a}, csrf_a, 201)
        csrf_a = reg_a["csrf_token"]
        self.assertEqual(reg_a["user"]["email"], email_a)

        # Duplicate register A should fail with clear message
        dup = req_a("/api/auth/register", "POST", {"email": email_a, "display_name": "Learner Alpha Duplicate", "password": pass_a}, csrf_a, 400)
        self.assertIn("already exists", dup["detail"])

        # Register B
        s_b = req_b("/api/session")
        csrf_b = s_b["csrf_token"]
        reg_b = req_b("/api/auth/register", "POST", {"email": email_b, "display_name": "Learner Beta", "password": pass_b}, csrf_b, 201)
        csrf_b = reg_b["csrf_token"]

        # Check Profiles
        prof_a = req_a("/api/profile")
        self.assertEqual(prof_a["display_name"], "Learner Alpha")
        prof_b = req_b("/api/profile")
        self.assertEqual(prof_b["display_name"], "Learner Beta")

        # Learner A completes assessment
        ans_a = req_a("/api/library/be-verb/assessment", "POST", {"answer": "are"}, csrf_a, 200)
        self.assertEqual(ans_a["score"], 1.0)

        # User Isolation Check: Learner B should not see Learner A's completed guides
        prog_b = req_b("/api/progress")
        self.assertEqual(prog_b["completed"], [])
        self.assertEqual(prog_b["recent_attempts"], [])

        prog_a = req_a("/api/progress")
        self.assertEqual(len(prog_a["completed"]), 1)
        self.assertEqual(prog_a["completed"][0]["resource"]["id"], "be-verb")

        # Wrong password login
        wrong = req_a("/api/auth/login", "POST", {"email": email_a, "password": "WrongPassword-999!"}, csrf_a, 401)
        self.assertIn("incorrect", wrong["detail"])

        # Logout A
        req_a("/api/auth/logout", "POST", {}, csrf_a, 200)

        # Protected route requires auth
        req_a("/api/progress", expected=401)
        req_a("/api/conversations", expected=401)

        # Login A again
        s_a_new = req_a("/api/session")
        login_a = req_a("/api/auth/login", "POST", {"email": email_a, "password": pass_a}, s_a_new["csrf_token"], 200)
        self.assertEqual(login_a["user"]["email"], email_a)
        csrf_a = login_a["csrf_token"]

        # Profile accessible again
        self.assertEqual(req_a("/api/profile")["email"], email_a)

    def test_03_learning_library_and_assessment(self):
        req, _ = self._client()
        lib = req("/api/library")
        self.assertGreaterEqual(len(lib["resources"]), 5)
        self.assertIn("A1", lib["levels"])

        # Search
        res = req("/api/library/search?q=greetings")
        self.assertTrue(len(res["results"]) > 0)
        self.assertEqual(res["results"][0]["id"], "greetings")

        # Detail
        detail = req("/api/library/greetings")
        self.assertEqual(detail["id"], "greetings")
        self.assertIn("exercise", detail)
        self.assertNotIn("answer", detail)

        # Path
        paths = req("/api/paths?start=greetings&goal=polite-requests")
        self.assertGreaterEqual(len(paths["steps"]), 2)

        question_a = req("/api/library/word-order/question")
        question_b = req(f"/api/library/word-order/question?exclude={question_a['question_id']}")
        self.assertNotEqual(question_a["question_id"], question_b["question_id"])
        self.assertNotIn("answer", question_a)
        self.assertIn("Build a sentence", question_a["exercise"] + question_b["exercise"])

        # Assessment evaluation
        sess = req("/api/session")
        csrf = sess["csrf_token"]
        exact = req("/api/library/be-verb/assessment", "POST", {"answer": "are"}, csrf, 200)
        self.assertEqual(exact["quality"], "complete")
        self.assertEqual(exact["score"], 1.0)

        partial = req("/api/library/be-verb/assessment", "POST", {"answer": "is"}, csrf, 200)
        self.assertEqual(partial["quality"], "almost")
        self.assertEqual(partial["score"], 0.5)

        wrong = req("/api/library/be-verb/assessment", "POST", {"answer": "xyzwrong"}, csrf, 200)
        self.assertEqual(wrong["quality"], "retry")
        self.assertEqual(wrong["score"], 0.0)

    def test_04_chat_requires_provider_instead_of_returning_scripted_fallback(self):
        req, _ = self._client()
        sess = req("/api/session")
        csrf = sess["csrf_token"]
        error = req("/api/chat", "POST", {"message": "Help me with greetings", "language": "auto"}, csrf, 503)
        self.assertIn("project-root .env", error["detail"])

    def test_05_csrf_security_enforcement(self):
        req, _ = self._client()
        # Missing CSRF
        req("/api/auth/register", "POST", {"email": "bad@test.com", "display_name": "B", "password": "SecretPass-1234!"}, None, 403)
        # Invalid CSRF
        req("/api/auth/register", "POST", {"email": "bad@test.com", "display_name": "B", "password": "SecretPass-1234!"}, "invalid-csrf", 403)

    def test_06_conversation_practice_and_report_lifecycle_and_isolation(self):
        req_a, _ = self._client()
        req_b, _ = self._client()

        # Init sessions
        s_a = req_a("/api/session")
        s_b = req_b("/api/session")

        email_a = f"practice-a-{uuid.uuid4().hex[:8]}@example.com"
        email_b = f"practice-b-{uuid.uuid4().hex[:8]}@example.com"
        reg_a = req_a("/api/auth/register", "POST", {"email": email_a, "display_name": "Practitioner A", "password": "PracticePass-123!"}, s_a["csrf_token"], 201)
        csrf_a = reg_a["csrf_token"]
        reg_b = req_b("/api/auth/register", "POST", {"email": email_b, "display_name": "Practitioner B", "password": "PracticePass-567!"}, s_b["csrf_token"], 201)
        csrf_b = reg_b["csrf_token"]

        # Conversation practice falls back to clearly labelled offline prompts when the AI provider is unavailable.
        session = req_a("/api/conversations", "POST", {
            "mode": "interview_hr",
            "level": "intermediate",
            "language": "en",
            "role": "Product Manager",
            "scenario": "",
            "corrections": True,
        }, csrf_a, 201)
        conv_id = session["id"]
        self.assertTrue(session["settings"]["offline_fallback"])
        self.assertIn("Tell me about yourself", session["messages"][0]["content"])
        for answer in (
            "I am a computer engineering student and I enjoy solving practical problems.",
            "In my project, I worked with two classmates and helped organize the tasks.",
            "We completed the main features and learned to communicate clearly when something failed.",
        ):
            turn = req_a(f"/api/conversations/{conv_id}/messages", "POST", {"message": answer}, csrf_a, 200)
            self.assertTrue(turn["reply"])
            self.assertTrue(turn["offline_fallback"])

        # User A sees the conversation
        a_list = req_a("/api/conversations")
        self.assertEqual(len(a_list["conversations"]), 1)
        self.assertEqual(a_list["conversations"][0]["id"], conv_id)
        self.assertEqual(a_list["conversations"][0]["report_status"], "none")

        # User B cannot see User A's conversation
        b_list = req_b("/api/conversations")
        self.assertEqual(b_list["conversations"], [])

        req_b(f"/api/conversations/{conv_id}", expected=404)

        # Active conversation report request returns 409 Conflict
        active_report = req_a(f"/api/conversations/{conv_id}/report", expected=409)
        self.assertIn("still active", active_report["detail"])

        # User A ends conversation
        voice_metrics = {
            "capture_available": True, "listening_seconds": 30, "speech_seconds": 24,
            "silence_ratio": 0.2, "pitch_median_hz": 145, "pitch_variation": 0.12,
            "volume_variation": 0.4, "word_count": 35, "response_count": 3,
        }
        ended = req_a(f"/api/conversations/{conv_id}/end", "POST", {"voice_metrics": voice_metrics}, csrf_a, 200)
        self.assertEqual(ended["session"]["status"], "completed")
        self.assertIn("report_status", ended)

        # Offline report is explicitly labelled and can be retrieved normally.
        self.assertEqual(ended["report_status"], "ready")
        self.assertEqual(ended["report"]["rubric_version"], "offline-1.1")
        self.assertTrue(ended["report"]["evidence_limitations"])
        self.assertIsNotNone(ended["report"]["spoken_confidence"]["score"])
        self.assertTrue(ended["report"]["spoken_confidence"]["voice_sample_available"])
        self.assertIn("clarity_fluency_score", ended["report"]["spoken_confidence"])
        fetched_offline_report = req_a(f"/api/conversations/{conv_id}/report")
        self.assertEqual(fetched_offline_report["report_status"], "ready")

        # Simulate an AI-evaluated report stored in DB to verify the same renderer contract.
        sample_report = {
            "session_id": conv_id,
            "session_type": "interview_hr",
            "overall_score": 88,
            "score_summary": "Excellent structured answers and clear communication.",
            "category_scores": [
                {"name": "Relevance of answers", "score": 90, "explanation": "Directly answered questions", "suggestion": "Keep it up"}
            ],
            "strengths": [{"title": "Clear communication", "explanation": "Structured and crisp"}],
            "improvements": [{"issue": "Use STAR", "excerpt": None, "improved_version": None, "why_better": "Clearer", "exercise": "Practice STAR"}],
            "answer_rewrites": [],
            "recommended_next_practice": {"mode": "interview_tech", "focus": "System design", "reason": "Next level"},
            "rubric_version": "1.0",
            "insufficient_evidence": False,
        }
        conn = sqlite3.connect(str(self.temp_db))
        try:
            conn.execute(
                "UPDATE conversations SET report_json=?, report_status='ready' WHERE id=?",
                (json.dumps(sample_report), conv_id)
            )
            conn.commit()
        finally:
            conn.close()

        # User A can now fetch the ready report
        rep_data = req_a(f"/api/conversations/{conv_id}/report")
        self.assertEqual(rep_data["report_status"], "ready")
        self.assertEqual(rep_data["report"]["overall_score"], 88)
        self.assertEqual(rep_data["report"]["category_scores"][0]["name"], "Relevance of answers")

        # User B still cannot fetch User A's report (user isolation)
        req_b(f"/api/conversations/{conv_id}/report", expected=404)

        # Cancel an active session: it must disappear without generating a report.
        cancel_session = req_a("/api/conversations", "POST", {
            "mode": "casual", "level": "beginner", "language": "hinglish"
        }, csrf_a, 201)
        cancel_id = cancel_session["id"]
        self.assertIn("Cognito", cancel_session["messages"][0]["content"])
        cancelled = req_a(f"/api/conversations/{cancel_id}/cancel", "POST", {}, csrf_a, 200)
        self.assertTrue(cancelled["cancelled"])
        self.assertFalse(cancelled["report_generated"])
        req_a(f"/api/conversations/{cancel_id}", expected=404)

        # Marathi and Hinglish are accepted for fresh sessions and keep their selected language.
        hinglish = req_a("/api/conversations", "POST", {"mode":"english","level":"beginner","language":"hinglish"}, csrf_a, 201)
        self.assertEqual(hinglish["settings"]["language"], "hinglish")
        req_a(f"/api/conversations/{hinglish['id']}/cancel", "POST", {}, csrf_a, 200)
        marathi = req_a("/api/conversations", "POST", {"mode":"english","level":"beginner","language":"mr"}, csrf_a, 201)
        self.assertEqual(marathi["settings"]["language"], "mr")
        req_a(f"/api/conversations/{marathi['id']}/cancel", "POST", {}, csrf_a, 200)

        # User A clears conversation history
        cleared = req_a("/api/conversations", "DELETE", {}, csrf_a, 200)
        self.assertEqual(cleared["ok"], True)
        self.assertGreaterEqual(cleared["deleted"], 1)

        # Now conversation is gone
        req_a(f"/api/conversations/{conv_id}", expected=404)

        # Education-based mock interviews require education + field and retain that context in the saved session.
        invalid_education = req_a("/api/conversations", "POST", {
            "mode": "mock_interview", "level": "beginner", "language": "en", "role": "intern",
        }, csrf_a, 422)
        self.assertIn("Select your education", invalid_education["detail"])
        mock_session = req_a("/api/conversations", "POST", {
            "mode": "mock_interview", "level": "beginner", "language": "en",
            "role": "Web Developer Intern", "education_level": "diploma", "education_field": "computer",
        }, csrf_a, 201)
        self.assertEqual(mock_session["settings"]["education_level"], "diploma")
        self.assertEqual(mock_session["settings"]["education_field"], "computer")
        self.assertIn("mock interview for Web Developer Intern", mock_session["messages"][0]["content"])
        mock_end = req_a(f"/api/conversations/{mock_session['id']}/end", "POST", {}, csrf_a, 200)
        self.assertEqual(mock_end["report_status"], "ready")
        self.assertEqual(mock_end["report"]["session_type"], "mock_interview")


if __name__ == "__main__":
    unittest.main()
