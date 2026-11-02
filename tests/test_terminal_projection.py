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
        self.assertIn("Before math: x² after.", rendered)
        self.assertIn("display math: a ⁄ b", rendered)
        self.assertEqual(render_math_accessibly(r"\(\alpha \ge 0\)"), "α ≥ 0")

    def test_unicode_pretty_printing_handles_common_forms(self):
        self.assertEqual(render_math_accessibly(r"\frac{\alpha^2}{x_1}"), "α² ⁄ x₁")
        self.assertEqual(render_math_accessibly(r"\sqrt{x^2 + y^2}"), "√x² + y²")

    def test_unsafe_math_is_source_preserving(self):
        source = r"\left\langle x \middle| y \right\rangle"
        self.assertEqual(render_math_accessibly(source), source)
        self.assertEqual(render_math_accessibly(r"x^{n+1"), r"x^{n+1")


if __name__ == "__main__":
    unittest.main()
