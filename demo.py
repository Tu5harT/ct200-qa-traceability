# Runs the whole v1 -> generate -> v2 -> stale flow against a running server.
#   python -m uvicorn app.main:app      (in one terminal)
#   python demo.py                      (in another)
import os
import sys

import httpx

api = httpx.Client(base_url=os.getenv("API_URL", "http://localhost:8000"), timeout=120)


def call(method, url, **kwargs):
    r = api.request(method, url, **kwargs)
    if r.is_error:
        sys.exit(f"{method} {url} failed with {r.status_code}: {r.text}")
    return r.json()


def title(text):
    print(f"\n=== {text} ===")


def section_ids(version, sections):
    nodes = call("GET", "/search", params={"q": "", "version": version})["results"]
    return {n["section"]: n["id"] for n in nodes if n["section"] in sections}


def show_generation(gen):
    print(f"stale: {gen['stale']}  (checked against version {gen['checked_against_version']})")
    for s in gen["source_status"]:
        line = f"  source {s['section_id']}: {s['status']}"
        if s["status"] == "changed":
            line += f" ({s['kind']}) {s['diff']}"
        print(line)
    if gen["new_sections"]:
        print(f"  new sections not covered by these tests: {gen['new_sections']}")
    for case in gen["test_cases"]:
        mark = "STALE" if case["stale"] else "ok   "
        print(f"  [{mark}] {case['title']}  <- {case['source_sections']}")


if len(call("GET", "/versions")) > 1:
    sys.exit("v2 is already loaded, so there's nothing to show. Reset first (see README) and run again.")

title("1. ingest v1")
v1 = call("POST", "/ingest", json={"filename": "ct200_manual.pdf"})
print(f"version {v1['version']}, {v1['nodes']} nodes")
for w in v1["warnings"]:
    print("  parser warning:", w)

title("2. top level sections")
for s in call("GET", "/sections")["sections"]:
    print(f"  {s['section']}. {s['heading']}")

title("3. select 3.1, 3.2, 4.2 and all of section 5 (v1)")
ids = section_ids(1, {"3.1", "3.2", "4.2", "5"})
sel = call("POST", "/selections", json={"name": "demo selection", "node_ids": list(ids.values())})
print(f"selection {sel['id']} pinned to version {sel['version']}")

title("4. generate test cases (real LLM call)")
gen = call("POST", f"/selections/{sel['id']}/generate")
print(f"took {gen['attempts']} attempt(s), model {gen['model']}")
for case in gen["test_cases"]:
    print(f"  - {case['title']}  <- {case['source_sections']}")

title("5. same selection again -> saved result, no new LLM call")
again = call("POST", f"/selections/{sel['id']}/generate")
print(f"reused: {again['reused']}, same id: {again['id'] == gen['id']}")

title("6. retrieve before v2")
show_generation(call("GET", "/generations", params={"selection_id": sel["id"]})[0])

title("7. ingest v2")
v2 = call("POST", "/ingest", json={"filename": "ct200_manual_v2.pdf"})
print(f"version {v2['version']}, {v2['nodes']} nodes")
for c in v2["changes"]:
    print(f"  {c['status']}: {c['section']}")

title("8. what happened to 3.2?")
changes = call("GET", f"/nodes/{ids['3.2']}/changes")
print(f"changed: {changes['changed']}, diff: {changes['diff']}")

title("9. retrieve after v2 -> stale check")
show_generation(call("GET", "/generations", params={"selection_id": sel["id"]})[0])

title("10. look up by the v2 node of 3.2")
v2_id = section_ids(2, {"3.2"})["3.2"]
found = call("GET", "/generations", params={"node_id": v2_id})
print(f"node {v2_id} (v2) -> {len(found)} generation(s) made from older text")

title("11. the selection still has the v1 text")
pinned = call("GET", f"/selections/{sel['id']}")
body = next(n["body"] for n in pinned["nodes"] if n["section"] == "3.2")
print(f"version {pinned['version']}: ...{body[60:160]}...")
