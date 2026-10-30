"""Deterministic Unicode LaTeX fallback for terminals without graphics.

Supports common matrices, aligned environments, nested fractions, roots and
scripts. Unknown commands remain visible; this is not a full TeX engine.
"""
from __future__ import annotations
import re

SYMBOLS = {
    r"\qquad": "    ", r"\quad": "  ", r"\cdot": "·",
    r"\times": "×", r"\equiv": "≡", r"\neq": "≠",
    r"\leq": "≤", r"\geq": "≥", r"\le": "≤", r"\ge": "≥",
    r"\rightarrow": "→", r"\to": "→", r"\mapsto": "↦",
    r"\infty": "∞", r"\sum": "∑", r"\prod": "∏",
    r"\lambda": "λ", r"\alpha": "α", r"\beta": "β",
    r"\gamma": "γ", r"\pi": "π", r"\mathbb{Z}": "ℤ",
    r"\mathbb{N}": "ℕ", r"\mathbb{R}": "ℝ",
}
SUP = str.maketrans("0123456789+-=n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼ⁿ")
SUB = str.maketrans("0123456789+-=", "₀₁₂₃₄₅₆₇₈₉₊₋₌")

def _group(text, start):
    if start >= len(text) or text[start] != "{": return None
    depth = 1
    index = start + 1
    while index < len(text):
        if text[index] == "{" and (index == 0 or text[index-1] != "\\"):
            depth += 1
        elif text[index] == "}" and (index == 0 or text[index-1] != "\\"):
            depth -= 1
            if depth == 0: return text[start+1:index], index+1
        index += 1
    return None

def _fractions(text):
    start = 0
    while True:
        match = re.search(r"\\(?:dfrac|tfrac|frac)\s*", text[start:])
        if match is None: break
        pos = start + match.start()
        first = _group(text, start + match.end())
        if first is None:
            start = pos + 1
            continue
        second = _group(text, first[1])
        if second is None:
            start = first[1]
            continue
        num = unicode_math(first[0]).strip()
        den = unicode_math(second[0]).strip()
        value = "(" + num + ")/(" + den + ")"
        text = text[:pos] + value + text[second[1]:]
        start = pos + len(value)
    return text

def _matrices(text):
    rx = re.compile(r"\\begin\{(matrix|pmatrix|bmatrix|Bmatrix|vmatrix|cases)\}(.*?)\\end\{\1\}",re.S)
    def replace(match):
        kind, body = match.group(1),match.group(2)
        rows = [row.strip() for row in re.split(r"\\\\",body) if row.strip()]
        cells = [[unicode_math(cell.strip()) for cell in row.split("&")] for row in rows]
        if not cells or len(cells)>40 or len(cells[0])>20: return match.group(0)
        widths = [max(len(row[c]) if c<len(row) else 0 for row in cells)
                  for c in range(max(map(len,cells)))]
        left,right = {"matrix":(" "," "), "pmatrix":("("," )"),
                      "bmatrix":("⎡","⎤"),"Bmatrix":("⎧","⎫"),
                      "vmatrix":("│","│"),"cases":("⎧"," ")}[kind]
        rendered=[]
        for idx,row in enumerate(cells):
            l,r=left,right
            if kind=="bmatrix" and len(cells)>1:
                l="⎡" if idx==0 else "⎣" if idx==len(cells)-1 else "⎢"
                r="⎤" if idx==0 else "⎦" if idx==len(cells)-1 else "⎥"
            rendered.append(l+"  ".join((row[i] if i<len(row) else "").ljust(width)
                                   for i,width in enumerate(widths)).rstrip()+r.strip())
        return "\n".join(rendered)
    return rx.sub(replace,text)

def unicode_math(source):
    text=_matrices(source)
    text=_fractions(text)
    text=re.sub(r"\\sqrt\{([^{}]+)\}",lambda m:"√("+unicode_math(m.group(1))+")",text)
    for key in sorted(SYMBOLS,key=len,reverse=True):
        text=text.replace(key,SYMBOLS[key])
    text=re.sub(r"\^\{([0-9+\-=n]+)\}",lambda m:m.group(1).translate(SUP),text)
    text=re.sub(r"_\{([0-9+\-=]+)\}",lambda m:m.group(1).translate(SUB),text)
    text=re.sub(r"\^([0-9])",lambda m:m.group(1).translate(SUP),text)
    text=re.sub(r"_([0-9])",lambda m:m.group(1).translate(SUB),text)
    text=re.sub(r"\\(?:begin|end)\{(?:aligned|align\*?|gathered|gather)\}","",text)
    text=text.replace(r"\\","\n").replace("&","")
    text=text.replace(r"\left","").replace(r"\right","")
    return text.strip()
