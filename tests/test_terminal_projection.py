"""Offline tests for source-only terminal mathematical projection."""
import unittest

from re_gpt.terminal_projection import recover_source, render_math_accessibly, render_terminal_accessibly, split_math


class TerminalProjectionTests(unittest.TestCase):
    def test_roundtrip_and_math(self):
        source = "Alpha α: $$\\frac{a}{b}$$ and \\(x+y\\) and \\[z\\]"
        parts = split_math(source)
        self.assertEqual(recover_source(parts), source)
        self.assertEqual([part.source for part in parts if part.kind == "math"],
                         ["$$\\frac{a}{b}$$", "\\(x+y\\)", "\\[z\\]"])
        for part in parts:
            self.assertEqual(source[part.start:part.end], part.source)

    def test_code_opaque(self):
        source = "```agda\n$x$\n```\nInline `$$z$$`"
        parts = split_math(source)
        self.assertEqual(recover_source(parts), source)
        self.assertFalse(any(part.kind == "math" for part in parts))

    def test_incomplete_stream_preserved(self):
        source = "While reasoning: $$\\frac{x}"
        parts = split_math(source)
        self.assertEqual(recover_source(parts), source)
        self.assertFalse(any(part.kind == "math" for part in parts))

    def test_dollar_escape(self):
        source = r"Price \\$5, equation $x$, unchanged"
        parts = split_math(source)
        self.assertEqual(recover_source(parts), source)

    def test_accessible_render_preserves_non_math_and_labels_display(self):
        source = "Before $x^2$ after.\n\n$$\\frac{a}{b}$$"
        rendered = render_terminal_accessibly(source)
        self.assertIn("Before math: x to the power of 2 after.", rendered)
        self.assertIn("display math: fraction of a of b", rendered)
        self.assertEqual(render_math_accessibly(r"\(\alpha \ge 0\)"), "alpha greater than or equal to 0")


if __name__ == "__main__":
    unittest.main()
