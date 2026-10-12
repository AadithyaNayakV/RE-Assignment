"""The skill's own instruction file.

When the skill is invoked with arguments, the loader treats `$1`, `$2`, ... as argument
placeholders and substitutes them. An example amount written with a dollar sign before a
digit was therefore rewritten: "a $1,806.24 credit" reached the model as
"a test_errors,,806.24 credit". SKILL.md writes example amounts as "USD 1,806.24".
"""

from __future__ import annotations

import re
from pathlib import Path

SKILL_MD = (Path(__file__).resolve().parents[1] / ".claude" / "skills"
            / "monthly-financial-analysis" / "SKILL.md")

DOLLAR_DIGIT = re.compile(r"\$\d")


def test_skill_md_has_no_dollar_sign_before_a_digit():
    hits = [f"line {n}: {line.strip()}"
            for n, line in enumerate(SKILL_MD.read_text(encoding="utf-8").splitlines(), 1)
            if DOLLAR_DIGIT.search(line)]
    assert not hits, "argument placeholders in SKILL.md:\n" + "\n".join(hits)


def test_the_pattern_catches_the_original_example():
    assert DOLLAR_DIGIT.search('"swung from a $12,935.29 charge to a $1,806.24 credit"')
    assert not DOLLAR_DIGIT.search('"swung from a USD 12,935.29 charge to a USD 1,806.24 credit"')
