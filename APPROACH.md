# Approach

## 1. Reading the PDF

**What I used: PyMuPDF on the PDF's text layer, no OCR.**

Before writing any parser code I dumped every line of both PDFs with its font, size, bold flag and x position.
Both files are born digital: every page has a real text layer and there are no images at all. OCR on these
pages would only re-guess characters we already have exactly (and lose the font/bold info I needed for headings),
so it would make the output worse, not better.

OCR is still the right tool for scanned pages, so the reader checks every page: a page with no text layer
becomes an `empty_page` item and the parser turns that into a warning ("page 5 has no text, probably scanned,
needs OCR"). It never gets silently skipped. I didn't wire in Tesseract because neither file needs it; that's
the place it would go.

Tables: PyMuPDF's `find_tables()` finds both tables (2.1 specs, 4.2 error codes) correctly, and every table becomes
one item so its rows don't end up as loose lines in the text.

## 2. Rebuilding the hierarchy

Rules, in order:

1. **A heading is a line that starts with a section number and is bold.** I don't use font size. Top level
   headings are 16.5pt and subsections 12.9pt, but `3.2` and `2.1.1.1` are printed in 11pt, the same as
   body text. A size based parser would glue 3.2 into 3.1.
2. **Level** = number of parts in the section number (`2.1.1.1` -> 4).
3. **Parent** = the closest earlier heading whose number is a prefix of this one. This handles skipped levels
   and out of order sections without special cases.
4. **Node id within a version** = section number, plus a `-2` suffix if a number ever repeats. The heading text is
   never used as an id, because "Error Codes" appears twice.
5. Wrapped lines are joined back into one paragraph. Indented numbered lines are list items and each starts a new line.
   Table rows are written as `Code | Meaning | Device Behavior`.
6. Each node gets a sha256 of its body with whitespace collapsed, so a page break or re-wrap doesn't change the hash.
7. Anything odd becomes a parser warning that's stored with the version and returned by `/ingest` and `/versions`.
   A clean-looking but wrong tree is worse than a tree with warnings.

### Irregularities found in the CT-200 manual and how each is handled

| Irregularity | Where | Handling | Test |
|---|---|---|---|
| Heading in body-size font | 3.2 (and 2.1.1.1) | headings detected by number + bold, not size | `test_small_font_heading_is_still_a_heading` |
| Skipped level, no 2.1.1 | 2.1.1.1 | parent = nearest prefix (2.1), warning raised | `test_skipped_level_goes_under_nearest_parent` |
| Sections out of order | 3.4 before 3.3 | document order kept, both under 3, warning raised | `test_out_of_order_sections_keep_correct_parent` |
| Same heading twice | "Error Codes" 4.2 and 7.1 | id is the number, not the heading, so two nodes with parents 4 and 7 | `test_duplicate_heading_gives_two_nodes_with_different_parents` |
| Numbered list that looks like headings | `1. Normal: ...` in 3.3 | not bold and indented -> list item | `test_numbered_list_is_not_turned_into_sections` |
| Section split over pages | 2 -> 2.1, 3.1 body | items read in page order and appended to the open section | `test_section_split_across_pages_stays_together` |
| Title on three lines | page 1 | bold lines before the first section are joined into the title | `test_title_is_joined` |
| Ligatures and special hyphens | `ﬁ`, `ﬀ`, U+2011 everywhere | NFKC normalise + hyphen replace | `test_ligatures_and_hyphens_are_cleaned` |
| Table cells over two lines | 4.2 error codes | text read per cell box | `test_error_code_table_is_one_item_with_clean_cells` |
| Cross references | "see 2.1, 4.3" in 3.3 | kept as plain text (not a heading since it's mid line and not bold), not linked, see decision log |

I also noticed content issues, which aren't parser problems but matter for QA: 4.1 says overpressure at
"exceeds 299 mmHg" and also "exceeds 300 mmHg for longer than 3 seconds", and 3.2's 40 mmHg steps from 180 go
220, 260, 300, which never lands on the 299 max. The LLM picked up on the second one by itself in one run.

### What went wrong along the way and how I found it

- **Table commas in the wrong place.** `table.extract()` returned cells like `"Aborts measurement displays E1\n,"`.
  Found by printing the extracted tables next to the line dump. Fixed by reading the text inside each cell's
  bounding box with `get_text(clip=...)`. Covered by a test.
- **Ligatures.** The raw dump showed `Cuﬀ Speciﬁcations`, so a search for "cuff" would miss most sections.
  Found in the first dump, fixed with NFKC.
- **Size based heading detection.** I spotted this in the font dump before writing the parser, so I never shipped it,
  but it's the obvious first approach and it fails on 3.2.
- **LLM model removed.** First real call returned 502 because `llama-3.3-70b-versatile` no longer exists on Groq.
  The failure path worked as designed (clean 502, failure stored). I listed the models for the key and switched the
  default to `openai/gpt-oss-120b`, configurable with `GROQ_MODEL`.
- **First real LLM reply was invalid.** The first successful run reported `attempts: 2`, so the retry path is not
  theoretical.
- **Generations attached to the wrong selection.** Running the demo against a fresh SQLite returned test cases from
  an older manual test. Mongo stored the SQLite selection id, and after deleting `ct200.db` the ids start at 1 again,
  so the new selection 1 picked up the old selection 1's tests with no error. Fixed by giving every selection a random
  `uid` that Mongo links to, and by ignoring generations whose selection isn't in the current database.
  `test_leftover_generation_from_old_db_is_ignored` reproduces it.

How I checked the tree overall: the line dump (`python -m app.pdf_reader`), the tree printout
(`python -m app.parser`) compared by eye with the PDF, a v1 vs v2 hash comparison that had to match exactly the
differences I found by diffing the two dumps by hand, and then the unit tests to keep it that way.

## 3. Data model

SQLite:

- `documents` - one row for the manual.
- `versions` - one per ingested PDF: number, filename, file hash, title, parser warnings.
- `nodes` - one row per section **per version**: section id, heading, level, parent, body, hash, page, order, plus
  `logical_id` (same for the same section across versions), `status` (new / unchanged / changed / added) and
  `prev_node_id` (the matching row in the previous version). Old rows are never updated.
- `selections` + `selection_nodes` - a name, a random `uid`, and the node rows it points to.

Because a node row is "this section in this version" and rows are never edited, a selection is version pinned
for free: it points at v1 rows and keeps returning the v1 text after v2 is ingested.

MongoDB `generations`: selection uid/id/name, version, `sources` (node id, logical id, section id and hash of every
section sent to the LLM), the test cases, model, prompt version, attempts, status (`ok` / `failed`), and for failures
the error and the raw reply. Generated output is schemaless and append-only, which fits a document store, and keeping
it out of SQLite means a re-ingest can't touch it.

## 4. Version matching

When a new version is ingested, every node is matched against the previous version:

1. Same section id and same heading (case and whitespace ignored) -> same logical node.
2. Leftovers: same heading only, if that heading is unique on both sides -> renumbered section.
3. Still no match -> `added`, new logical id.

Matched nodes are `unchanged` if the hash is equal, else `changed`. For v1 -> v2 this gives exactly
2.1.1.1, 3.2, 4.2, 4.3 changed and 5.3 added. 3.1 moved over a page break and is correctly `unchanged`.

Why this: section numbers are the identity a regulated document actually uses ("see 4.2"), so they're the
right primary key. Hash based matching alone can't work because a changed section by definition has a new hash.
Fuzzy text matching is harder to explain and to trust in an audit.

Where it breaks:

- Renamed **and** renumbered in the same release -> looks like one section removed and a new one added, history lost.
- Two sections with the same heading both renumbered -> not matched on purpose (pass 2 refuses to guess), shows as added.
- A section moved to another parent but keeping its number and heading -> matched, but the move isn't reported.
- A section deleted in v2 and a different section with the same heading added elsewhere -> pass 2 links them.
- Only the previous version is compared, so a section missing in v2 and back in v3 gets a new logical id.
- Removed sections aren't stored as rows. `/nodes/{id}/changes` reports `removed` when there's no node with that
  logical id in the latest version.

## 5. LLM generation

**Provider:** Groq free tier, `openai/gpt-oss-120b`, JSON mode, temperature 0.2.

**Input:** the selected nodes plus their whole subtree (selecting "5" with an empty body would otherwise send nothing),
in document order, each prefixed with its id: `[3.2] Cuff Inflation Sequence`.

**Prompt:** QA engineer for medical device firmware; 3 to 5 tests; only from the given text; concrete enough for
someone else to run; exact numbers from the text; prefer safety, limits and error handling; every test lists the
`source_sections` it came from; reply with JSON in a fixed shape.

**Validation** (the important part):

- JSON parse + Pydantic model: 3-5 test cases, each with title, at least one step, expected result, at least one
  source section.
- Every `source_sections` id must be one we actually sent. A test citing a section that wasn't in the input breaks
  traceability, so the whole reply is rejected.

**When the reply is bad:** the error is sent back to the model in the same conversation and it gets one more try.
If that's still bad, or the API call itself fails (bad key, rate limit, network), the endpoint returns **502** and
the attempt is stored in Mongo as `status: failed` with the raw reply. A failed generation is never returned as test
cases. One retry because a second correction rarely helps and it doubles cost and latency; partial results (say 2
valid tests out of 3) are rejected rather than trimmed, because a half-checked medical test plan looks complete when it isn't.

**Same selection twice:** returns the saved generation with `reused: true`. The selection is pinned to fixed text,
so another call is just a random re-roll of identical input, costs tokens, and gives two competing test plans for the
same requirement. `?force=true` creates a new generation and keeps the old one, for when someone wants another go.
If the old one is stale that doesn't matter here: a stale generation means the *document* moved on, and the fix is a
new selection on the new version, not regenerating the old one.

## 6. Staleness

Each generation stores the hash of every section it was built from. Staleness is computed **when you retrieve**,
against whatever is the latest version at that moment, so nothing has to be updated on re-ingest.

For each source section, find the node with the same logical id in the latest version:

- same hash -> `unchanged`
- different -> `changed`, with a word diff and a `kind`: `numbers changed` if the multiset of numbers in the text
  differs (40 -> 30 mmHg, 2 -> 1.5 s, E5 -> E6), otherwise `wording only`
- missing -> `removed`
- the old text isn't in SQLite anymore -> `unknown`

A test case is stale if any section it cites is not `unchanged`, so after v2 the 3.2 tests are stale and a 3.1 test
isn't. New children under a section we generated from (5.3 under 5) are listed as `new_sections` because no test
covers them. All of this is in the `GET /generations` response.

**Does a one word change count the same as a changed pressure threshold?** For "is it stale": yes, on purpose.
Any change to the source text marks the tests stale, because on a medical device a missed real change is far worse
than a reviewer spending a minute on a typo. The `kind` field is there to sort the review queue, not to decide
whether review is needed. And it can't be trusted for that: "falls below 15%" -> "rises above 15%" has the same
digits and comes out as `wording only` (there's a test that pins this gap), and "two" -> "three" in words is missed too.

Granularity is per section. In the demo, "Cuff not connected displays E1" is marked stale because the 4.2 table
changed, even though the E1 row didn't. Row level hashing for tables would fix that.

## 7. Stack

As requested: FastAPI, Pydantic, SQLAlchemy + SQLite, MongoDB, Git. Groq for the LLM. PyMuPDF for the PDF.

## 8. With more time

- Hash tables per row (and maybe per sentence) so staleness points at the exact requirement.
- Follow cross references: store "see 2.1, 4.3" as links, include them in the prompt and in staleness.
- Report every pass 2 (heading only) match at ingest so a person confirms it.
- A better change classifier: compare comparison words (below/above, must/may) and units, not just digits.
- Lock or unique index on `selection_uid` for generate, so two parallel requests can't both call the LLM.
- Move SQLite + Mongo consistency to one place (or put generations in SQLite with a JSON column) so the two
  stores can't drift apart again.
- OCR fallback with Tesseract for pages without a text layer, run only on those pages.

## 9. Decision log

**What's most likely to silently give wrong results without erroring? How would I catch it?**

The link between a generation and the text it came from. It already happened once: after resetting SQLite, Mongo
still had generations keyed by selection id 1, and a brand new selection 1 got someone else's test cases, with a
200 response and no warning. I caught it only because the demo selected 4 sections and the result listed 2.
Fixed with a random selection uid. The next most likely one is the version matcher: if pass 2 links the wrong
sections by heading, every status and staleness answer downstream is wrong and looks fine. I'd catch that by
listing heading-only matches in the `/ingest` response for a human to confirm, and by adding a renumbered v3
fixture to the tests.

**Where did I choose simplicity over correctness, and what breaks first in production?**

Staleness is per section, and the change kind is a digit comparison. That's simple and easy to explain, but it
over-flags (the E1 test after only E3 changed) and it mislabels meaning changes without digits as wording only.
What breaks first in production though is concurrency: generation is a synchronous LLM call inside the request,
the "already generated?" check and the insert aren't atomic, and SQLite allows one writer. Two people clicking
generate on the same selection at the same time get two LLM calls and two generations.

**One input I don't handle, and what the system does with it.**

Cross references. 3.3 says "see 2.1, 4.3 for related specifications and alarm thresholds". The parser keeps that
as plain text, so selecting 3.3 doesn't send 2.1 or 4.3 to the LLM, and if 4.3's thresholds change in a later
version, tests made from 3.3 are still reported as fresh. The system doesn't error or warn, it just treats the
reference as words.
