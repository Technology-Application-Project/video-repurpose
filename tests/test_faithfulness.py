import unittest

from pipeline.faithfulness import audit_storyboard
from pipeline.transcript_quality import inspect_transcript


CLIPS = [{"id": "c1", "start": 0.0, "end": 5.0}, {"id": "c2", "start": 5.0, "end": 10.0}]
SEGMENTS = [
    {"start": 0.0, "end": 4.0, "text": "Regular physical activity may improve concentration among some students."},
    {"start": 5.0, "end": 9.0, "text": "The second example is about student habits."},
]


def board(title, ids=None):
    return {"opening": {"title": title, "subtitle": "", "evidence_ids": ids if ids is not None else ["c1"]},
            "transitions": [{"label": "Student habits", "evidence_ids": ["c1", "c2"]}],
            "ending": {"takeaway": "Regular physical activity may improve concentration", "evidence_ids": ["c1"]}}


class TranscriptQualityTests(unittest.TestCase):
    def test_valid_transcript(self):
        self.assertTrue(inspect_transcript(SEGMENTS)["valid"])

    def test_missing_or_reversed_timestamp(self):
        self.assertFalse(inspect_transcript([{"start": 2, "end": 1, "text": "hi"}])["valid"])

    def test_nan_rejected(self):
        self.assertFalse(inspect_transcript([{"start": float("nan"), "end": 4, "text": "hi"}])["valid"])

    def test_empty_rejected(self):
        self.assertFalse(inspect_transcript([])["valid"])

    def test_out_of_order_rejected(self):
        self.assertFalse(inspect_transcript([SEGMENTS[1], SEGMENTS[0]])["valid"])

    def test_overlap_is_warning_not_silent_deletion(self):
        result = inspect_transcript([{"start": 0, "end": 2, "text": "one"}, {"start": 1, "end": 3, "text": "two"}])
        self.assertTrue(result["valid"])
        self.assertTrue(result["warnings"])

    def test_word_timing_warning(self):
        data = [{"start": 0, "end": 2, "text": "hello", "words": [{"start": 4, "end": 5, "word": "hello"}]}]
        self.assertTrue(inspect_transcript(data)["warnings"])


class FaithfulnessTests(unittest.TestCase):
    def test_possible_overstatement_flagged(self):
        review = audit_storyboard(board("Regular physical activity always improves concentration"), CLIPS, SEGMENTS)
        self.assertIn("possible_overstatement_of_certainty", review["checks"][0]["reasons"])

    def test_supported_candidate_is_not_automatically_approved(self):
        review = audit_storyboard(board("Regular physical activity may improve concentration"), CLIPS, SEGMENTS)
        self.assertEqual(review["checks"][0]["status"], "evidence_candidate_found")
        self.assertTrue(review["human_review_required"])

    def test_missing_evidence_reference(self):
        review = audit_storyboard(board("Regular activity", ["nonexistent"]), CLIPS, SEGMENTS)
        self.assertIn("missing_or_invalid_evidence_ids", review["checks"][0]["reasons"])

    def test_context_is_restricted_to_clip_time(self):
        review = audit_storyboard(board("The second example is about student habits"), CLIPS, SEGMENTS)
        self.assertIn("low_lexical_evidence_overlap", review["checks"][0]["reasons"])

    def test_negation_warning(self):
        review = audit_storyboard(board("Physical activity does not improve concentration"), CLIPS, SEGMENTS)
        self.assertIn("possible_negation_mismatch", review["checks"][0]["reasons"])

    def test_repeatable_serializable_report(self):
        import json
        first = audit_storyboard(board("Physical activity"), CLIPS, SEGMENTS)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(audit_storyboard(board("Physical activity"), CLIPS, SEGMENTS), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
