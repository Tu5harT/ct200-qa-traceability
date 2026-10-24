import json
import os

from groq import Groq
from pydantic import BaseModel, Field, ValidationError

MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
PROMPT_VERSION = 1
MAX_ATTEMPTS = 2  # first try + one retry with the error sent back

SYSTEM_PROMPT = """You are a QA engineer writing test cases for medical device firmware.
You get sections of a device manual. Each section starts with its id in square brackets, like [3.2].

Write 3 to 5 test case ideas based ONLY on the given text.
- Each test must be concrete enough for another tester to run it.
- Use the exact numbers from the text (pressures, times, limits). Don't invent values.
- Prefer safety behaviour, limits and error handling over cosmetic things.
- source_sections lists the ids of the sections the test is based on, only ids from the input.

Reply with JSON only, in exactly this shape:
{"test_cases": [{"title": "...", "steps": ["...", "..."], "expected_result": "...", "source_sections": ["3.2"]}]}"""


class QACase(BaseModel):
    title: str = Field(min_length=5)
    steps: list[str] = Field(min_length=1)
    expected_result: str = Field(min_length=5)
    source_sections: list[str] = Field(min_length=1)


class QACaseList(BaseModel):
    test_cases: list[QACase] = Field(min_length=3, max_length=5)


class GenerationFailed(Exception):
    def __init__(self, error, raw):
        super().__init__(error)
        self.raw = raw  # what the model actually sent, kept for debugging


def call_groq(messages):
    client = Groq()  # picks up GROQ_API_KEY from the env
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content


def collect_nodes(selected):
    # a selected parent brings its whole subtree, otherwise picking "4" would send an empty text
    result = []

    def walk(node):
        if node not in result:
            result.append(node)
            for child in node.children:
                walk(child)

    for node in selected:
        walk(node)
    return sorted(result, key=lambda n: n.order)


def sections_text(nodes):
    return "\n\n".join(f"[{n.section_id}] {n.heading}\n{n.body}" for n in nodes)


def check(raw, allowed_sections):
    """Raise ValueError if the reply isn't what we asked for."""
    try:
        result = QACaseList.model_validate(json.loads(raw))
    except (TypeError, json.JSONDecodeError, ValidationError) as e:  # TypeError = empty reply
        raise ValueError(str(e))

    for case in result.test_cases:
        unknown = set(case.source_sections) - allowed_sections
        if unknown:
            # a test pointing at a section we never sent breaks traceability
            raise ValueError(f"test '{case.title}' cites sections not in the input: {sorted(unknown)}")
    return result.test_cases


def generate(text, allowed_sections, call=None):
    call = call or call_groq  # tests pass a fake here
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    raw, error = None, None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = call(messages)
            return check(raw, allowed_sections), attempt
        except ValueError as e:
            error = str(e)
        except Exception as e:  # network, rate limit, bad key...
            error = f"LLM call failed: {e}"
            continue

        # tell the model what was wrong and let it fix it once
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content": f"Your reply was invalid: {error}\nSend the corrected JSON only."})

    raise GenerationFailed(error, raw)
