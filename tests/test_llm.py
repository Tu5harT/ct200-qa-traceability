import json

import pytest

from app.llm import GenerationFailed, generate


def case(title="Overpressure triggers E3", sections=("3.2",)):
    return {
        "title": title,
        "steps": ["Start a measurement", "Force cuff pressure above 299 mmHg"],
        "expected_result": "Cuff vents within 2 seconds and E3 is shown",
        "source_sections": list(sections),
    }


GOOD = json.dumps({"test_cases": [case(), case(), case()]})


def fake_llm(*replies):
    # returns the replies one by one and remembers what it was sent
    calls = []

    def call(messages):
        calls.append(list(messages))
        return replies[len(calls) - 1]

    return call, calls


def test_good_reply_passes_first_time():
    call, calls = fake_llm(GOOD)
    cases, attempts = generate("text", {"3.2"}, call)
    assert len(cases) == 3 and attempts == 1


def test_broken_json_gets_one_retry_with_the_error():
    call, calls = fake_llm("Sure! Here are your tests: {oops", GOOD)
    cases, attempts = generate("text", {"3.2"}, call)
    assert attempts == 2
    assert "invalid" in calls[1][-1]["content"]  # the error was sent back to the model


def test_gives_up_after_two_bad_replies():
    too_few = json.dumps({"test_cases": [case()]})
    call, _ = fake_llm(too_few, too_few)
    with pytest.raises(GenerationFailed) as e:
        generate("text", {"3.2"}, call)
    assert e.value.raw == too_few


def test_made_up_section_is_rejected():
    bad = json.dumps({"test_cases": [case(), case(), case(sections=["9.9"])]})
    call, _ = fake_llm(bad, bad)
    with pytest.raises(GenerationFailed, match="9.9"):
        generate("text", {"3.2"}, call)


def test_wrong_shape_is_rejected():
    call, _ = fake_llm(json.dumps({"tests": ["do stuff"]}), json.dumps([1, 2, 3]))
    with pytest.raises(GenerationFailed):
        generate("text", {"3.2"}, call)
