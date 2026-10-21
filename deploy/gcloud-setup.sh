#!/usr/bin/env bash
# One-time Google Cloud setup for photo-sorter. Requires: gcloud CLI, a project with billing enabled.
#   PROJECT=my-project ./deploy/gcloud-setup.sh
# Afterwards create two OAuth clients by hand (console > APIs & Services > Credentials):
#   1. Desktop app  -> download JSON -> ~/.config/photo-sorter/client_secret.json   (CLI on laptops)
#   2. Web application -> redirect URIs: http://localhost:8080/auth/callback and <cloud run url>/auth/callback
#      -> download JSON -> store it as the secret below:  gcloud secrets versions add photo-sorter-oauth-client --data-file=web_client.json
set -euo pipefail
: "${PROJECT:?set PROJECT=<gcp project id>}"
REGION="${REGION:-us-central1}"
gcloud config set project "$PROJECT" >/dev/null

echo "Enabling APIs ..."
gcloud services enable drive.googleapis.com aiplatform.googleapis.com run.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com

echo "Service account for Cloud Run ..."
SA_NAME=photo-sorter-run
SA="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || gcloud iam service-accounts create "$SA_NAME" --display-name "photo-sorter Cloud Run"
for ROLE in roles/aiplatform.user roles/secretmanager.secretAccessor roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$ROLE" --condition=None >/dev/null
done

echo "Secrets ..."
gcloud secrets describe photo-sorter-session-secret >/dev/null 2>&1 || \
  python3 -c "import secrets; print(secrets.token_hex(32))" | gcloud secrets create photo-sorter-session-secret --data-file=-
gcloud secrets describe photo-sorter-oauth-client >/dev/null 2>&1 || \
  gcloud secrets create photo-sorter-oauth-client --replication-policy automatic
echo
echo "Done. Next: create the OAuth consent screen (External, publish it) and the two OAuth clients, then"
echo "  gcloud secrets versions add photo-sorter-oauth-client --data-file=web_client.json"
echo "  PROJECT=$PROJECT ./deploy/deploy-cloud-run.sh"
