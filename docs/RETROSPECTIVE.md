# photo-sorter: retrospective and roadmap

*As of the evening of October 9, 2026. Covers October 3–9, 2026. Times are US Eastern (EDT), converted from the transcripts' UTC timestamps.*

A visual version of this document is in [retrospective.html](retrospective.html).

---

## 1. Evidence inventory

**What I could read**

| Source | What it gives | Limits |
|---|---|---|
| This project's Claude Code transcript (`~/.claude/projects/…image-clasification/46ad0f9b….jsonl`, 18 MB, Oct 4 to Oct 9) | Every user message and every assistant reply with timestamps. Tool-call counts and test results per turn. | I read all user and assistant messages and computed statistics with scripts. I did not replay every tool output or diff. |
| Subagent logs for that session (3) | A codebase survey, the implementation plan and the code review run | The full code review report went straight to the user. I only have its 15 finding titles, from the session summary. |
| Plan files `~/.claude/plans/lets-plan-about-this-golden-donut.md` (Oct 3) and `plan-that-last-one-serialized-unicorn.md` (Oct 9) | Documented reasoning for the two big design decisions | Neither plan was updated after approval. |
| Sibling project `~/Desktop/Projects/reef_image_test` (README, folder layout, user messages from its transcript) | The pre-history: a Claude Skill version | I skimmed it. I did not review its code or its iterations. |
| Current code, tests, README, `deploy/`, `CATEGORIES.md`, `CATEGORIES.legacy.md` | The current state | None |
| File modification times | Rough order in which files last changed | They show only the last change, not history. |
| Claude memory files (`uv-pth-hidden-flag.md`, `photo-sorter-v2-decisions.md`) | Decisions and the environment fix, as recorded at the time | None |

**Important gaps**

- **No git repository.** There are no commits, diffs, issues or PRs. Everything below comes from the transcripts, the files on disk and their timestamps.
- **No researcher feedback.** Every test was done by the project owner or by the assistant.
- **Accuracy is measured against "silver" labels.** These are album names that Claude assigned in the sibling project, not human-checked truth. Only 9 images have human labels.
- **The owner's own effort is not measured.** The time spent testing between messages, and the time spent before October 3, are unknown.
- **The Google Drive, Vertex AI, Cloud Run and Windows paths have never run against the real services.**

---

## 2. Retrospective in brief

**What we built.** A local-first photo sorter for the I.CARE coral program. Most of it was built on October 9, after two earlier prototype days. Photos land in an inbox folder, on disk or in Google Drive. A free local image model (SigLIP 2) suggests a category from a plain `CATEGORIES.md` list and learns from example photos. A pixel-art web UI then lets a person file photos one by one or in piles: renamed, moved and undoable. It reports what each run cost, and it can be taught with uploaded or web-searched examples.

**How we built it.** In short request-and-response cycles, about 30 user messages, with the assistant writing nearly all the code. The core design came from one planned step. The user interface was found by trial: the main screen went through about six versions in two and a half hours, mostly after the owner tried it and pushed back.

**Where it stands.** The local-folder workflow is tested and works on this Mac with the real model; the tests run against a fake Drive. A code review found 15 bugs plus 7 more and 3 efficiency problems. The efficiency problems and 1 bug are fixed. Hosting on Google Cloud is written but blocked by a sign-in bug. A researcher can't install it yet without hand-editing a JSON file.

---

## 3. How the project evolved

Each stage lists the goal, what changed, the reasoning, what worked or failed, and the evidence. "**Documented**" marks reasoning written down at the time. "**My read**" marks my interpretation.

### Stage 0: a Claude Skill for a free account (Oct 3–4, sibling project)

- **Goal.** File about 2,000 I.CARE photos into the team's existing iCloud Photos albums, with keywords and captions. The tool had to work for a researcher on a free Claude account.
- **Built.** A Claude Skill (ZIP), Mac scripts that export from and apply to Photos, a share page, an evaluation workspace with four iterations and answer keys, and a classification of the real 84-photo shared album.
- **Documented reasoning.** Claude's own vision inside a Skill was the only option that fit a free account. Roboflow, BioCLIP and DINOv2 were ruled out because the free sandbox can't reach them. iCloud had to be handled on the Mac.
- **Outcome.** It worked as a proof of concept. Its outputs were reused later: the taxonomy (`CATEGORIES.md`), the silver-labelled album and the 9 gold images.
- **Evidence.** `lets-plan-about-this-golden-donut.md`, `reef_image_test/README.md`, and that project's transcript (11 user messages, Oct 3 13:45 to Oct 4 12:11).

### Stage 1: a config-driven command-line tool with a vision LLM (Oct 4, 12:07–13:54)

- **Goal.** The owner pasted a script that sorted Apple Photos with Gemini. The ask was to rebuild it so categories come from an md or json file, the source is an iCloud shared-album link, and output goes to a local folder or iCloud.
- **Built.** A client for the shared-album link, which the assistant reverse-engineered from the CloudKit web API and tested live on 84 photos. A loader for the `CATEGORIES.md` tree (55 photo and 5 video categories), Claude and Gemini classifiers, and three destinations: a local folder, iCloud Drive and Photos. The workflow was `fetch` → `classify` to a CSV → hand review → `apply`.
- **Documented reasoning.** Claude Opus was the default because species codes and Outplants versus Natural are hard from a thumbnail. Estimated cost was about $0.50 per album.
- **Failed or deferred.** No real model call was made, because there was no API key. The Photos destination was only dry-run. The laptop slept in the middle of a run. The first sign of the iCloud "hidden file" problem appeared and was patched with a workaround, `chflags`.
- **Evidence.** Transcript, Oct 4. [icloud.py](../photo_sorter/icloud.py), [legacy_icloud.py](../photo_sorter/legacy_icloud.py) and [outputs.py](../photo_sorter/outputs.py) still exist.

### Stage 2: choosing the model again (Oct 4 22:48 to Oct 9 09:48)

- **Question.** "Using Roboflow or SigLIP2 would be better and easier?" It went unanswered for five days because of an interruption.
- **First answer (09:32).** "Not for this taxonomy, not yet." SigLIP can't do species codes or people's identities. Roboflow needs thousands of labels.
- **New conditions (09:47).** Photos now arrive in Google Drive. Categories are general (coral, sponge, diver, people). There would be `_training_data` folders, improvement over time, a web UI and easy installation.
- **Second answer (09:48).** SigLIP 2 plus nearest-neighbour matching against example photos. **Documented:** "The requirements changed the problem."
- **Revised afterwards.**

  | Initial recommendation | Changed to |
  |---|---|
  | Drive for Desktop sync | Drive API with the researcher's Google sign-in |
  | Gradio or Streamlit | FastAPI + HTMX |
  | Copy, don't move | Move and rename |
  | Filed photos teach "off by default" | On by default |
  | Model "about 400 MB" | Verified at 1.5 GB in the plan |

- **My read.** The flip was legitimate, because the requirements changed. The 400 MB figure was a factual error, caught by the plan's verification step before any code was written.

### Stage 3: plan and build of v0.2 (Oct 9, 09:53–10:37)

- **Goal.** Turn the recommendation into a plan, then build it. During planning the owner added two requirements: the server option must use only Google Drive, and the project should use Google Cloud as much as possible, to learn it.
- **Process.** Plan mode used a codebase-survey subagent, a planning subagent, 7 web searches and 2 page fetches. It asked the owner two multiple-choice questions: Drive sign-in method, and which embedding model runs where. The plan was approved at 10:13.
- **Built in about 24 minutes after approval.**
  - A storage layer for a local folder and for Google Drive.
  - The SigLIP 2 and Vertex AI embedders and the zero-shot plus examples classifier.
  - A move history ("manifest") with undo.
  - A command-line tool, a FastAPI web UI, Cloud Run and VPS deploy scripts, and Mac and Windows installers.
  - The README and 24 tests.
- **Measured on 69 album photos with the full 55-category list:**

  | Setup | Exact category | Top level (coral / sponge) |
  |---|---|---|
  | Zero-shot, no examples | ~0% | 78% |
  | With examples (leave-one-out) | 51% | 95.7% |

  At confidence 0.2, 94% of filed photos were right and 75% went to Review.
- **Not verified.** Drive, Vertex and Cloud Run; this Mac has no `gcloud` and no project. The summary said Google sign-in was "kept in an encrypted cookie". The later code review found the cookie is signed, not encrypted.
- **Evidence.** [plan-that-last-one-serialized-unicorn.md](../../../../.claude/plans/plan-that-last-one-serialized-unicorn.md), the transcript (10:13 → 10:37), and [photo-sorter-v2-decisions.md](../../../../.claude/projects/-Users-alejandrogalguera-Desktop-Projects-image-clasification/memory/photo-sorter-v2-decisions.md).

### Stage 4: first hands-on testing and UI churn (Oct 9, 10:50–13:10)

- **Goal.** The owner tested locally on `~/sorter-test`. Each round of testing produced a new request.

| Time | Owner's report | Response |
|---|---|---|
| 11:13 | Categories file too complex; thousands of photos will be a mess | Simple nested-list format plus a categories editor ([taxonomy.py](../photo_sorter/taxonomy.py)); pagination and bulk filing |
| 11:24 | "Why is it not working?" | All test photos were `.avif`, which the scanner skipped. Support added, and skipped files are now reported. |
| 11:27 | "Taking a long time … no idea what's going on" | The run had finished but the page never refreshed. It now refreshes itself. |
| 11:31 | "Terrible UI"; a turtle 97% "shark" | List layout. An **absolute fit check** (`min_fit`): a photo that fits no category now goes to Review. |
| 11:56 | Pixel-art reef theme, show what's happening | Theme plus a live status panel |
| 12:11 | First screen = unsorted images; nothing runs until a button | New first screen. The tests also caught that undo could reverse every move made in the same second; records got unique ids. |
| 12:23 | "Still launching the classification on start" | The model loaded at login, and two requests raced to load it twice. It now loads only on Sort, behind a lock. |
| 12:27–12:29 | `uv run` errors (3 messages) | Root cause found: the iCloud-synced Desktop hides files in the virtual environment. The environment was moved to `~/.venvs`. |
| 12:43 | "UI should be simpler": piles, hints, progress only when running; categories as a simple outliner | Board of piles, empty-inbox guide, tree editor. **First check in a real browser (Playwright + Brave).** |
| 13:01 | A new category isn't recognised without a restart | The category file is compared on each page load |
| 13:04 | Better logo; animation principles from an article | Pixel sprite logo; animations mapped to the article's principles |

- **Worked.** Every request was turned around in 1–13 minutes of assistant time. Tests grew from 24 to 38.
- **Failed or revised.** The UI was redesigned about six times in two and a half hours. Three of those redesigns followed explicit pushback. The environment problem first seen on Oct 4 cost four more owner messages before it was fixed at the root.
- **My read.** The owner's goal was simplicity from the start ("keep it very simple" at 11:13). The early versions added features instead: pagination, filters, tabs, cards. The assistant didn't look at the UI in a browser until the sixth version.

### Stage 5: transparency and teaching (Oct 9, 13:16–16:38)

- **Goals.** Show what each run uses and costs. Let the owner teach the model by example. Make fixing mistakes and reviewing photos one at a time fast.
- **Built.**
  - **Run receipts** ([metering.py](../photo_sorter/metering.py)): exact model and revision, where it ran, tokens, estimated FLOPs, CPU time, memory and money. Spending history is kept in `.sorter/runs.jsonl`.
  - **Examples** ([examples.py](../photo_sorter/examples.py)): upload, an openly-licensed web image search (Openverse), and "use as example". Examples are re-read on every Sort.
  - **One picture teaches one category**, matched by content. Adds a **Move to…** action and a **Check this category** action.
  - **One-by-one viewer**: number keys file a photo, typing filters categories, Ctrl/⌘+Z undoes.
- **Found while testing and fixed.**
  - With examples only in "Turtle", 5 photos went to Turtle and only 2 were turtles. The fix calibrates a separate cutoff for each category.
  - A bare category name scored near zero; names are now lowercased.
  - A scoring formula bug could give leftover score to a category whose description didn't fit. Filed accuracy went from 93.8% to 94.1% after the fix.
  - Pages failed silently after a server restart; every page now handles a missing session.
- **Documented gap, flagged by the assistant at 15:32.** Auto-filed photos also act as examples, "so a wrong automatic filing teaches the wrong thing". The proposed fix was "count only your own decisions". It was not built. The code now treats every filing as a user decision.
- **Evidence.** Transcript 13:16–16:38. Tests grew from 43 to 57.

### Stage 6: real taxonomy, review and hardening (Oct 9, 17:14–22:04)

- **Real categories.** The repo's `CATEGORIES.md` was converted to the simple format. It keeps all 60 categories at the same paths, adds a short visual description to each and keeps the notes. The original is saved as `CATEGORIES.legacy.md`. A preview on 10 test photos filed 6 with confidence and sent 4 to Review. A diver photo was weakly guessed as "Sponge Monitoring".
- **Code review** (owner-run `/code-review`, 17:26) found:
  - 15 reported bugs, including: web Google sign-in fails, two sessions overwrite each other's move history, Drive could create duplicate folders, a same-named category deletes its parent, and crashes when other pages are used during a Sort.
  - 7 more bugs that were left out of the report.
  - 3 efficiency problems.
- **Readiness check (21:48).** A fresh install stops with `config not found: categories.json`, so a researcher must hand-write JSON. Drive, Cloud Run and Windows have never run.
- **Efficiency fixes (21:57–22:04).** The move history now only appends, and checking it takes a single pass. Sorting checks the cache before reading an image. Drive lists each folder's subfolders once, which also fixes the duplicate-folder bug.
  - Benchmark on a made-up history of 30,000 moves: History page 43 s → 20 ms; data written per filing 16 MB → about 0.5 KB.
  - Tests: 60.

---

## 4. Current state

**Size.** 4,804 lines of Python in 28 files. The web app alone, [web/app.py](../photo_sorter/web/app.py), is 1,103 lines. There are 1,239 lines of templates, 60 tests (1,319 lines), a 245-line [README](../README.md) and 6 deploy files.

| Area | Status | Notes |
|---|---|---|
| Local folder: sort, file, rename, undo (CLI + web) | ✅ verified | Real model, on this Mac |
| SigLIP 2 classifier + examples + fit check | ✅ verified | 69 silver-labelled photos and about 10 test photos |
| Web UI: piles, viewer, categories editor, examples, history, receipts | ✅ verified locally | Browser-driven checks; one user |
| iCloud shared-album reader | ✅ verified (Oct 4) | Kept as a legacy path |
| Google Drive storage | 🧪 tested with fakes only | Never run against a real Drive |
| Claude / Gemini classifiers, LLM second opinion | 🧪 unit tests only | No real API calls made |
| Vertex AI embeddings, Cloud Run, docker-compose, installers | ⚠️ written, never run | No `gcloud`, Docker or Windows here |
| Web Google sign-in | ❌ known broken | Review finding: a sign-in security value is lost between redirects |
| Session cookie | ❌ known weak | Signed but readable (not encrypted) |
| First-run setup | ❌ missing | Needs a hand-written `categories.json` |
| Code review findings | 🟡 1 of 15 fixed; 14 + 7 open | Efficiency issues fixed |

---

## 5. Feature backlog

The **Origin** column separates three kinds of item: **Done**, **Discussed** (proposed earlier in the sessions or in the plan) and **New** (my recommendation in this document).

### Past work (done)

| Feature | Purpose | Status |
|---|---|---|
| iCloud shared-album client + LLM classifiers + CSV workflow | First config-driven version | Done; legacy |
| Storage layer (local, Drive) | One code path for disk and Drive | Local verified; Drive fakes only |
| SigLIP 2 / Vertex embedders, examples, calibrated cutoffs, fit check | Free, local, improves with examples | SigLIP verified; Vertex unrun |
| Move history with undo and re-filing chains | Every move reversible | Done; append-only since Oct 9 (evening) |
| Categories editor (outliner) and simple `CATEGORIES.md` | Non-technical upkeep | Done |
| Web UI: piles, one-by-one viewer, receipts, examples, history | Daily use | Done (one user) |
| Run metering and spending history | Know what each run costs | Done |
| Deploy scripts (Cloud Run, VPS, installers) | Hosting, easy install | Written, unrun |
| Efficiency fixes | Scales to tens of thousands of moves | Done, benchmarked |

### Open items

| # | Item | Purpose | Status | Depends on | Next step | Origin |
|---|---|---|---|---|---|---|
| 1 | Put the project in git, private GitHub repo | History, diffs, install from URL, reviewable changes | Not started | None | `git init`, first commit, push | Discussed |
| 2 | Fix review blockers for the local/Drive path | Correctness before anyone else uses it | 1 of 15 fixed | 1 | Fix taxonomy find, category name matching, thread safety, undo clash, one-picture rule in the core, re-file metadata, dry-run side effects, Drive token refresh errors | Discussed |
| 3 | First-run setup in the browser | Remove the JSON step for researchers | Not started | 2 | "Which folder are your photos in?" page that writes the config | Discussed |
| 4 | Real Google Drive test | Biggest unknown in the researcher path | Not started | 1 (Google Cloud project, OAuth client) | Scratch Drive folder, 10 photos, dry run → file → undo | Discussed |
| 5 | Researcher quick start; admin guide separate | Researchers read one page | Not started | 3 | One page with screenshots; move Google Cloud and hosting material out | Discussed |
| 6 | Watched first run with a researcher | The only real usability evidence | Not started | 3, 5 | One hour, silent observation, log every question | Discussed |
| 7 | Gold-labelled test set (100–200 human-checked I.CARE photos) | Replace silver labels; track accuracy per change | Not started | Team time | Researcher labels a sample; add it to `eval` | **New** |
| 8 | CI install smoke test (macOS, Windows, Linux) | Catch install breakage | Not started | 1 | GitHub Actions: install + tiny classification | Discussed |
| 9 | Drive cost per filing (full history re-upload; example-copy scan) | Fast one-by-one filing on Drive | Open, measured | 4 | Measure on real Drive first, then batch or index | Discussed (Oct 9, 22:04) |
| 10 | Should auto-filed photos teach? | Avoid self-reinforcing mistakes | Open question | 7 | Compare accuracy with and without on the gold set | Discussed (15:32) |
| 11 | Web sign-in fix + encrypted cookie, then Cloud Run with Vertex | Hosted version; Google Cloud learning goal | Blocked | 1, 2, 4 | Fix the sign-in bug, run `gcloud-setup.sh` and `deploy-cloud-run.sh` | Discussed |
| 12 | Several people on one Drive folder | Shared-folder safety | Partly (local appends safe) | 4 | Decide whether it's needed; if so, lock or per-user logs | Discussed |
| 13 | Split `web/app.py` (1,103 lines) | Easier to change and review | Not started | 1, 2 | Split by area (sorting, examples, categories, auth) behind tests | **New** |
| 14 | Number keys follow top suggestions when there are more than 9 categories | Faster one-by-one review with the real 60-category list | Offered | None | Small change in the viewer | Discussed (17:16) |
| 15 | Nightly sorting (Cloud Scheduler + Cloud Run Job), `categories --from-folders`, Gemini Embedding 2, iCloud read adapter | Later automation and options | Not started | 11 | Revisit after hosting works | Discussed (plan, phase 7) |
| 16 | Confirm names and dates in `CATEGORIES.md` notes | Correct folders for I.CARE | Open with the team | Team | Export the album list on the I.CARE Mac | Discussed |

---

## 6. Roadmap

Ratings: **V** = value, **E** = effort, **U** = uncertainty (H = high, M = medium, L = low). The order favours the researcher's path (a local folder or a shared Drive) before hosting. That is my recommendation, not a decision already taken.

### Now: make it safe and installable (about 1–2 weeks)

| Item | V | E | U | Why now |
|---|---|---|---|---|
| 1. Git + private repo | H | L | L | Every later step needs diffs and a URL to install from |
| 2. Fix review blockers (local + Drive path) | H | M | L | They are known bugs in the path a researcher will use |
| 4. Real Drive smoke test | H | L | **H** | Cheap, and it removes the biggest unknown; do it early |
| 3. First-run setup page | H | M | M | It decides whether a researcher can start at all |

### Next: prove it with a real user (about 2–4 weeks)

| Item | V | E | U | Why next |
|---|---|---|---|---|
| 5. Researcher quick start | H | L | L | Needed for item 6 |
| 6. Watched first run | H | L | M | First evidence from the actual user |
| 7. Gold-labelled set | H | M | M | Turns accuracy from "silver" into evidence; answers item 10 |
| 8. CI install smoke | M | M | L | Protects item 3 once it works |
| 9. Drive filing cost | M | M | M | Only if item 4 shows it's slow |

### Later: hosting, scale and learning Google Cloud

| Item | V | E | U | Notes |
|---|---|---|---|---|
| 11. Sign-in fix → Cloud Run + Vertex | M (H for the Google Cloud learning goal) | M | H | Worth doing as a learning project once the local path is solid |
| 12. Several people on one Drive folder | M | M | M | Only if researchers will share one folder |
| 13. Split `web/app.py` | M | M | L | After git and tests make refactoring safe |
| 14, 15 | L–M | L–M | M | When asked for |

**Decisions the owner should make soon**

- Which setup the researcher actually needs: a Mac app on a local folder, a shared Drive folder, or a hosted web page. This changes the order of items 4, 11 and 12.
- Who can label 100–200 photos for the gold set.
- Whether species-level sorting is in scope this season. If it is, it depends on `_training_data` examples, not on the model.

---

## 7. AI-assisted development: what helped, what hurt

### Evidence it helped

- **Implementation speed.** The approved plan became about 20 modules, 24 tests, deploy scripts and a README in about 24 minutes (10:13 → 10:37, from transcript timestamps). Later requests took 1–13 minutes of assistant time each. Across the whole build, the assistant worked about 3 to 3.5 hours. That is under an hour on Oct 4 (approximate, because the laptop slept mid-turn), about 126 minutes on Oct 9 up to 17:16, about 11 minutes for the review and about 8 minutes for the efficiency fixes.
- **Exploration.** The CloudKit shared-album API was reverse-engineered and tested live in about 3 minutes. The plan stage checked facts against the web and corrected a wrong model size (400 MB → 1.5 GB) before any code was written.
- **Measurement over opinion.** Accuracy was measured on 69 real photos. A bad score (turtle → shark) was reproduced and fixed with a before/after table. Efficiency fixes were benchmarked (43 s → 20 ms).
- **Tests as a by-product.** The suite grew from 0 to 60, with fakes for the model and for Drive. Tests caught real bugs: same-second undo records (12:17) and a batching bug (22:03). A scenario test exposed the scoring formula bug (16:23).
- **Documentation kept current.** The README was updated in most feature turns.

### Evidence of friction

- **UI rework.** About 6 UI redesigns in 2.5 hours. Three of them followed explicit pushback: "terrible UI", "UI should be simpler", "this view has lot of issues". 6 of about 30 owner messages reported something broken or unwanted in the app, and 3 more were environment errors.
- **Claims ahead of checks.** The v0.2 summary described the cookie as "encrypted"; it is only signed. Drive, Cloud Run, Vertex and the installers are documented step by step but have never run.
- **Defects found late.** One review at the end found 15 + 7 bugs and 3 efficiency problems. One of these, the move-history check, would have taken 43 seconds per History page at 30,000 moves.
- **Workaround before root cause.** The iCloud hidden-file problem appeared on Oct 4 and was patched with `chflags`. It cost four more owner messages on Oct 9 before the environment was moved out of the synced folder.
- **Hard-to-review changes.** In this project's main session the assistant made 259 shell calls but only 13 direct file edits and writes; most code was written through scripts. With no git there are no diffs to review. One patch script failed silently on a stale anchor (20:30).
- **Scope growth.** The web app reached 1,103 lines in one file. The owner had to ask for "DRY and KISS" explicitly.
- **Context limits.** The session was compacted twice. Decisions survived because they were saved in memory files and in the summary.
- **Communication.** "Cut" in the review summary was read as "fixed" and needed a follow-up question.

### What we can and can't conclude

- **Can.** With the assistant, a working local prototype with tests, measurements and docs went from plan to usable on this Mac within one day (Oct 9, 09:31–17:16). Most rework was in the UI and followed the owner's feedback, not technical failures.
- **Can't.** That this was faster or better than building it by hand, because there is no baseline. That a researcher can use it, because no researcher has tried. That accuracy holds on real I.CARE data, because only silver labels exist. How much of the owner's own time the iterations took, because it isn't recorded.

### What to measure from now on

- **Lead time per request:** from the message to a verified result.
- **Rework rate:** follow-up messages that report "broken" or "not what I meant", per feature.
- **Review findings per change:** after each feature, not only at the end.
- **Unverified components:** count them in each release note; aim for zero in the researcher path.
- **Accuracy on the gold set:** per release.
- **Time to first sorted photo** for a new user: the target is under 15 minutes on macOS and Windows.

---

## 8. Workflow improvements (3–5, each with a simple check)

| # | Change | How to evaluate |
|---|---|---|
| 1 | **Git with one commit per request.** The message says what was verified and what wasn't. | Every change has a diff. Count the commits whose "not verified" list is non-empty, and work it down to zero for the researcher path. |
| 2 | **Agree on the UI before building it.** A text mockup or a screenshot of a static page, approved in one round, before any code. | UI redesign requests per feature. Target: at most 1, compared with about 6 for the main screen this time. |
| 3 | **Definition of done = tests + a real-browser check + an explicit list of what wasn't run.** No security or "works" claim without a check. | Count review findings that contradict a claim in a summary. Target: 0. |
| 4 | **Review in small batches.** Run `/code-review` on each feature's diff and fix blockers before the next feature. | Findings per review stay small (under 5), and no review finds an issue older than one feature. |
| 5 | **Test in the real environment early.** A scratch Drive folder, the gold set and a fresh-install CI job, run each release. | Pass or fail on each, recorded in the release note. The component status table loses its ⚠️ rows over time. |
