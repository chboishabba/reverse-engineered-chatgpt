"""Offline tests for source-only terminal mathematical projection."""
import unittest

from re_gpt.terminal_projection import recover_source, split_math


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


if __name__ == "__main__":
    unittest.main()
