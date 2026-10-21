#!/usr/bin/env bash
# Deploy the web app to Cloud Run. Run once: deploy/gcloud-setup.sh. Then re-run this after changes.
set -euo pipefail
: "${PROJECT:?set PROJECT=<gcp project id>}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-photo-sorter}"
SA="photo-sorter-run@${PROJECT}.iam.gserviceaccount.com"

gcloud config set project "$PROJECT" >/dev/null
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$SA" \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 2 --cpu 1 --memory 1Gi --concurrency 4 --timeout 300 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT},PHOTO_SORTER_PROVIDER=vertex" \
  --set-secrets "GOOGLE_OAUTH_CLIENT_JSON=photo-sorter-oauth-client:latest,PHOTO_SORTER_SECRET=photo-sorter-session-secret:latest"

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')
# the app needs to know its public URL to build the OAuth redirect; set it once the URL exists
gcloud run services update "$SERVICE" --region "$REGION" --update-env-vars "PHOTO_SORTER_BASE_URL=${URL}" >/dev/null
echo
echo "Deployed: $URL"
echo "Add this redirect URI to the Web OAuth client in the Google Cloud console:  ${URL}/auth/callback"
