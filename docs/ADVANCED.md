# photo-sorter: advanced guide

For admins and developers. Researchers only need the [README](../README.md).

## How it works

Each photo is turned into an "embedding" (a list of numbers describing what it shows) by an image model:
SigLIP 2 running on the computer, or Vertex AI when hosted. That embedding is compared with the words of each
category (zero-shot) and with each category's example photos (few-shot), and the best match wins if it is
clear enough. No model training and no GPU are needed; it improves as examples are added.

```
Inbox/IMG_4101.jpg ──► embed (SigLIP 2 locally, or Vertex AI in the cloud)
                       ├─ zero-shot: how well does it match each category's words?
                       └─ few-shot:  how close is it to each category's example images?
                   ──► "Photos - Coral › Outplants › APAL" (confidence 0.31)
                   ──► move + rename to Photos - Coral/Outplants/APAL/apal_013.jpg   (undoable)
```

## Installing

Researchers paste one line (see the README). It downloads `install.sh` (Mac) or `install.ps1` (Windows) from
this repository and runs it. The script:

1. checks the computer (Mac: Apple chip, because PyTorch has no Intel Mac builds since 2.3; Windows: 64-bit
   Intel/AMD, plus the Microsoft Visual C++ Runtime that PyTorch needs);
2. installs [uv](https://docs.astral.sh/uv/) if it is missing, without changing the shell's PATH;
3. runs `uv tool install "photo-sorter[researcher] @ <this repo's main.zip>"` with `UV_PYTHON_PREFERENCE=only-managed`,
   so the app gets its own Python 3.12 and never uses conda, Homebrew or system Pythons, and nothing needs git;
4. runs `photo-sorter setup`, which downloads the image model into the Hugging Face cache;
5. writes a Desktop launcher (`Photo Sorter.command` on a Mac, a `Photo Sorter.lnk` shortcut on Windows) that
   runs `photo-sorter serve --open`.

Everything goes into the user's folder; the log is `~/Library/Logs/photo-sorter-install.log` (Mac) or
`%LOCALAPPDATA%\photo-sorter-install.log` (Windows). `uninstall.sh` / `uninstall.ps1` reverse it, using
`photo-sorter setup --remove-model` so the model's files are really freed.

Options, as environment variables: `PHOTO_SORTER_SOURCE=<folder or URL>` installs from a local copy or another
archive (for example a release), and `PHOTO_SORTER_UNATTENDED=1` skips the question and does not open the app.
From a checkout: `PHOTO_SORTER_SOURCE=. bash install.sh`. CI (`.github/workflows/ci.yml`) runs exactly this on a
Mac and on Windows for every push: install, start, check, uninstall.

By hand instead: `uv tool install --python 3.12 ".[researcher]"` from this folder, then `photo-sorter serve --open`.
Developers: `uv sync --extra dev && uv run pytest`.

> Python 3.11 to 3.13. The default model `google/siglip2-base-patch16-224` needs about 4 GB of RAM.
> **If the project folder is synced by iCloud Drive** (for example anywhere under Desktop or Documents with
> "Desktop & Documents" on), keep the virtualenv outside it: add
> `export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/photo-sorter"` to `~/.zshrc`, delete `.venv`, and run
> `uv sync` again. iCloud marks rewritten files hidden and makes "photo-sorter 2" duplicates, and Python
> then reports "No module named photo_sorter".


## 1. Categories: `CATEGORIES.md` in the photo folder

The list lives at the top of the photo folder as `CATEGORIES.md` (also in Google Drive), so it travels with the
folder. Edit it in the web UI (**Categories** page, an outliner: type to rename or describe, `+` for a
sub-category, `−` to remove, arrows to reorder or nest) or in the file. Edits made elsewhere (a colleague, Drive,
a text editor) show up on the next page load. The file is a plain nested bullet list:

```markdown
# Categories

- Coral: hard coral colonies on the reef
  - Bleaching: pale or white coral
- Sponge: sponges, sponge fragments on ropes or epoxy
- Diver: a scuba diver or snorkeler in the frame
- People: staff portraits or groups of people on land
```

Each line is a category; the text after the colon tells the model what the photos look like (this is
what zero-shot classification reads, so a few concrete words help). Indent to make sub-categories; a
category with sub-categories acts as a folder and photos are filed into the leaves, e.g.
`Coral/Bleaching/bleaching_001.jpg`. Names starting with "Videos" apply to videos only. Notes after a
`## ` heading are kept untouched when the UI saves the file. Start simple (four or five categories) and
split later; the filed photos stay where they are, and the manifest still lets you undo.

The setup page also offers ready-made lists from `photo_sorter/presets/` (for example *Coral reef restoration*).
To add one, drop a CATEGORIES.md-style file there: its `# ` heading is the title and its first paragraph the
description. For a team, the simplest way to share a list is to put `CATEGORIES.md` in the shared folder:
everyone who opens it gets the list automatically.

## 2. Settings

**Per folder**, in `<folder>/.sorter/settings.json` (written by the setup page; all keys optional):

```json
{ "inbox": "", "min_confidence": 0.15, "review_category": "Review", "review_action": "move",
  "naming": { "pattern": "{slug}_{n:03d}{ext}" }, "training_subfolder": "_training_data" }
```

`"inbox": ""` (the default) sorts the photos at the top of the folder itself; `"inbox": "Inbox"` reads new photos
from a separate subfolder instead (the app's "Use a separate folder for new photos" link). **Per computer or server**, the model comes from
the environment: `PHOTO_SORTER_PROVIDER` (`siglip` on laptops, `vertex` when hosted), `PHOTO_SORTER_MODEL`,
`GOOGLE_CLOUD_PROJECT`. The last folder used on a laptop is remembered in `~/.config/photo-sorter/app.json`.

**Optional config file** (advanced, and for older setups): a `categories.json` in the current folder or `-c file`
overrides the above. If it names a `categories_file`, that list is used for every folder instead of the folder's own.

```jsonc
{
  "categories_file": "CATEGORIES.md",
  "source": { "kind": "drive", "root": "https://drive.google.com/drive/folders/<id>", "inbox": "Inbox" },
  // "kind": "local" with a folder path also works (e.g. a Google Drive for Desktop folder)
  "embeddings": { "provider": "siglip", "model": "google/siglip2-base-patch16-224", "knn_weight": 0.6, "knn_k": 3 },
  // hosted: { "provider": "vertex", "model": "multimodalembedding@001", "project": "<gcp project>", "location": "us-central1" }
  "min_confidence": 0.15,            // margin between the best and second-best category; below -> Review
  // embeddings.min_fit (default 0.02): the best category must also *fit* the image on its own (SigLIP's
  // per-label probability); otherwise it goes to Review as "no category fits". Add an "Other" category
  // if many photos legitimately match nothing.
  "review_category": "Review", "review_action": "move",   // or "keep" to leave unsure items in the inbox
  "naming": { "pattern": "{slug}_{n:03d}{ext}" },
  "training_subfolder": "_training_data"
}
```

## 3. Daily use

```bash
photo-sorter scan                     # what is in the inbox
photo-sorter classify --limit 10      # predictions only, nothing moves
photo-sorter file --dry-run           # the exact moves it would make
photo-sorter file                     # move confident items; add --all to send the rest to Review/
photo-sorter undo --last 5            # put them back
photo-sorter refs                     # how many example images each category has
photo-sorter serve --open             # web UI at http://127.0.0.1:8080 (pick the folder there)
```

The commands use the folder chosen in the web UI; `--source <folder or Drive link>` picks another one.

**Web UI** (`photo-sorter serve`): opening it never starts any work. The first screen is the inbox as a
pile of photos with the count and a time estimate; click the pile to list every image, and press
**Sort** when ready. While sorting, a status panel shows the three steps (load model, look at images,
ready to file), the image being looked at, a progress bar with speed and time left, the latest results
and an activity log; it disappears when the run ends. Results come back as one pile per category plus a
**Needs review** pile: click a pile to check it, change any image's category, and press **File** for the
pile (or **File all labeled**). Every filing shows a toast with **Undo**. If the inbox is empty, the page
explains where to put photos. The **Categories** page is an outliner: edit names and descriptions in place,
`+` adds a sub-category, `−` removes (with Undo), arrows reorder and nest.

**One by one (fastest):** **▶ Decide** on the review pile, **▶ One by one** on any list, or a click on a
thumbnail opens a full-size viewer. Categories appear as numbered tags, with the model's suggestion starred
and highlighted. Press a number to file the image there and jump to the next one.

| Key | Does |
|---|---|
| `1`–`9` | file into that category |
| `Enter` | file into the highlighted category (the suggestion, or the first search match) |
| type letters | search categories; if nothing matches, `Enter` creates a category with that name and files there |
| `↑` `↓` | move the highlight |
| `←` `→` | previous / next image (skip) |
| `Shift`+`Enter` | add the image as an example of the highlighted category, without moving it |
| `Ctrl`/`⌘`+`Z` | undo the last filing (the image comes back with its suggestion) |
| `Esc` | clear the search, or close |

In the lists, **✓** files an image into its suggested category and the **other category…** dropdown files it as
soon as you pick (including **＋ New category…**, then Enter). Every filing can be undone.

**Teaching it (examples):** besides the words in `CATEGORIES.md`, each category learns from example photos in
`<category>/_training_data/`. Add them from the **Categories** page: the 🖼 button on a category opens its
examples, where you can drop or upload your own photos, or search the web (openly licensed images from
Openverse; the search box starts with the words the model reads) and click **＋ Add**. In the review list,
**＋ example** copies an image into the chosen category's examples without moving it, and **＋ New category…**
in the dropdown creates a category right there. Photos you file into a category also count as examples
(the newest 50 per category; `embeddings.use_filed_as_examples` / `max_filed_examples_per_category`).
Examples are re-read on every Sort, so changes apply without a restart; the page then offers **Re-sort all**.
How close an image must be to a category's examples is calibrated from how alike those examples are, so 5 to
20 consistent photos work best. Removing an example moves it to `.sorter/trash/` (or the Drive trash), and
every added example is logged with its source and licence in `.sorter/examples.jsonl`.

**Fixing a mistake:** a picture teaches exactly one category (it is recognised by its content, so renamed
copies count as the same picture). If a photo was filed into the wrong category, and maybe also added as an
example there, open that category's 🖼 panel on the Categories page: it shows both its examples and the filed
photos that teach it. Select the wrong photo, choose **Move to…** and press **Move**: the photo is re-filed
(recorded in History, undoable one step at a time) and every example copy follows it to the new category.
**Check this category** then re-judges each photo against all categories, leaving it out of its own
comparison, and marks the ones that look like another category, which catches photos the bad example may
have pulled in. **Move to…** is also on the Filed list and on History. Suggestions still waiting in the
inbox are flagged and **Re-sort all** refreshes them.

**Checking accuracy:** make a CSV with `path,category` for photos you know, then
`photo-sorter eval --labels labels.csv` (with a local source). It prints accuracy, a threshold sweep
(accuracy among filed items vs. share sent to Review) and the top confusions. `--loo` uses the labelled
set as its own references, leave-one-out, to show what few-shot learning buys.

## What a run costs

Every sort reports what it used, before, during and after the run, and in the CLI after `classify` / `file`:

* **Model**: the exact id and revision (e.g. `google/siglip2-base-patch16-224 @ 75de2d55ec`, or the exact Claude
  model that answered), where it ran (this computer with CPU/GPU and chip, Google Cloud region and project, or
  the Anthropic API), and for local models the weights folder, size on disk, parameters and memory.
* **Usage**: images computed vs. taken from the embedding cache, tokens (image patches and text for SigLIP;
  input/output tokens for Claude/Gemini; billed images and text characters for Vertex), API calls, model time
  per image, estimated FLOPs.
* **Compute**: wall time, CPU seconds, peak memory of the process.
* **Money**: local models have no API charge ($0, the cost is your machine's time). Cloud models are priced
  from list prices as estimates; set or correct them in `categories.json`:

```jsonc
"pricing": {
  "claude-haiku-4-5": { "input_per_mtok": 1, "output_per_mtok": 5 },
  "multimodalembedding@001": { "per_image": 0.0001, "per_1k_text_chars": 0.0002 },
  "gemini-2.5-flash-lite": { "input_per_mtok": 0.1, "output_per_mtok": 0.4 }
}
```

Each run is appended to `.sorter/runs.jsonl` in the photo folder, and the History page totals it
("Spending in this folder").

## 4. Google Drive setup (one time, by the admin)

The tool acts as the signed-in researcher, so Drive keeps normal ownership and permissions. This needs a
Google Cloud project; the steps double as a Google Cloud tour.

1. **Project:** console.cloud.google.com, create a project, enable billing (Drive API and Cloud Run have
   free tiers; Vertex embeddings cost about $0.0001 per image).
2. **APIs and resources:** `PROJECT=<id> ./deploy/gcloud-setup.sh` enables Drive, Vertex AI, Cloud Run,
   Artifact Registry, Cloud Build, Secret Manager and Cloud Scheduler, creates the Cloud Run service
   account with `roles/aiplatform.user` and `roles/secretmanager.secretAccessor`, and creates two
   secrets (session key, OAuth client).
3. **OAuth consent screen** (APIs & Services): External, add the `.../auth/drive` scope, add the lab
   members as test users, then **publish** it. In Testing status refresh tokens expire after 7 days.
   The "unverified app" warning on sign-in is expected for an internal tool.
4. **OAuth clients** (Credentials > Create credentials > OAuth client ID):
   * *Desktop app* → download JSON → save as `~/.config/photo-sorter/client_secret.json` on each laptop
     (Windows: `%USERPROFILE%\.config\photo-sorter\client_secret.json`), or put it at the top of a local copy
     of this repository and install from it with `PHOTO_SORTER_SOURCE`
     (or set `GOOGLE_OAUTH_CLIENT_JSON` to the path or the JSON itself). Used by `photo-sorter auth`.
   * *Web application* → authorized redirect URIs `http://localhost:8080/auth/callback` and later
     `https://<cloud-run-url>/auth/callback` → download JSON →
     `gcloud secrets versions add photo-sorter-oauth-client --data-file=web_client.json`.
5. **Share the Drive folder** with each researcher (Editor). The tool needs the `drive` scope because
   `drive.file` cannot move files it did not create.

Then on a laptop: **Sign in with Google** on the setup page (or `photo-sorter auth`) and paste the folder link.
Files never leave Drive; only 640px thumbnails are downloaded to classify, and `.sorter/` inside the folder
holds the settings, the manifest and the embedding cache.

## 5. Hosting on Google Cloud (Cloud Run)

```bash
PROJECT=<id> ./deploy/deploy-cloud-run.sh
```

Builds the container with Cloud Build (from `deploy/Dockerfile`, `cloud` extra), pushes it to Artifact
Registry and deploys a Cloud Run service that scales to zero. The service uses **Vertex AI** embeddings
through its service account (no model weights, 1 vCPU / 1 GiB is enough), reads the OAuth client and the
session key from **Secret Manager**, and keeps each user's Drive token only in an encrypted browser
cookie, so nothing about the photos is stored on the server. After the first deploy, add the printed
`/auth/callback` URL to the Web OAuth client. Logs are in Cloud Logging; costs are Vertex per image plus
Cloud Run per request.

Using it: open the service URL, **Continue with Google**, paste the Drive folder link, and go through the same
three setup steps as on a laptop (only Drive links are offered on the server). The image carries no config:
each folder brings its own `CATEGORIES.md` and `.sorter/settings.json`, so a laptop and the hosted app can
work on the same Drive folder. Vertex and SigLIP are different embedding spaces, so each keeps its own cache
and example index inside `.sorter/`; switching re-embeds the example images once.

Secondary target, any VPS with Docker: `deploy/docker-compose.yml` runs the same image with the local
SigLIP model (4 GB RAM). Put Caddy or nginx with TLS in front and set `PHOTO_SORTER_BASE_URL`.

## 6. iCloud (optional, legacy)

Albums shared from Photos (`photos.icloud.com/shared/album/...`) are still supported as a read-only
source with the original three-step flow: `photo-sorter fetch`, `classify` (writes `results/results.csv`),
`apply --dest <folder> | icloud | photos`. Set `source.kind` to `icloud`. `--dest icloud` writes to the
iCloud Drive folder on a Mac; `--dest photos` imports into Apple Photos albums (needs `uv sync --extra photos`).

## Layout

```
photo_sorter/presets/            ready-made category lists offered on the setup page (add a .md file to add one)
categories.json                  optional local config file (git-ignored, overrides the folder's settings)
photo_sorter/taxonomy.py         the category tree: parse / save CATEGORIES.md, add / rename / delete
photo_sorter/config.py           settings + categories; category paths joined with " › "
photo_sorter/storage/            local folder and Google Drive backends (list, thumbs, folders, move+rename)
photo_sorter/embedders/          SigLIP 2 (local) and Vertex AI (cloud) embedders
photo_sorter/references.py       embedding cache + per-category reference images (_training_data, corrections)
photo_sorter/embedding_classifier.py  zero-shot + few-shot blend -> category, confidence, reason
photo_sorter/sorter.py           scan -> predict -> file / undo, manifest, counters
photo_sorter/manifest.py         .sorter/manifest.jsonl inside the photo folder
photo_sorter/web/                FastAPI + HTMX UI (also the Cloud Run entry point)
photo_sorter/cli.py              auth, setup, scan, classify, file, undo, refs, eval, serve
photo_sorter/legacy_icloud.py    iCloud shared-album flow (fetch / classify / apply)
install.sh, install.ps1           laptop installers (uninstall.sh, uninstall.ps1 remove everything)
deploy/                          Dockerfile, Cloud Run scripts, docker-compose
tests/                           pytest (fake embedder, fake Drive, web client)
```
