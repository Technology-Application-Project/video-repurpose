"""The privacy notice promises that only text reaches the cloud. These tests fail if a request ever carries media."""
import json
import unittest

from pipeline import blueprint, storyboard

SENTINELS = ("/Users/someone/lecture.mp4", "frame-0001.png", "raw-audio.wav", "speaker-face-embedding")


def clips():
    return [{"id": f"c{index}", "start": index * 30, "end": index * 30 + 10, "title": f"Idea {index} about assumptions",
             "takeaway": f"Takeaway {index}.", "excerpt": f"Excerpt {index}.",
             # Local-only fields that must never be sent.
             "video_path": SENTINELS[0], "frame_path": SENTINELS[1], "audio_path": SENTINELS[2], "face": SENTINELS[3]}
            for index in range(3)]


class Capture:
    def __init__(self):
        self.messages = None

    def __call__(self, *, model, format, options, messages):
        if self.messages is None:
            self.messages = [dict(message) for message in messages]
        return {"message": {"content": "not json"}, "provider": "test"}


def user_payload(capture):
    return json.loads(next(message["content"] for message in capture.messages if message["role"] == "user"))


class CloudPayloadTests(unittest.TestCase):
    def test_blueprint_request_carries_only_text_fields(self):
        capture = Capture()
        blueprint.generate_blueprint("model", clips(), "Kids", request=capture)
        payload = user_payload(capture)
        self.assertEqual(set(payload), {"audience", "audience_profile", "presentation", "selected_clips_in_order", "timing", "source_link_available"})
        for clip in payload["selected_clips_in_order"]:
            self.assertEqual(set(clip), {"id", "title", "takeaway", "seconds", "excerpt"})
        self.assertFalse(any(item in json.dumps(capture.messages) for item in SENTINELS))

    def test_storyboard_request_carries_only_text_fields(self):
        capture = Capture()
        with self.assertRaises(ValueError):
            storyboard.generate_storyboard("model", clips(), "Kids", request=capture)
        payload = user_payload(capture)
        self.assertEqual(set(payload), {"audience", "audience_profile", "presentation", "selected_clips_in_order",
                                        "speech_seconds", "total_duration_range", "source_link_available"})
        for clip in payload["selected_clips_in_order"]:
            self.assertEqual(set(clip), {"clip_id", "start", "end", "quote"})
        self.assertFalse(any(item in json.dumps(capture.messages) for item in SENTINELS))


if __name__ == "__main__":
    unittest.main()
