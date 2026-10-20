import io
import json
import tempfile
import unittest
from urllib.error import HTTPError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.database import initialize_database, connect
from app.internal.curriculum_map import INTERNAL_ALIGNMENT
from app.services.language_model import load_model, predict_language, train_and_save
from app.services.recommendations import cluster_resources
from app.services.resources import BY_ID, RESOURCES, EXERCISE_BANKS, public_resource, search_path, search_resources, get_exercise, validate_exercise, validate_question
from app.services.text import char_ngrams, clean_text, tokenize
from app.services.provider import (
    ProviderUnavailable,
    _complete,
    _parse_report_json,
    conversation_feedback,
    conversation_reply,
    generate_performance_report,
    tutoring_reply,
)
from app.routers.conversations import _offline_reply, _offline_performance_report, _scenario_for_options, _spoken_confidence_report


class TextPipelineTests(unittest.TestCase):
    def test_unicode_normalization_preserves_accents_and_script(self):
        self.assertEqual(clean_text("  cafe\u0301   नमस्ते  "), "café नमस्ते")
        self.assertIn("café", tokenize("Café"))

    def test_control_chars_are_removed_and_ngram_is_unicode_aware(self):
        self.assertEqual(clean_text("hello\u0000 world"), "hello world")
        self.assertTrue(char_ngrams("नमस्ते"))

    def test_empty_language_input_is_unknown(self):
        self.assertEqual(predict_language("   ",None)["language"],"unknown")


class ResourceTests(unittest.TestCase):
    def test_catalog_is_unique_and_relationships_resolve(self):
        self.assertEqual(len(BY_ID),len(RESOURCES))
        self.assertTrue(all(parent in BY_ID for row in RESOURCES for parent in row["prerequisites"]))

    def test_resource_search_and_prerequisite_path(self):
        found=search_resources("polite request",limit=3)
        self.assertEqual(found[0]["id"],"polite-requests")
        path=search_path("greetings","polite-requests")
        self.assertEqual(path[0],"greetings")
        self.assertEqual(path[-1],"polite-requests")

    def test_assessment_feedback(self):
        self.assertEqual(validate_exercise("be-verb","are")["score"],1.0)
        self.assertEqual(validate_exercise("be-verb","is")["quality"],"almost")
        with self.assertRaises(ValueError): validate_exercise("be-verb"," ")

    def test_question_bank_rotates_prompts_without_exposing_answers(self):
        first = get_exercise("word-order")
        second = get_exercise("word-order", {first["id"]})
        self.assertNotEqual(first["id"], second["id"])
        self.assertIn("Build a sentence", first["exercise"])
        self.assertNotIn("answer", first)
        known = EXERCISE_BANKS["word-order"][0]
        result = validate_question("word-order", known["id"], known["answer"])
        self.assertEqual(result["score"], 1.0)
        with self.assertRaises(ValueError):
            validate_question("word-order", "word-order-not-real", "test")

    def test_public_resources_do_not_include_answer_key_or_internal_mapping(self):
        public=json.dumps([public_resource(row,include_exercise=True) for row in RESOURCES])
        self.assertNotIn('"answer"',public)
        self.assertNotIn("CO301",public)
        self.assertEqual(len(INTERNAL_ALIGNMENT),6)

    def test_cluster_summary_is_finite(self):
        result=cluster_resources()
        self.assertEqual(result["examples"],len(RESOURCES))
        self.assertTrue(-1<=result["silhouette"]<=1)


class ModelTrainingTests(unittest.TestCase):
    def test_training_is_reproducible_and_model_loads(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            artifact_path=Path(directory)/"language-model.json"
            first=train_and_save(artifact_path)
            first_bytes=artifact_path.read_bytes()
            second=train_and_save(artifact_path)
            self.assertEqual(first_bytes,artifact_path.read_bytes())
            loaded=load_model(artifact_path)
            self.assertEqual(loaded["version"],"language-classifier-v1")
            self.assertEqual(loaded["split_sizes"],{"train":40,"validation":12,"test":12})
            self.assertGreaterEqual(first["metrics"]["neural_network_test"]["accuracy"],0.5)
            self.assertEqual(second["metrics"],first["metrics"])
            self.assertEqual(predict_language("नमस्ते",loaded)["language"],"hi")


class ConversationProviderTests(unittest.TestCase):
    def test_main_chat_uses_recent_history_and_natural_chat_instructions(self):
        history = [
            {"role": "user", "content": "Mera naam Aman hai"},
            {"role": "assistant", "content": "Hi Aman, nice to meet you!"},
        ]
        with patch("app.services.provider.settings", SimpleNamespace(provider_configured=True)), patch("app.services.provider._complete", return_value="Badhiya Aman, bata kya chal raha hai?") as complete:
            answer = tutoring_reply("bas tu bata", "hi", [], history=history)
            self.assertIn("Aman", answer)
            messages = complete.call_args.args[0]
            self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
            self.assertIn("Do not turn every message into a language lesson", messages[0]["content"])
            self.assertEqual(messages[-1]["content"], "bas tu bata")

    def test_context_is_bounded_and_mode_instructions_are_sent(self):
        transcript=[{"role":"user" if index%2==0 else "assistant","content":f"turn {index}"} for index in range(22)]
        with patch("app.services.provider.settings",SimpleNamespace(provider_configured=True)), patch("app.services.provider._complete",return_value="One question at a time.") as complete:
            self.assertEqual(conversation_reply(transcript,mode="interview",language="en",level="beginner",role="intern"),"One question at a time.")
            messages=complete.call_args.args[0]
            self.assertEqual(len(messages),17)
            self.assertIn("exactly one question at a time",messages[0]["content"])
            self.assertEqual(messages[-1]["content"],"turn 21")

    def test_feedback_is_explicitly_text_only(self):
        with patch("app.services.provider.settings",SimpleNamespace(provider_configured=True)), patch("app.services.provider._complete",return_value="Useful feedback") as complete:
            self.assertEqual(conversation_feedback([],mode="interview",level="intermediate"),"Useful feedback")
            self.assertIn("text-only feedback",complete.call_args.args[0][0]["content"])
            self.assertIn("Do not claim to assess pronunciation",complete.call_args.args[0][0]["content"])

    def test_missing_provider_does_not_return_a_scripted_conversation(self):
        with patch("app.services.provider.settings",SimpleNamespace(provider_configured=False)):
            with self.assertRaises(ProviderUnavailable):
                conversation_reply([],mode="casual",language="en",level="intermediate")


class MockInterviewTests(unittest.TestCase):
    def test_mock_interview_prompt_is_role_and_education_aware(self):
        transcript = [{"role": "assistant", "content": "Please introduce yourself."}]
        with patch("app.services.provider.settings", SimpleNamespace(provider_configured=True)), patch("app.services.provider._complete", return_value="Tell me about a project.") as complete:
            answer = conversation_reply(
                transcript, mode="mock_interview", language="en", level="beginner",
                role="Software Developer Intern",
                scenario="Candidate education: Diploma; field of study: Computer Engineering / Computer Science / IT / AI.",
            )
        self.assertEqual(answer, "Tell me about a project.")
        prompt = complete.call_args.args[0][0]["content"]
        self.assertIn("realistic, role-specific job interview", prompt)
        self.assertIn("Software Developer Intern", prompt)
        self.assertIn("Candidate education: Diploma", prompt)
        self.assertIn("exactly one question at a time", prompt)

    def test_mock_interview_offline_greeting_includes_selected_role(self):
        greeting = _offline_reply([], mode="mock_interview", language="en", level="beginner", role="Web Developer Intern")
        self.assertIn("mock interview for Web Developer Intern", greeting)
        self.assertIn("introduce yourself", greeting.lower())

    def test_mock_interview_offline_followup_matches_the_selected_role(self):
        answer = _offline_reply(
            [
                {"role": "assistant", "content": "Introduce yourself."},
                {"role": "user", "content": "I am learning web development."},
                {"role": "assistant", "content": "Tell me about your project."},
                {"role": "user", "content": "I built a portfolio site."},
            ],
            mode="mock_interview", language="en", level="beginner", role="Web Developer Intern",
        )
        self.assertIn("mobile screen", answer)

    def test_spoken_confidence_uses_voice_metrics_and_transcript_clarity(self):
        messages = [
            {"role": "assistant", "content": "Tell me about yourself."},
            {"role": "user", "content": "I am studying computer engineering and I enjoy building practical web projects."},
            {"role": "assistant", "content": "What did you build?"},
            {"role": "user", "content": "I made a responsive portfolio and fixed layout issues on mobile screens."},
            {"role": "assistant", "content": "What did you learn?"},
            {"role": "user", "content": "I learned to break problems down, test the result, and explain my decisions clearly."},
        ]
        metrics = {
            "capture_available": True, "listening_seconds": 22, "speech_seconds": 16,
            "silence_ratio": 0.25, "pitch_median_hz": 145, "pitch_variation": 0.11,
            "volume_variation": 0.4, "word_count": 36, "response_count": 3,
        }
        report = _spoken_confidence_report(messages, voice_metrics=metrics)
        self.assertIsInstance(report["score"], int)
        self.assertTrue(report["voice_sample_available"])
        self.assertIsInstance(report["clarity_fluency_score"], int)
        self.assertIsNotNone(report["vocal_tone_score"])
        self.assertIn("not a psychological", report["limitations"])

    def test_spoken_confidence_does_not_invent_voice_score_without_audio(self):
        report = _spoken_confidence_report([{"role": "user", "content": "I am practising English conversation with a complete sentence."}])
        self.assertIsNone(report["score"])
        self.assertFalse(report["voice_sample_available"])
        self.assertIsNotNone(report["clarity_fluency_score"])

    def test_mock_interview_report_uses_interview_improvement_guidance(self):
        report = _offline_performance_report(
            [{"role": "assistant", "content": "Tell me about yourself."}, {"role": "user", "content": "I am a student."}],
            mode="mock_interview", level="beginner", role="Software Developer Intern",
        )
        self.assertTrue(any("Structure interview answers" in item["issue"] for item in report["improvements"]))

    def test_mock_interview_education_context_is_normalized(self):
        scenario = _scenario_for_options({
            "mode": "mock_interview", "scenario": "", "education_level": "diploma", "education_field": "computer"
        })
        self.assertIn("Diploma", scenario)
        self.assertIn("Computer Engineering", scenario)


class ProviderHTTPTests(unittest.TestCase):
    def setUp(self):
        self.provider_settings = SimpleNamespace(
            provider_configured=True,
            ai_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            ai_api_key="top-secret-test-key",
            ai_model="gemini-3.8-flash",
            ai_timeout_seconds=1,
        )

    @staticmethod
    def _http_error(status=503, detail="The service is overloaded"):
        body = json.dumps({"error": {"message": detail}}).encode("utf-8")
        return HTTPError("https://provider.invalid/chat/completions", status, "provider error", None, io.BytesIO(body))

    def test_retries_transient_503_then_returns_success(self):
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "Hey bhai, bilkul!"}}]}).encode("utf-8"))
        with patch("app.services.provider.settings", self.provider_settings), \
             patch("app.services.provider.urlopen", side_effect=[self._http_error(), response]) as urlopen_mock, \
             patch("app.services.provider.time.sleep") as sleep_mock:
            result = _complete([{"role": "user", "content": "hi"}], max_tokens=100)
        self.assertEqual(result, "Hey bhai, bilkul!")
        self.assertEqual(urlopen_mock.call_count, 2)
        sleep_mock.assert_called_once()

    def test_requests_non_streaming_json_explicitly(self):
        response = io.BytesIO(json.dumps({"choices": [{"message": {"content": "Hello!"}}]}).encode("utf-8"))
        with patch("app.services.provider.settings", self.provider_settings), \
             patch("app.services.provider.urlopen", return_value=response) as urlopen_mock:
            self.assertEqual(_complete([{"role": "user", "content": "hi"}], max_tokens=100), "Hello!")
        sent = json.loads(urlopen_mock.call_args.args[0].data.decode("utf-8"))
        self.assertIs(sent["stream"], False)

    def test_nvidia_nemotron_disables_thinking_and_polls_pending_result(self):
        nvidia_settings = SimpleNamespace(
            provider_configured=True,
            ai_base_url="https://integrate.api.nvidia.com/v1",
            ai_api_key="top-secret-test-key",
            ai_model="nvidia/nemotron-3.5-lightning-30b-a3b",
            ai_timeout_seconds=1,
        )
        pending = io.BytesIO(json.dumps({"requestId": "test-request-123"}).encode("utf-8"))
        pending.status = 202
        completed = io.BytesIO(json.dumps({"choices": [{"message": {"content": "Hello from Nemotron."}}]}).encode("utf-8"))
        completed.status = 200
        with patch("app.services.provider.settings", nvidia_settings), \
             patch("app.services.provider.urlopen", side_effect=[pending, completed]) as urlopen_mock, \
             patch("app.services.provider.time.sleep"):
            result = _complete([{"role": "user", "content": "hi"}], max_tokens=100)
        self.assertEqual(result, "Hello from Nemotron.")
        payload = json.loads(urlopen_mock.call_args_list[0].args[0].data.decode("utf-8"))
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["reasoning_budget"], 0)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(urlopen_mock.call_args_list[1].args[0].get_method(), "GET")
        self.assertTrue(urlopen_mock.call_args_list[1].args[0].full_url.endswith("/status/test-request-123"))

    def test_persistent_503_explains_failure_and_redacts_credentials(self):
        failures = [self._http_error(detail="Service overloaded; key top-secret-test-key") for _ in range(3)]
        with patch("app.services.provider.settings", self.provider_settings), \
             patch("app.services.provider.urlopen", side_effect=failures) as urlopen_mock, \
             patch("app.services.provider.time.sleep"):
            with self.assertRaises(ProviderUnavailable) as raised:
                _complete([{"role": "user", "content": "hi"}], max_tokens=100)
        message = str(raised.exception)
        self.assertIn("HTTP 503", message)
        self.assertIn("3 attempts", message)
        self.assertIn("Service overloaded", message)
        self.assertNotIn("top-secret-test-key", message)
        self.assertEqual(urlopen_mock.call_count, 3)

    def test_auth_and_model_errors_have_actionable_messages(self):
        cases = (
            (401, "API key"),
            (404, "model"),
            (429, "quota"),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                with patch("app.services.provider.settings", self.provider_settings), \
                     patch("app.services.provider.urlopen", side_effect=[self._http_error(status=status) for _ in range(3)]), \
                     patch("app.services.provider.time.sleep"):
                    with self.assertRaises(ProviderUnavailable) as raised:
                        _complete([{"role": "user", "content": "hi"}], max_tokens=100)
                self.assertIn(expected.lower(), str(raised.exception).lower())


class PerformanceReportTests(unittest.TestCase):
    def setUp(self):
        self.provider_settings = SimpleNamespace(
            provider_configured=True,
            ai_base_url="https://provider.example.com/v1",
            ai_api_key="secret-key",
            ai_model="test-model",
            request_timeout_seconds=5,
        )

    def test_unconfigured_provider_raises_provider_unavailable(self):
        with patch("app.services.provider.settings", SimpleNamespace(provider_configured=False)):
            with self.assertRaises(ProviderUnavailable) as cm:
                generate_performance_report([], mode="interview", level="B1")
            self.assertIn("needs a configured AI provider", str(cm.exception))

    def test_short_session_returns_insufficient_evidence_safely(self):
        messages = [
            {"role": "assistant", "content": "Tell me about yourself."},
            {"role": "user", "content": "I am a software engineer."},
        ]
        with patch("app.services.provider.settings", self.provider_settings):
            report = generate_performance_report(messages, mode="interview", level="B1", session_id="test-123")
        self.assertTrue(report["insufficient_evidence"])
        self.assertIsNone(report["overall_score"])
        self.assertIn("too short", report["score_summary"])
        self.assertEqual(report["session_id"], "test-123")
        self.assertEqual(report["session_type"], "interview")
        self.assertIn("Complete a longer practice session", report["recommended_next_practice"]["focus"])

    def test_parse_report_json_handles_code_fences_and_sanitizes(self):
        raw_json = """```json
        {
            "overall_score": 85.6,
            "score_summary": "Strong communication with minor grammatical hesitation.",
            "category_scores": [
                {"name": "Clarity and structure", "score": 90, "explanation": "Structured answers well.", "suggestion": "Keep it up."},
                {"name": "Unknown", "score": "invalid", "explanation": "Could not measure"}
            ],
            "strengths": [
                {"title": "Direct answers", "explanation": "Addressed each question directly.", "example": "I built X."}
            ],
            "improvements": [
                {"issue": "Use STAR format", "excerpt": "We did stuff.", "improved_version": "In my previous role, I...", "why_better": "Clearer structure"}
            ],
            "answer_rewrites": [
                {"original": "We did stuff.", "improved": "I led the development.", "reason": "Active voice"}
            ],
            "recommended_next_practice": {
                "mode": "interview_tech",
                "focus": "Technical deep dive",
                "reason": "Strengthen technical terminology"
            }
        }
        ```"""
        parsed = _parse_report_json(raw_json, "sess-1", "interview")
        self.assertEqual(parsed["overall_score"], 85)
        self.assertEqual(parsed["session_id"], "sess-1")
        self.assertEqual(parsed["session_type"], "interview")
        self.assertFalse(parsed["insufficient_evidence"])
        self.assertEqual(len(parsed["category_scores"]), 2)
        self.assertEqual(parsed["category_scores"][0]["score"], 90)
        self.assertIsNone(parsed["category_scores"][1]["score"])
        self.assertEqual(len(parsed["strengths"]), 1)
        self.assertEqual(len(parsed["improvements"]), 1)
        self.assertEqual(len(parsed["answer_rewrites"]), 1)
        self.assertEqual(parsed["recommended_next_practice"]["mode"], "interview_tech")

    def test_parse_report_json_invalid_format_raises_provider_unavailable(self):
        with self.assertRaises(ProviderUnavailable):
            _parse_report_json("Not a json at all", "sess-1", "interview")

    def test_generate_performance_report_with_mocked_completion(self):
        messages = [
            {"role": "assistant", "content": "Question 1"},
            {"role": "user", "content": "Answer 1"},
            {"role": "assistant", "content": "Question 2"},
            {"role": "user", "content": "Answer 2"},
            {"role": "assistant", "content": "Question 3"},
            {"role": "user", "content": "Answer 3"},
        ]
        mock_response = json.dumps({
            "overall_score": 78,
            "score_summary": "Solid foundation with room for more technical depth.",
            "category_scores": [{"name": "Technical accuracy", "score": 75, "explanation": "Good knowledge"}],
            "strengths": [{"title": "Clear communication", "explanation": "Easy to follow"}],
            "improvements": [{"issue": "Provide metrics", "excerpt": "Improved speed", "improved_version": "Improved speed by 30%"}],
            "answer_rewrites": [],
            "recommended_next_practice": {"mode": "interview_tech", "focus": "System design", "reason": "Practice scaling"},
        })
        with patch("app.services.provider.settings", self.provider_settings), \
             patch("app.services.provider._complete", return_value=mock_response):
            report = generate_performance_report(
                messages, mode="interview_tech", level="B2", role="Backend Engineer", session_id="sess-xyz"
            )
        self.assertEqual(report["overall_score"], 78)
        self.assertEqual(report["session_id"], "sess-xyz")
        self.assertEqual(report["session_type"], "interview_tech")
        self.assertEqual(len(report["category_scores"]), 1)


class DatabaseTests(unittest.TestCase):
    def test_schema_has_user_isolation_and_cascade(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            path=Path(directory)/"test.sqlite3"
            initialize_database(path)
            conn=connect(path)
            try:
                tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"users","sessions","progress","assessment_attempts","recommendation_feedback","conversations"}.issubset(tables))
                self.assertTrue(any(row["name"]=="idx_attempts_user_time" for row in conn.execute("PRAGMA index_list(assessment_attempts)")))
                self.assertTrue(any(row["name"]=="idx_conversations_user_updated" for row in conn.execute("PRAGMA index_list(conversations)")))
                migrations={row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
                self.assertEqual(migrations,{"001_initial","002_conversations","003_modes","004_reports","005_mock_interview"})
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
