"""Focused regressions for three reviewed Skills; no network or user data access."""
import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


deadlines = load("deadline_tracker", "scientific-skills/Other/resubmission-deadline-tracker/scripts/main.py")
network = load("citation_export", "scientific-skills/Evidence Insight/citation-network/scripts/export_gexf_html.py")
slides = load("svg_slides", "scientific-skills/Other/slide-deck-images/scripts/generate_svg.py")


class PublicationRegressions(unittest.TestCase):
    def test_deadline_instants_and_dst(self):
        self.assertEqual(deadlines.deadline_instant("2026-07-01", "20:00", "Asia/Shanghai"),
                         deadlines.deadline_instant("2026-07-01", "08:00", "America/New_York"))
        before = deadlines.deadline_instant("2026-03-07", "12:00", "America/New_York")
        after = deadlines.deadline_instant("2026-03-08", "12:00", "America/New_York")
        self.assertEqual(after - before, timedelta(hours=23))
        for date, time, zone in [("2026-02-30", "12:00", "UTC"),
                                 ("2026-03-08", "02:30", "America/New_York"),
                                 ("2026-11-01", "01:30", "America/New_York"),
                                 ("2026-01-01", "12:00", "Invalid/Zone")]:
            with self.subTest(date=date, time=time, zone=zone), self.assertRaises(ValueError):
                deadlines.deadline_instant(date, time, zone)

    def test_saved_timezone_drives_remaining_time_without_rewriting_history(self):
        fixed = datetime(2026, 7, 1, 10, tzinfo=timezone.utc)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                self.assertIs(tz, timezone.utc)
                return fixed

        with tempfile.TemporaryDirectory() as tmp, patch.object(deadlines, "DATA_DIR", Path(tmp)), \
             patch.object(deadlines, "DEADLINES_FILE", Path(tmp) / "deadlines.json"):
            tracker = deadlines.DeadlineTracker()
            record = tracker.add_deadline("Example", "Example journal", "2026-07-01", "08:00", "America/New_York")
            before = deadlines.DEADLINES_FILE.read_bytes()
            with patch.object(deadlines, "datetime", Clock):
                self.assertEqual(tracker.calculate_remaining_time(record), timedelta(hours=2))
                self.assertEqual(tracker.calculate_remaining_time(deadlines.DeadlineTracker().deadlines[0]), timedelta(hours=2))
            self.assertEqual(deadlines.DEADLINES_FILE.read_bytes(), before)
            with self.assertRaises(ValueError):
                tracker.add_deadline("Invalid", "Example journal", "2026-03-08", "02:30", "America/New_York")
            self.assertEqual(deadlines.DEADLINES_FILE.read_bytes(), before)
            self.assertEqual(len(tracker.deadlines), 1)

    def test_cli_explicit_and_default_timezone(self):
        for supplied, expected in [(None, "Asia/Shanghai"), ("America/New_York", "America/New_York")]:
            with self.subTest(zone=supplied), tempfile.TemporaryDirectory() as tmp, \
                 patch.object(deadlines, "DATA_DIR", Path(tmp)), \
                 patch.object(deadlines, "DEADLINES_FILE", Path(tmp) / "deadlines.json"):
                argv = ["main.py", "--add", "--title", "Example", "--journal", "Example journal", "--deadline", "2030-01-01"]
                if supplied:
                    argv += ["--timezone", supplied]
                output = io.StringIO()
                with patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
                    deadlines.main()
                self.assertEqual(json.loads(deadlines.DEADLINES_FILE.read_text())[0]["timezone"], expected)
                self.assertEqual("Deadline calculated using" in output.getvalue(), supplied is None)

    def test_interactive_timezone(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(deadlines, "DATA_DIR", Path(tmp)), \
             patch.object(deadlines, "DEADLINES_FILE", Path(tmp) / "deadlines.json"), \
             patch("builtins.input", side_effect=["1", "Example", "Example journal", "2030-01-01", "12:00", "UTC", "0", "0", "", "6"]), \
             contextlib.redirect_stdout(io.StringIO()):
            deadlines.interactive_mode()
            self.assertEqual(json.loads(deadlines.DEADLINES_FILE.read_text())[0]["timezone"], "UTC")

    def test_html_payload_cannot_end_script_and_round_trips(self):
        label = '</ScRiPt><script>throw new Error("injected")</script><!-- & \u2028 中文'
        payload = {"nodes": [{"id": label, "label": label}], "edges": [{"from": label, "to": "ordinary"}]}
        html = network.render_html(payload)
        encoded = html.split("const payload = ", 1)[1].split(";\n", 1)[0]
        self.assertNotIn("<", encoded)
        self.assertEqual(json.loads(encoded), payload)
        self.assertEqual(html.lower().count("</script>"), 2)

    def test_svg_outline_and_prompt_titles_render_to_valid_xml(self):
        text = "## Slide 1 of 2\nHeadline: Review Topic\n- A & B\n## Slide 2 of 2\nHeadline: Results\n- <example>\n"
        self.assertEqual(slides.parse_outline_sections(text), [("Review Topic", ["A & B"]), ("Results", ["<example>"])])
        self.assertEqual(slides.extract_title_and_bullets("Title: A title\n- One"), ("A title", ["One"]))
        self.assertEqual(slides.slugify("Review   Topic"), "review-topic")
        with tempfile.TemporaryDirectory() as tmp, patch.object(slides, "OUTLINE_PATH", Path(tmp) / "outline.md"), \
             patch.object(slides, "PROMPTS_DIR", Path(tmp) / "prompts"), \
             patch.object(slides, "OUTPUT_DIR", Path(tmp) / "slides"):
            slides.OUTLINE_PATH.write_text(text, encoding="utf-8")
            slides.main()
            files = sorted(slides.OUTPUT_DIR.glob("*.svg"))
            self.assertEqual([p.name for p in files], ["01-slide-review-topic.svg", "02-slide-results.svg"])
            for file in files:
                output = file.read_text(encoding="utf-8")
                ET.fromstring(output)
                self.assertNotIn(r"\n", output)
            slides.PROMPTS_DIR.mkdir()
            (slides.PROMPTS_DIR / "01.md").write_text("Headline: Prompt title\n- One", encoding="utf-8")
            self.assertEqual(slides.load_slides(), [("Prompt title", ["One"])])


if __name__ == "__main__":
    unittest.main()
