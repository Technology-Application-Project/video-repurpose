import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pipeline import cards
from pipeline.media import PRESENTATION
from pipeline.storyboard import (BRIDGE_MAX_WORDS, _THROAT_CLEARING, draft_storyboard, generate_storyboard,
                                 validate_storyboard)
from pipeline.utils import ffmpeg_bin, media_duration

KICKERS = PRESENTATION["General public"]["kickers"]


def clips(count=3):
    return [{"id": f"c{index}", "start": index * 20, "end": index * 20 + 12, "title": "Question assumptions",
             "takeaway": "Examine the assumptions behind advice.", "excerpt": "Examine the assumptions behind advice."}
            for index in range(count)]


def draft(audience="General public", media_directory=None, count=3):
    storyboard = draft_storyboard(clips(count), audience)
    storyboard["opening"].update(title="Can classrooms build rich nations?", subtitle="Skills, wages and growth")
    storyboard["ending"].update(takeaway="Education can transform a nation's economy.",
                                question="Can your community invest more?", cta="Study Singapore's 1960s education plan.")
    for index, join in enumerate(storyboard["transitions"]):
        join["label"] = ["Why do nations differ?", "How did Singapore escape poverty?", "Who pays for schools?", "What changes next?"][index]
    if media_directory:
        assets = {}
        for name, color in (("opening", "coral"), ("ending", "teal")):
            path = Path(media_directory) / f"{name}.png"
            Image.new("RGB", (640, 360), color).save(path)
            assets[name] = {"path": str(path), "sha256": "x", "kind": "image"}
        storyboard["media_assets"] = assets
    return storyboard


class CardLayoutTests(unittest.TestCase):
    def test_text_that_cannot_fit_readably_fails_instead_of_shrinking_silently(self):
        long = " ".join(["unbelievably"] * 40)
        for width, height in ((1280, 720), (720, 1280)):
            with self.assertRaisesRegex(ValueError, "readable size"):
                cards.fit_card([("Kicker", "kicker", False), (long, "title", False)], width, height,
                               opening=False, audience="General public")

    def test_allowed_wording_fits_both_canvases_for_every_audience(self):
        for audience in PRESENTATION:
            storyboard = draft(audience)
            cards.check_card_text("opening", storyboard, PRESENTATION[audience]["kickers"])
            cards.check_card_text("divider", storyboard, PRESENTATION[audience]["kickers"], number=2)
            cards.check_card_text("ending", storyboard, PRESENTATION[audience]["kickers"])

    def test_two_beat_ending_gives_takeaway_a_larger_size_than_one_crowded_card(self):
        storyboard = draft()
        beats, _ = cards.card_beats("ending", storyboard, KICKERS)
        self.assertEqual(len(beats), 2)
        crowded = [section for beat in beats for section in beat if section[1] != "kicker"]
        alone = cards.fit_card(beats[0], 1280, 720, opening=False, audience="General public")
        together = cards.fit_card([("Kicker", "kicker", False), *crowded], 1280, 720, opening=False, audience="General public")
        self.assertGreaterEqual(alone["size"], together["size"])
        self.assertEqual([text for text, role, _ in beats[0] if role == "title"], [storyboard["ending"]["takeaway"]])
        self.assertEqual([text for text, role, _ in beats[1] if role == "question"], [storyboard["ending"]["cta"]])

    def test_short_url_is_a_clean_on_screen_form(self):
        from pipeline.utils import short_url
        self.assertEqual(short_url("https://www.example.org/lectures/week-3/?utm_source=x#t=5"), "example.org/lectures/week-3")
        self.assertEqual(short_url("http://example.org/"), "example.org")
        self.assertEqual(short_url("https://www.example.org/a/b", host_only=True), "example.org")
        self.assertEqual(short_url("https://www.example.edu/courses/economics/week-3-lecture?utm=x"), "example.edu/.../week-3-lecture")
        long = short_url("https://example.edu/courses/economics/lecture-12-growth-and-institutions")
        self.assertEqual(len(long), 32)
        self.assertTrue(long.startswith("example.edu/.../lecture-12") and long.endswith("..."))
        for bad in ("", "example.org/lecture", "ftp://example.org/a", "not a url", "https://", None):
            self.assertEqual(short_url(bad), "", bad)

    def test_source_link_appears_on_the_ending_card_and_replaces_the_default_credit(self):
        storyboard = draft()
        storyboard["source_url"] = "https://www.example.org/lectures/week-3?utm=x#t=5"
        beats, corner = cards.card_beats("ending", storyboard, KICKERS)
        self.assertEqual(beats[1][-1], ("example.org/lectures/week-3", "link", False))
        self.assertFalse(any(role == "link" for _, role, _ in beats[0]))
        self.assertEqual(corner, ["Source: example.org"])
        storyboard["ending"]["credit"] = "Prof. Lee, Week 3"
        self.assertEqual(cards.card_beats("ending", storyboard, KICKERS)[1], ["Prof. Lee, Week 3"])
        plain, corner = cards.card_beats("ending", draft(), KICKERS)
        self.assertFalse(any(role == "link" for _, role, _ in plain[0]))
        self.assertEqual(plain[1][-1], (cards.EXAMPLE_LINK, "link", False))
        self.assertEqual(cards.EXAMPLE_LINK, "example.edu/lecture")
        self.assertEqual(corner, ["Source lecture"])
        self.assertEqual(corner, ["Source lecture"])
        opening, _ = cards.card_beats("opening", {**storyboard, "source_url": "https://example.org/a"}, KICKERS)
        self.assertFalse(any(role == "link" for _, role, _ in opening[0]))

    def test_ending_with_a_source_link_fits_both_canvases_for_every_audience(self):
        url = "https://sub.very-long-university-domain.example.edu/courses/economics/lecture-12-growth"
        for audience in PRESENTATION:
            with self.subTest(audience=audience):
                cards.check_card_text("ending", {**draft(audience), "source_url": url}, PRESENTATION[audience]["kickers"])

    def test_credit_is_a_corner_label_not_part_of_the_text_stack(self):
        beats, corner = cards.card_beats("ending", draft(media_directory=tempfile.gettempdir()), KICKERS)
        self.assertEqual(corner, ["Source lecture", "AI illustration"])
        self.assertTrue(all(role != "credit" for beat in beats for _, role, _ in beat))

    def test_bridge_and_ending_type_is_larger_than_before(self):
        beats, _ = cards.card_beats("divider", draft(), KICKERS, number=2)
        for width, height in ((1280, 720), (720, 1280)):
            layout = cards.fit_card(beats[0], width, height, opening=False, audience="General public",
                                    ratio=cards.TYPE_RATIO["divider"])
            self.assertGreater(layout["size"], round(width * 0.045) if width < height else 48)

    def test_portrait_blocks_sit_below_centre_and_landscape_stays_centred(self):
        beats, _ = cards.card_beats("divider", draft(), KICKERS, number=2)

        def middle(width, height):
            layout = cards.fit_card(beats[0], width, height, opening=False, audience="General public")
            last = layout["blocks"][-1]
            return (layout["chip"]["y"] + last["y"] + last["height"]) / 2 / height

        self.assertAlmostEqual(middle(720, 1280), cards.PORTRAIT_ANCHOR, delta=0.03)
        self.assertAlmostEqual(middle(1280, 720), 0.5, delta=0.03)

    def test_tall_portrait_text_stays_inside_the_frame(self):
        beats, _ = cards.card_beats("opening", draft(), KICKERS)
        layout = cards.fit_card(beats[0], 720, 1280, opening=True, audience="General public")
        last = layout["blocks"][-1]
        self.assertGreaterEqual(layout["chip"]["y"], layout["padding"])
        self.assertLessEqual(last["y"] + last["height"], 1280 - layout["padding"])

    def test_accent_marks_the_first_matching_word_with_its_punctuation(self):
        self.assertEqual(cards._accent_span(["Can learning make", "you richer?"], "Richer"), (1, 4, 11))
        self.assertIsNone(cards._accent_span(["Can learning make"], "richer"))
        self.assertIsNone(cards._accent_span(["Can learning make"], ""))
        storyboard = draft()
        storyboard["opening"]["accent"] = "nations?"
        self.assertEqual(cards.card_accents("opening", storyboard), {storyboard["opening"]["title"]: "nations?"})
        self.assertEqual(cards.card_accents("ending", draft()), {})
        beats, _ = cards.card_beats("opening", storyboard, KICKERS)
        layout = cards.fit_card(beats[0], 1280, 720, opening=True, audience="General public",
                                accents=cards.card_accents("opening", storyboard))
        title = next(block for block in layout["blocks"] if block["role"] == "title")
        self.assertIsNotNone(title["accent_span"])
        layer = cards.render_layer(layout, [("line", layout["blocks"].index(title), title["accent_span"][0])])
        colors = {pixel[:3] for pixel in layer.getdata() if pixel[3] == 255}
        self.assertIn(layout["accent"], colors)
        self.assertIn((255, 255, 255), colors)

    def test_opening_chip_shows_the_measured_runtime_only_when_known(self):
        self.assertEqual(cards.runtime_label(42.64), "43 sec")
        self.assertEqual(cards.runtime_label(120), "2 min")
        self.assertEqual(cards.runtime_label(None), "")
        storyboard = draft()
        plain, _ = cards.card_beats("opening", storyboard, KICKERS)
        self.assertEqual(plain[0][0][0], KICKERS["opening"])
        storyboard["measured_seconds"] = 42.64
        timed, _ = cards.card_beats("opening", storyboard, KICKERS)
        self.assertEqual(timed[0][0][0], f"{KICKERS['opening']} \u2022 43 sec")

    def test_every_audience_font_has_the_glyphs_the_chip_uses(self):
        from PIL import ImageFont
        for audience, presentation in PRESENTATION.items():
            path = next((path for path in presentation["fonts"] if os.path.exists(path)), None)
            if path is None:
                continue
            font = ImageFont.truetype(path, 60)
            absent = bytes(font.getmask("\U0010FFFF"))
            self.assertNotEqual(bytes(font.getmask("\u2022")), absent, f"{audience} font lacks the chip separator")

    def test_drift_direction_alternates_and_the_ending_stays_centred(self):
        self.assertEqual([cards.card_drift("opening"), cards.card_drift("divider", 2), cards.card_drift("divider", 3),
                          cards.card_drift("ending")], [1, -1, 1, 0])

    def test_blur_is_available_but_bridges_keep_the_unblurred_source_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            picture = Path(directory) / "frame.png"
            stripes = Image.new("RGB", (640, 360), "white")
            for x in range(0, 640, 8):
                stripes.paste("black", (x, 0, x + 4, 360))
            stripes.save(picture)

            def contrast(blur):
                image = cards.render_background(640, 360, picture=picture, audience="General public", opening=False,
                                                blur=blur).convert("L").crop((300, 100, 340, 120))
                low, high = image.getextrema()
                return high - low

            self.assertGreater(contrast(False), 4 * contrast(True))
            storyboard = draft()
            with patch.object(cards, "render_background", wraps=cards.render_background) as render:
                for still in (picture, None):
                    cards.build_card("divider", storyboard, KICKERS, Path(directory), "d", 640, 360, duration=2.0,
                                     narration_seconds=1.4, number=2, still=still)
            self.assertEqual([call.kwargs["blur"] for call in render.call_args_list], [False, False])


def _luminance(pixels):
    import numpy as np
    linear = np.where(pixels <= 0.03928, pixels / 12.92, ((pixels + 0.055) / 1.055) ** 2.4)
    return linear @ np.array([0.2126, 0.7152, 0.0722])


def _illustrations(directory, width, height):
    """Synthetic art spanning the range a model can produce: bright, dark, saturated and noisy."""
    import numpy as np
    rng = np.random.default_rng(7)
    palettes = {"bright": [(250, 240, 200), (200, 235, 250), (255, 210, 220), (255, 255, 255)],
                "dark": [(0, 0, 0), (20, 10, 40), (0, 30, 20), (10, 10, 10)],
                "saturated": [(255, 255, 0), (0, 255, 255), (255, 0, 255), (0, 0, 255)]}
    paths = {}
    for name, colors in palettes.items():
        image = Image.new("RGB", (width, height), colors[0])
        draw = __import__("PIL.ImageDraw", fromlist=["ImageDraw"]).Draw(image)
        for _ in range(40):
            x, y = int(rng.integers(0, width)), int(rng.integers(0, height))
            draw.ellipse((x, y, x + int(rng.integers(20, width // 3)), y + int(rng.integers(20, height // 2))),
                         fill=colors[int(rng.integers(0, len(colors)))])
        paths[name] = Path(directory) / f"{name}.png"
        image.save(paths[name])
    paths["noise"] = Path(directory) / "noise.png"
    Image.fromarray(rng.integers(0, 256, (height, width, 3), dtype="uint8")).save(paths["noise"])
    return paths


class ContrastTests(unittest.TestCase):
    def test_text_stays_readable_on_any_illustration_for_every_audience(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            for width, height in ((640, 360), (360, 640)):
                art = _illustrations(directory, width, height)
                for audience in PRESENTATION:
                    storyboard = draft(audience)
                    storyboard["opening"]["accent"] = "nations?"
                    storyboard["transitions"][0]["accent"] = "Singapore"
                    storyboard["ending"]["accent"] = "transform"
                    kickers = PRESENTATION[audience]["kickers"]
                    look = cards.card_look(audience)
                    for kind in ("opening", "divider", "ending"):
                        beats, _ = cards.card_beats(kind, storyboard, kickers, number=2)
                        for beat in beats:
                            layout = cards.fit_card(beat, width, height, opening=kind == "opening", audience=audience,
                                                    ratio=cards.TYPE_RATIO.get(kind, cards.TYPE_RATIO["ending"]),
                                                    accents=cards.card_accents(kind, storyboard, 2))
                            for name, path in art.items():
                                background = cards.render_background(width, height, picture=path, audience=audience,
                                                                     opening=kind == "opening").convert("RGB")
                                pixels = np.asarray(background, dtype=float) / 255
                                for block in layout["blocks"]:
                                    left = int(min(block["x"]))
                                    right = int(max(x + w for x, w in zip(block["x"], block["widths"])))
                                    region = pixels[block["y"]:block["y"] + block["height"], left:right]
                                    low, high = np.percentile(_luminance(region), [3, 97])
                                    ink = float(_luminance(np.array(block["color"], dtype=float) / 255))
                                    worst = min((max(value, ink) + 0.05) / (min(value, ink) + 0.05) for value in (low, high))
                                    needed = 3.0 if block["color"] == look["accent_text"] else 4.5
                                    self.assertGreaterEqual(worst, needed, f"{audience} {kind} {name} {width}x{height}: {worst:.2f}")
                                    if block["accent_span"]:
                                        accent = float(_luminance(np.array(look["accent_text"], dtype=float) / 255))
                                        worst = min((max(value, accent) + 0.05) / (min(value, accent) + 0.05) for value in (low, high))
                                        self.assertGreaterEqual(worst, 3.0, f"{audience} {kind} {name} accent word: {worst:.2f}")


class CardScheduleTests(unittest.TestCase):
    def build(self, kind, duration, narration, number=1, storyboard=None):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        return cards.build_card(kind, storyboard or draft(), KICKERS, Path(self.directory.name), kind, 640, 360,
                                duration=duration, narration_seconds=narration, number=number)

    def test_audiences_differ_in_how_they_move_and_look(self):
        motions = {audience: cards.card_motion(audience) for audience in PRESENTATION}
        self.assertEqual([motions[audience]["zoom"] for audience in ("Researcher", "General public", "Kids")], [1.06, 1.15, 1.28])
        for key in ("zoom", "fade", "slide", "ease", "reveal"):
            self.assertEqual(len({str(motion[key]) for motion in motions.values()}), 3, key)
        looks = {audience: cards.card_look(audience) for audience in PRESENTATION}
        self.assertEqual(len({look["chip_radius"] for look in looks.values()}), 2)
        self.assertGreater(sum(looks["Kids"]["scrim"]) / 3, 200, "Kids card veil is light")
        self.assertLess(sum(looks["Researcher"]["scrim"]) / 3, 30, "Researcher veil is near-charcoal")
        self.assertEqual(cards.card_drift("opening", 1, "Researcher"), 0)
        self.assertEqual(cards.card_drift("opening", 1, "Kids"), 1)

    def test_easing_expressions_overshoot_only_for_back(self):
        self.assertEqual(cards._ease("linear", "u"), "(u)")
        self.assertIn("2.70158", cards._ease("back", "u"))
        self.assertNotIn("2.70158", cards._ease("quad", "u"))

    def test_researcher_reveals_the_whole_title_together_and_kids_reveal_word_by_word(self):
        def starts(audience, kind="opening"):
            storyboard = draft(audience)
            kickers = PRESENTATION[audience]["kickers"]
            beats, _ = cards.card_beats(kind, storyboard, kickers)
            layout = cards.fit_card(beats[0], 1280, 720, opening=kind == "opening", audience=audience)
            motion = cards.card_motion(audience)["reveal"]
            return [(moment, step) for moment, step in cards._beat_times(layout, 0.0, 3.0, 0.15, 2.15, motion)
                    if step[0] != "chip" and layout["blocks"][step[1]]["role"] == "title"]

        title = starts("Researcher")
        self.assertEqual(len({round(moment, 2) for moment, _ in title}), 1)
        words = starts("Kids")
        self.assertTrue(all(step[0] == "word" for _, step in words))
        self.assertEqual(len(words), len(draft("Kids")["opening"]["title"].split()))
        self.assertEqual(sorted(moment for moment, _ in words), [moment for moment, _ in words])
        self.assertGreater(words[-1][0], words[0][0])
        general = starts("General public")
        self.assertTrue(all(step[0] == "line" for _, step in general))

    def test_kids_ending_makes_the_spoken_call_to_action_the_hero_and_others_keep_the_question(self):
        kids = draft("Kids")
        beats, _ = cards.card_beats("ending", kids, PRESENTATION["Kids"]["kickers"])
        self.assertEqual([text for text, role, _ in beats[1] if role == "title"], [kids["ending"]["cta"]])
        self.assertEqual([text for text, role, _ in beats[1] if role == "question"], [kids["ending"]["question"]])
        researcher = draft("Researcher")
        beats, _ = cards.card_beats("ending", researcher, PRESENTATION["Researcher"]["kickers"])
        self.assertEqual([text for text, role, _ in beats[1] if role == "title"], [researcher["ending"]["question"]])

    def test_kids_bridge_number_is_a_badge_not_chip_text(self):
        kids = draft("Kids")
        self.assertEqual(cards.card_badge("divider", kids, 3), "03")
        self.assertIsNone(cards.card_badge("divider", draft("Researcher"), 3))
        self.assertIsNone(cards.card_badge("opening", kids, 1))
        beats, _ = cards.card_beats("divider", kids, PRESENTATION["Kids"]["kickers"], number=3)
        self.assertEqual(beats[0][0][0], "Next up!")
        layout = cards.fit_card(beats[0], 1280, 720, opening=False, audience="Kids", badge="03")
        self.assertGreater(layout["chip"]["badge"]["d"], 1.8 * layout["chip"]["h"])
        self.assertLessEqual(layout["chip"]["x"] + layout["chip"]["row_w"], 1280 - layout["padding"])
        layer = cards.render_layer(layout, [("chip",)])
        self.assertIn(cards.card_look("Kids")["accent_text"], {pixel[:3] for pixel in layer.getdata() if pixel[3] == 255})
        researcher, _ = cards.card_beats("divider", draft("Researcher"), PRESENTATION["Researcher"]["kickers"], number=3)
        self.assertEqual(researcher[0][0][0], "Next evidence  03")

    def test_decor_arrives_as_timed_layers_with_one_entrance_per_audience(self):
        kinds = {}
        for audience in PRESENTATION:
            with tempfile.TemporaryDirectory() as directory:
                layers = cards.decor_layers(audience, 640, 360, Path(directory), "d", 0.4)
                kinds[audience] = sorted({layer["fx"]["kind"] for layer in layers})
                self.assertTrue(all(layer["decor"] and not layer["slide"] for layer in layers))
        self.assertEqual(kinds, {"Researcher": ["grow"], "General public": ["slide"], "Kids": ["pop"]})
        with tempfile.TemporaryDirectory() as directory:
            starts = [layer["start"] for layer in cards.decor_layers("Kids", 640, 360, Path(directory), "d", 0.4)]
        self.assertEqual(starts, sorted(starts))
        self.assertGreater(len(set(starts)), 3)

    def test_decor_is_no_longer_baked_into_the_background(self):
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory) / "plain.png"
            Image.new("RGB", (640, 360), "white").save(plain)
            image = cards.render_background(640, 360, picture=plain, audience="Kids", opening=True).convert("RGB")
            self.assertEqual(len({image.getpixel((51, 50)), image.getpixel((320, 50))}), 1, "no stars or dots in the background")

    def test_every_reveal_leaves_time_to_read_and_the_chip_comes_first(self):
        for kind, duration, narration in (("opening", 3.0, 2.0), ("divider", 2.4, 1.6), ("ending", 6.7, 6.2)):
            with self.subTest(kind=kind):
                _, layers = self.build(kind, duration, narration, number=2)
                text_layers = [layer for layer in layers if layer["slide"]]
                self.assertEqual(sorted(layer["start"] for layer in text_layers), [layer["start"] for layer in text_layers])
                self.assertTrue(all(layer["start"] <= duration - cards.MIN_HOLD + 1e-6 for layer in text_layers))
                self.assertLess(text_layers[0]["start"], 0.2)
                _, corner = cards.card_beats(kind, draft(), KICKERS, number=2)
                self.assertNotIn("AI illustration", corner)
                self.assertEqual(bool([layer for layer in layers if not layer["slide"] and not layer.get("decor")
                                       and not layer.get("static")]), bool(corner))

    def test_opening_adds_motes_opening_scrim_sweep_and_accent_underline_for_every_audience(self):
        for audience in PRESENTATION:
            with self.subTest(audience=audience), tempfile.TemporaryDirectory() as directory:
                storyboard = draft(audience, media_directory=directory)
                storyboard["opening"]["accent"] = "nations?"
                _, layers = cards.build_card("opening", storyboard, PRESENTATION[audience]["kickers"], Path(directory),
                                             "o", 640, 360, duration=3.0, narration_seconds=2.4)
                effects = [layer for layer in layers if layer.get("fx") and layer.get("decor")]
                kinds = [layer["fx"]["kind"] for layer in effects]
                self.assertIn("flash", kinds)
                kinds = [kind for kind in kinds if kind != "flash"]
                effects = [layer for layer in effects if layer["fx"]["kind"] != "flash"]
                slams = [layer for layer in layers if layer.get("fx") and not layer.get("decor")]
                self.assertEqual(bool(slams), audience != "Researcher", "word-by-word audiences slam each title word in")
                self.assertEqual(kinds[:3], ["float", "float", "open"], "motes, colour wash, scrim band")
                self.assertIn("pop", kinds)
                self.assertGreaterEqual(kinds.count("grow"), 3, "two rules and the accent underline")
                self.assertEqual(kinds.count("slide"), 3 if audience == "General public" else 2, "sweep, exit swoosh, wedge")
                self.assertEqual(effects[-1]["fx"]["kind"], "slide", "exit swoosh is the last effect")
                self.assertAlmostEqual(effects[-1]["start"] + effects[-1]["fx"]["seconds"], 3.0 - 0.05, delta=0.1)
                pulse = [layer for layer in effects if layer["fx"]["kind"] == "pulse"]
                underline = [layer for layer in effects if "underline" in layer["png"].name]
                self.assertEqual(len(pulse), 1)
                self.assertGreater(pulse[0]["start"], underline[0]["start"] - 0.2)
                self.assertLessEqual(pulse[0]["start"] + pulse[0]["fx"]["seconds"], 3.0 - cards.EXIT_SECONDS + 0.3)
                scrim = next(layer for layer in effects if layer["fx"]["kind"] == "open")
                self.assertEqual(scrim["fx"]["close"], 3.0 - cards.EXIT_SECONDS)
                self.assertTrue(all(layer["end"] == 3.0 - 0.1 for layer in layers if layer["slide"]), "text clears with the card")
                self.assertGreater(cards.card_motion(audience, "opening")["zoom"], 1.0)
                self.assertEqual(cards.card_motion(audience, "divider")["zoom"], 1.0)

    def test_opening_sting_is_synthesised_to_the_flash_pulse_and_swoosh_timing(self):
        import wave
        with tempfile.TemporaryDirectory() as directory:
            storyboard = draft(media_directory=directory)
            storyboard["opening"]["accent"] = "nations?"
            _, layers = cards.build_card("opening", storyboard, KICKERS, Path(directory), "o", 640, 360,
                                         duration=3.0, narration_seconds=2.4)
            path = cards.opening_sting(layers, Path(directory) / "o.mp4", 3.0)
            with wave.open(str(path)) as handle:
                self.assertEqual((handle.getframerate(), handle.getnchannels()), (44100, 1))
                self.assertEqual(handle.getnframes(), 3 * 44100)
                samples = __import__("numpy").frombuffer(handle.readframes(handle.getnframes()), dtype="<i2") / 32768
            self.assertLess(abs(samples).max(), 0.9)
            self.assertGreater(abs(samples[:4000]).max(), 0.2, "hit at the flash")
            self.assertGreater(abs(samples[int(2.6 * 44100):]).max(), 0.02, "whoosh at the exit")
            self.assertIsNone(cards.opening_sting([layer for layer in layers if layer.get("fx", {}).get("kind") != "flash"],
                                                  Path(directory) / "x.mp4", 3.0))

    def test_opening_without_an_accent_word_has_no_underline(self):
        with tempfile.TemporaryDirectory() as directory:
            _, layers = cards.build_card("opening", draft(media_directory=directory), KICKERS, Path(directory), "o",
                                         640, 360, duration=3.0, narration_seconds=2.4)
        self.assertNotIn("pop", [layer["fx"]["kind"] for layer in layers if layer.get("fx") and "glow" in layer["png"].name])
        self.assertFalse([layer for layer in layers if layer.get("fx") and "underline" in layer["png"].name])

    def test_title_lines_appear_as_the_voice_reaches_them(self):
        _, layers = self.build("opening", 3.4, 2.6)
        starts = [layer["start"] for layer in layers if layer["slide"]]
        self.assertGreater(len(set(round(start, 2) for start in starts)), 2)
        self.assertGreater(starts[-1], starts[1])

    def test_ending_switches_beats_with_the_first_beat_fading_out(self):
        _, layers = self.build("ending", 6.7, 6.2)
        first = [layer for layer in layers if layer["slide"] and layer["end"] is not None]
        second = [layer for layer in layers if layer["slide"] and layer["end"] is None]
        self.assertTrue(first and second)
        switch = first[0]["end"]
        self.assertTrue(all(layer["end"] == switch for layer in first))
        self.assertTrue(all(layer["start"] >= switch - 0.01 for layer in second))
        self.assertGreaterEqual(switch, 1.3)

    def test_bridges_use_generated_illustrations_alternating_without_mirroring(self):
        with tempfile.TemporaryDirectory() as media:
            storyboard = draft(media_directory=media, count=5)
            with patch.object(cards, "render_background", wraps=cards.render_background) as render:
                for number in (2, 3, 4, 5):
                    self.build("divider", 2.0, 1.4, number=number, storyboard=storyboard)
            pictures = [(call.kwargs["picture"].name, call.kwargs["mirror"]) for call in render.call_args_list]
        self.assertEqual(pictures, [("opening.png", False), ("ending.png", False), ("opening.png", False), ("ending.png", False)])


@unittest.skipUnless(os.environ.get("RUN_MEDIA_TESTS") == "1", "Set RUN_MEDIA_TESTS=1 for ffmpeg integration tests")
class AnimatedCardVideoTests(unittest.TestCase):
    def frame(self, video, second):
        out = subprocess.run([ffmpeg_bin(), "-v", "error", "-ss", str(second), "-i", str(video), "-frames:v", "1",
                              "-f", "image2pipe", "-vcodec", "png", "-"], check=True, capture_output=True)
        return Image.open(io.BytesIO(out.stdout)).convert("RGB")

    def test_text_is_absent_at_first_then_revealed_and_duration_is_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            storyboard = draft(media_directory=directory)
            for kind, duration, narration in (("opening", 3.0, 2.0), ("ending", 6.5, 6.0)):
                with self.subTest(kind=kind):
                    background, layers = cards.build_card(kind, storyboard, KICKERS, work, kind, 640, 360,
                                                          duration=duration, narration_seconds=narration)
                    video = cards.animated_card_video(background, layers, work / f"{kind}.mp4", 640, 360, duration)
                    self.assertAlmostEqual(media_duration(video), duration, delta=0.1)
                    early, late = self.frame(video, 0.03), self.frame(video, duration - 0.15)
                    gap = sum(abs(a - b) for a, b in zip(early.convert("L").resize((64, 36)).tobytes(),
                                                         late.convert("L").resize((64, 36)).tobytes())) / (64 * 36)
                    self.assertGreater(gap, 3, "revealed text must change the frame")
                    self.assertTrue(len(late.getcolors(640 * 360)) > 50)

    def test_every_audience_renders_with_its_own_decor_entrance_and_motion(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            for audience in PRESENTATION:
                with self.subTest(audience=audience):
                    storyboard = draft(audience, media_directory=directory)
                    background, layers = cards.build_card("opening", storyboard, PRESENTATION[audience]["kickers"], work,
                                                          audience[:3], 640, 360, duration=3.0, narration_seconds=2.0)
                    video = cards.animated_card_video(background, layers, work / f"{audience[:3]}.mp4", 640, 360, 3.0,
                                                      audience=audience, drift=cards.card_drift("opening", 1, audience))
                    self.assertAlmostEqual(media_duration(video), 3.0, delta=0.1)
                    if audience == "Kids":
                        def yellow(frame):
                            corner = frame.crop((20, 20, 100, 90))
                            return sum(1 for r, g, b in corner.getdata() if r > 200 and g > 170 and b < 120)
                        self.assertEqual(yellow(self.frame(video, 0.0)), 0, "stars have not popped in yet")
                        self.assertGreater(yellow(self.frame(video, 1.5)), 100, "star popped in")

    def test_zoom_moves_background_only_so_left_aligned_text_stays_inside_the_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            storyboard = draft(media_directory=directory)
            background, layers = cards.build_card("ending", storyboard, KICKERS, work, "ending", 1280, 720,
                                                  duration=6.5, narration_seconds=6.0)
            video = cards.animated_card_video(background, layers, work / "ending.mp4", 1280, 720, 6.5)
            layer = Image.open(layers[-2]["png"]).convert("RGBA")
            left = layer.getbbox()[0]
            self.assertGreaterEqual(left, 40)
            final = self.frame(video, 6.4)
            self.assertEqual(final.size, (1280, 720))

    def test_drift_pans_the_background_in_opposite_directions(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            background = work / "ramp.png"
            ramp = Image.new("RGB", (640, 360))
            for x in range(640):
                ramp.paste((x * 255 // 639, 0, 0), (x, 0, x + 1, 360))
            ramp.save(background)
            means = {}
            for drift in (1, -1):
                video = cards.animated_card_video(background, [], work / f"d{drift}.mp4", 640, 360, 3.0, drift=drift)
                self.assertAlmostEqual(media_duration(video), 3.0, delta=0.1)
                final = self.frame(video, 2.9).convert("L")
                means[drift] = sum(final.crop((0, 0, 320, 360)).getdata()) / (320 * 360)
            # Opposite pan directions expose different parts of the ramp in the left half of the frame.
            self.assertNotAlmostEqual(means[1], means[-1], delta=3)


class WordingRuleTests(unittest.TestCase):
    def reply(self, **opening):
        response = {"opening": {"title": "Which advice deserves a second look?", "subtitle": "Examples and limits",
                                "evidence_ids": ["c0", "c1"]},
                    "ending": {"takeaway": "Check assumptions before applying advice.", "question": "Where might the advice not apply?",
                               "cta": "Test one assumption in your next example.", "seconds": 5, "evidence_ids": ["c0", "c1"]},
                    "transitions": [{"before_clip_id": "c1", "mode": "Divider", "label": "Another perspective", "evidence_ids": ["c0", "c1"]}]}
        response["opening"].update(opening)
        return {"message": {"content": json.dumps(response)}}

    def generate(self, audience, *replies):
        selection = [dict(clip, end=clip["start"] + 15) for clip in clips()[:2]]
        with patch("pipeline.storyboard.request_text", side_effect=list(replies)) as chat:
            result = generate_storyboard("model", selection, audience)
        return result, chat

    def test_throat_clearing_openers_are_matched_without_catching_real_hooks(self):
        for bad in ("In this video we explore growth", "Today we look at wages", "Today, we'll cover schools",
                    "This lecture explores education", "Welcome to the lecture", "Let's dive into growth",
                    "We're going to talk about schools", "  \"In this clip, schools"):
            self.assertTrue(_THROAT_CLEARING.match(bad), bad)
        for good in ("Can classrooms build nations?", "Why do rich nations invest in schools?", "Today's schools shape wages",
                     "Welcoming strangers: does it pay?", "In 1960 Singapore was poor", "What if school were optional?"):
            self.assertFalse(_THROAT_CLEARING.match(good), good)

    def test_each_violation_triggers_one_correction_and_the_valid_retry_is_accepted(self):
        cases = {"long hook": "Why do some nations manage to build wealth through schools so fast?",
                 "statement for general public": "Classrooms build rich nations",
                 "throat clearing": "Today we explore why schools matter?"}
        for label, title in cases.items():
            with self.subTest(label):
                result, chat = self.generate("General public", self.reply(title=title), self.reply())
                self.assertEqual(result["generation"]["attempts"], 2)
                self.assertIn("Validation failed", chat.call_args.kwargs["messages"][-1]["content"])

    def test_researcher_may_use_a_statement_and_two_violations_stop_generation(self):
        result, _ = self.generate("Researcher", self.reply(title="Retrospective evidence of schooling returns"))
        self.assertEqual(result["generation"]["attempts"], 1)
        with self.assertRaisesRegex(ValueError, "two attempts"):
            self.generate("Kids", self.reply(title="Schools build nations"), self.reply(title="Schools build nations"))

    def test_accent_words_are_kept_only_when_they_are_one_word_from_the_text(self):
        response = json.loads(self.reply(accent="advice?")["message"]["content"])
        response["ending"]["accent"] = "two words"
        response["transitions"][0]["accent"] = "Invented"
        selection = [dict(clip, end=clip["start"] + 15) for clip in clips()[:2]]
        with patch("pipeline.storyboard.request_text", return_value={"message": {"content": json.dumps(response)}}):
            result = generate_storyboard("model", selection, "General public")
        self.assertEqual(result["opening"]["accent"], "advice?")
        self.assertEqual(result["ending"]["accent"], "")
        self.assertEqual(result["transitions"][0]["accent"], "")
        result["opening"]["accent"] = "two words"
        with self.assertRaisesRegex(ValueError, "single word"):
            validate_storyboard(result, selection)

    def test_bridge_labels_are_limited_to_four_words_at_generation_only(self):
        long = self.reply()
        payload = json.loads(long["message"]["content"])
        payload["transitions"][0]["label"] = "Why some nations prioritize schooling"
        long = {"message": {"content": json.dumps(payload)}}
        result, _ = self.generate("General public", long, self.reply())
        self.assertEqual(result["generation"]["attempts"], 2)
        legacy = draft()
        legacy["transitions"][0]["label"] = "Why some nations prioritize schooling so strongly"
        self.assertGreater(len(legacy["transitions"][0]["label"].split()), BRIDGE_MAX_WORDS)
        validate_storyboard(legacy, clips())


if __name__ == "__main__":
    unittest.main()
