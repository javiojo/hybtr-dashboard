# Google Drive setup for the Seeking Alpha ingest job

The weekday-evening workflow can pick up Javier's official Seeking Alpha Excel export from the existing Google Drive folder **Seeking Alpha HYBTR** (`https://drive.google.com/drive/folders/1GCtlKEnOMtL-Y4vuL97h3QVQamJOKE0W`). This is read-only. Nobody needs to create credentials until you are ready; the Action skips the Drive step cleanly when the secret is missing.

Do **not** ask Webbie or another agent to create the Google Cloud project, service account, or key. That has to be done in Javier's Google account, once.

## Option A — automatic pickup from Drive (recommended)

You need a Google Cloud service account that can *view* that one folder, and a GitHub Actions secret holding its JSON key.

### 1. Create a service account with no project roles

1. In [Google Cloud Console](https://console.cloud.google.com/) choose (or create) a project you already own. A fresh, empty project named something like `hybtr-ingest` is fine. This is the only step that might prompt you to enable billing on a brand-new Google Cloud account; the Drive API and this use stay inside the free tier.
2. APIs & Services → Library → enable **Google Drive API**.
3. IAM & Admin → Service Accounts → **Create service account**.
   - Name it e.g. `hybtr-drive-reader`.
   - Skip every role on the project (no Editor, no Viewer, no Drive-related project role). Folder sharing in the next step is all the access it needs.
4. Open the new service account → Keys → **Add key** → **Create new key** → JSON. Save the file on your computer. It looks like `{"type":"service_account","project_id":"...","private_key":"-----BEGIN PRIVATE KEY-----...","client_email":"...@....iam.gserviceaccount.com",...}`.
5. Copy the `client_email` value (it ends in `.iam.gserviceaccount.com`).

### 2. Share only the one Drive folder, as Viewer

1. Open [Seeking Alpha HYBTR](https://drive.google.com/drive/folders/1GCtlKEnOMtL-Y4vuL97h3QVQamJOKE0W).
2. Share that folder (not "My Drive", not any parent folder) with the service account **email** from step 1.
3. Role: **Viewer**. Uncheck "Notify people" if you like — it is not a real inbox.
4. Confirm the rest of Drive is not shared with this email.

### 3. Add the key as a GitHub Actions secret

1. Open the repo → Settings → Secrets and variables → Actions.
2. New repository secret:
   - Name (exact): `GDRIVE_SA_KEY`
   - Value: the **entire** JSON file contents (the whole document, starting with `{`).
3. Do not commit the JSON file, paste it into a pull request, or store it in `data.js` / `index.html`. If a key ever leaks, delete that key in Google Cloud and create a new one.

After this is in place, the evening job (8:00 PM America/New_York, weekdays) and the "Run workflow" button will download the newest `.xlsx` in that folder, ingest it if it is newer than the last run, refresh prices and technicals from Yahoo, and commit to `main`.

## Option B — no Google credentials (manual drop)

If you would rather not create a service account:

1. Download the Excel from Seeking Alpha as usual.
2. In this GitHub repo, open the `incoming/` folder → **Add file** → **Upload files**.
3. Upload the `.xlsx`. The folder is gitignored for spreadsheets, but GitHub's web UI can still hold a file on a branch you then commit, **or** you can attach the path when running the workflow.
4. Repo → Actions → **Ingest Seeking Alpha export** → **Run workflow**. Optional input `file_path` can be set to e.g. `incoming/High-Yield, Best Total Return ETF's 2026-10-27.xlsx`.

A cleaner variation of the same idea: keep using Drive yourself, but when you want an update, download the file and upload it to `incoming/` (or pass `file_path`). The job never needs `GDRIVE_SA_KEY` in that case.

`.xlsx` files must not be committed to `main` for long; `incoming/*.xlsx` is in `.gitignore`. If you upload through the web UI it will be in that commit unless you delete it afterwards. Prefer Drive + the secret, or run the workflow on a branch that is not needed long-term, or delete the spreadsheet from the repo after the ingest commit has been made.

## What the job will not do

- It will not log into Seeking Alpha, scrape the screener, or refresh the Excel for you. The official Export button is still a manual step.
- It will not write to Drive (the service account is Viewer only).
- It will not ingest the same file twice; it records the last file name and modified time in `.ingest_state.json`.
