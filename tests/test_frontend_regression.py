"""Regression checks for browser-side event wiring."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class FrontendRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
        cls.html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")

    def test_document_level_event_handlers_use_document_directly(self):
        # The `$` helper only accepts CSS selector strings. Passing `document`
        # to querySelector throws and aborts the rest of app.js at load time.
        self.assertNotIn("$(document)", self.js)
        self.assertGreaterEqual(self.js.count("document.addEventListener("), 4)

    def test_main_chat_keeps_and_sends_recent_conversation_history(self):
        self.assertIn("standaloneMessages.slice(-16)", self.js)
        self.assertIn('history:standaloneMessages.slice(-16)', self.js)
        self.assertIn('standaloneMessages=[...standaloneMessages,{role:"user",content:text},{role:"assistant",content:data.reply}]', self.js)

    def test_static_id_selectors_exist_in_html(self):
        html_ids = set(re.findall(r'\bid=["\']([^"\']+)', self.html))
        js_ids = set(re.findall(r'\$\(["\']#([A-Za-z0-9_-]+)["\']\)', self.js))
        self.assertEqual(set(), js_ids - html_ids, "Some static JavaScript selectors have no matching HTML element.")

    def test_voice_practice_and_report_controls_are_wired(self):
        self.assertIn('id="voice-practice-mode"', self.html)
        self.assertIn('Stop &amp; Generate Report', self.html)
        self.assertIn('function startVoicePractice()', self.js)
        self.assertIn('function startVoicePracticeListening()', self.js)
        self.assertIn('const VOICE_SPEEDS=["0.5","1","1.25","1.5"]', self.js)
        self.assertIn('utterance.rate=VOICE_SPEEDS.includes(String(selectedRate))?selectedRate:1', self.js)
        self.assertIn('function finishVoiceMetricsCapture()', self.js)
        self.assertIn('spoken_confidence', self.js)
        self.assertIn('id="report-voice-confidence"', self.html)
        self.assertIn('id="voice-speed"', self.html)
        self.assertIn('id="cancel-session"', self.html)
        self.assertIn('value="hinglish"', self.html)
        self.assertIn('value="mr"', self.html)
        self.assertIn("Cancel this conversation", self.js)
        self.assertIn('VOICE_SPEED_STORAGE_KEY', self.js)
        self.assertIn('renderReport(result.report)', self.js)


    def test_education_based_mock_interview_controls_are_wired(self):
        for element_id in ("mock-interview-config", "education-level", "education-field", "mock-interview-role"):
            self.assertIn(f'id="{element_id}"', self.html)
        self.assertIn('value="mock_interview"', self.html)
        self.assertIn("MOCK_ROLE_BANK", self.js)
        self.assertIn("updateMockRoles", self.js)
        self.assertIn('education_level:isMockInterview?', self.js)
        self.assertIn('education_field:isMockInterview?', self.js)
        self.assertIn("Stop &amp; Generate Report", self.html)

    def test_lesson_practice_fetches_fresh_questions(self):
        self.assertIn('/question?${params.toString()}', self.js)
        self.assertIn('question_id:activeQuestionId', self.js)
        self.assertIn('Next question ↗', self.js)


if __name__ == "__main__":
    unittest.main()
