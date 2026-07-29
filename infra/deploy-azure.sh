#!/usr/bin/env bash
#
# Deploy the inbound voice agent to Azure Container Apps.
#
#   ./infra/deploy-azure.sh              # provision, build, deploy
#   PLAN_ONLY=1 ./infra/deploy-azure.sh  # print what it would do, touch nothing
#
# Idempotent: every create is guarded by a show, so re-running after a partial
# failure resumes rather than erroring. Safe to run twice.
#
# What this script never does:
#   - put a secret in argv (visible in `ps` and in shell history)
#   - write a secret to the repo, or to any path that outlives the run
#   - create a registry admin password
#   - hard-code a subscription, tenant, or credential
#
# Requires: az >= 2.60 with the containerapp extension, git, openssl, python3.
# Written for bash 3.2 so it runs on stock macOS as well as Linux.

set -euo pipefail

# ---- configuration (override by exporting before you run) ------------------
LOCATION="${LOCATION:-eastus2}"
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-voice-agent}"
ENV_NAME="${ENV_NAME:-cae-voice-agent}"
APP_NAME="${APP_NAME:-ca-voice-agent}"
IDENTITY_NAME="${IDENTITY_NAME:-id-voice-agent-acrpull}"
IMAGE_REPO="${IMAGE_REPO:-voice-agent}"
BAKE_TURN_DETECTOR="${BAKE_TURN_DETECTOR:-0}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env.local}"
MANIFEST="$REPO_ROOT/infra/containerapp.yaml"

REQUIRED_VARS="LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET DEEPGRAM_API_KEY OPENAI_API_KEY"

# ---- scratch space that cannot outlive this run ---------------------------
WORKDIR=""
cleanup() {
  if [ -n "$WORKDIR" ] && [ -d "$WORKDIR" ]; then
    find "$WORKDIR" -type f -exec rm -f {} + 2>/dev/null || true
    rm -rf "$WORKDIR" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
info() { printf '    %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

# ---- 0. preflight ----------------------------------------------------------
say "Preflight"

command -v az      >/dev/null 2>&1 || die "az not found. https://learn.microsoft.com/cli/azure/install-azure-cli"
command -v git     >/dev/null 2>&1 || die "git not found."
command -v openssl >/dev/null 2>&1 || die "openssl not found."
command -v python3 >/dev/null 2>&1 || die "python3 not found."

if ! az extension show -n containerapp >/dev/null 2>&1; then
  info "installing the containerapp extension"
  az extension add -n containerapp --allow-preview true -y >/dev/null
fi

ACCOUNT_JSON="$(az account show -o json 2>/dev/null || true)"
[ -n "$ACCOUNT_JSON" ] || die "not signed in. Run: az login"

SUBSCRIPTION_ID="$(printf '%s' "$ACCOUNT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
SUBSCRIPTION_NAME="$(printf '%s' "$ACCOUNT_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
info "subscription: $SUBSCRIPTION_NAME ($SUBSCRIPTION_ID)"

[ -f "$ENV_FILE" ] || die "no $ENV_FILE. Copy .env.example to .env.local and fill it in."

# Read one value out of the env file WITHOUT sourcing it. Sourcing an untrusted
# file executes it; this only ever reads. Trailing whitespace and surrounding
# quotes are stripped, which is the difference between a working key and an
# auth error that takes an hour to find.
envval() {
  python3 - "$ENV_FILE" "$1" <<'PY'
import sys
path, key = sys.argv[1], sys.argv[2]
val = None
with open(path, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() != key:
            continue
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        val = v
print(val if val is not None else "")
PY
}

MISSING=""
for VAR in $REQUIRED_VARS; do
  VALUE="$(envval "$VAR")"
  if [ -z "$VALUE" ] || [ "$VALUE" = "REPLACE_ME" ]; then
    MISSING="$MISSING $VAR"
  fi
done
[ -z "$MISSING" ] || die "missing or placeholder in $ENV_FILE:$MISSING"
info "all 5 required credentials present in .env.local (values not printed)"

# ACR names are globally unique, 5-50 lowercase alphanumerics. Deriving the
# suffix from the subscription id makes the name deterministic: re-running this
# script finds the registry it made last time instead of creating a second one.
ACR_SUFFIX="$(printf '%s' "$SUBSCRIPTION_ID" | openssl dgst -sha256 | tr -d ' ' | sed 's/.*=//' | cut -c1-10)"
ACR_NAME="${ACR_NAME:-acrvoiceagent${ACR_SUFFIX}}"

GIT_SHA="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo nogit)"
if [ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null || true)" ]; then
  GIT_SHA="${GIT_SHA}-dirty"
  info "working tree is dirty — tagging the image ${GIT_SHA} so the deployed"
  info "revision is never mistaken for a clean commit"
fi
IMAGE_TAG="${IMAGE_TAG:-$GIT_SHA}"

echo
info "resource group : $RESOURCE_GROUP        ($LOCATION)"
info "registry       : $ACR_NAME"
info "environment    : $ENV_NAME"
info "container app  : $APP_NAME"
info "identity       : $IDENTITY_NAME"
info "image          : $IMAGE_REPO:$IMAGE_TAG"
info "bake turn det. : $BAKE_TURN_DETECTOR (0 = skip the 461 MB download)"

if [ "${PLAN_ONLY:-0}" = "1" ]; then
  say "PLAN_ONLY=1 — nothing was created."
  exit 0
fi

# ---- 1. resource providers -------------------------------------------------
say "Resource providers"
for NS in Microsoft.App Microsoft.ContainerRegistry Microsoft.OperationalInsights Microsoft.ManagedIdentity; do
  STATE="$(az provider show -n "$NS" --query registrationState -o tsv 2>/dev/null || echo Unknown)"
  if [ "$STATE" = "Registered" ]; then
    info "$NS already registered"
  else
    info "registering $NS (first time on this subscription, can take a minute)"
    az provider register -n "$NS" --wait
  fi
done

# ---- 2. resource group -----------------------------------------------------
say "Resource group"
if az group show -n "$RESOURCE_GROUP" >/dev/null 2>&1; then
  info "$RESOURCE_GROUP exists"
else
  az group create -n "$RESOURCE_GROUP" -l "$LOCATION" -o none
  info "created $RESOURCE_GROUP"
fi

# ---- 3. container registry -------------------------------------------------
say "Container registry"
if az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" >/dev/null 2>&1; then
  info "$ACR_NAME exists"
else
  # --admin-enabled false is the default and is restated here on purpose: the
  # admin account is a shared username/password for the whole registry, and the
  # container app authenticates with a managed identity instead. There is no
  # registry password anywhere in this deployment.
  az acr create -n "$ACR_NAME" -g "$RESOURCE_GROUP" -l "$LOCATION" \
      --sku Basic --admin-enabled false -o none
  info "created $ACR_NAME"
fi
ACR_ID="$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)"
ACR_LOGIN_SERVER="$(az acr show -n "$ACR_NAME" -g "$RESOURCE_GROUP" --query loginServer -o tsv)"

# ---- 4. build the image, in Azure ------------------------------------------
say "Build image (remote — no local Docker daemon needed)"
info "az acr build streams the build context to the registry and builds there."
info "That also means the image is built for linux/amd64 regardless of whether"
info "you are on an Apple Silicon Mac."
az acr build \
  --registry "$ACR_NAME" \
  --image "${IMAGE_REPO}:${IMAGE_TAG}" \
  --image "${IMAGE_REPO}:latest" \
  --file "$REPO_ROOT/Dockerfile" \
  --build-arg "BAKE_TURN_DETECTOR=${BAKE_TURN_DETECTOR}" \
  --platform linux/amd64 \
  "$REPO_ROOT"

IMAGE="${ACR_LOGIN_SERVER}/${IMAGE_REPO}:${IMAGE_TAG}"
info "built $IMAGE"

# ---- 5. pull identity ------------------------------------------------------
say "Managed identity for registry pull"
if az identity show -n "$IDENTITY_NAME" -g "$RESOURCE_GROUP" >/dev/null 2>&1; then
  info "$IDENTITY_NAME exists"
else
  az identity create -n "$IDENTITY_NAME" -g "$RESOURCE_GROUP" -l "$LOCATION" -o none
  info "created $IDENTITY_NAME"
fi
IDENTITY_ID="$(az identity show -n "$IDENTITY_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)"
IDENTITY_PRINCIPAL_ID="$(az identity show -n "$IDENTITY_NAME" -g "$RESOURCE_GROUP" --query principalId -o tsv)"

# ---- 6. AcrPull ------------------------------------------------------------
say "AcrPull role assignment"
EXISTING="$(az role assignment list --assignee "$IDENTITY_PRINCIPAL_ID" --scope "$ACR_ID" \
             --role AcrPull --query "[0].id" -o tsv 2>/dev/null || true)"
if [ -n "$EXISTING" ]; then
  info "already granted"
else
  # --assignee-object-id with an explicit principal type skips the Microsoft
  # Graph lookup, which is the usual reason this step fails on a subscription
  # where the signed-in user cannot read the directory.
  az role assignment create \
    --assignee-object-id "$IDENTITY_PRINCIPAL_ID" \
    --assignee-principal-type ServicePrincipal \
    --role AcrPull \
    --scope "$ACR_ID" -o none
  info "granted AcrPull on $ACR_NAME"
  info "waiting 30s for the assignment to propagate before the first pull"
  sleep 30
fi

# ---- 7. container apps environment -----------------------------------------
say "Container Apps environment"
if az containerapp env show -n "$ENV_NAME" -g "$RESOURCE_GROUP" >/dev/null 2>&1; then
  info "$ENV_NAME exists"
else
  info "creating $ENV_NAME (provisions a Log Analytics workspace; ~2 minutes)"
  az containerapp env create -n "$ENV_NAME" -g "$RESOURCE_GROUP" -l "$LOCATION" -o none
fi
ENVIRONMENT_ID="$(az containerapp env show -n "$ENV_NAME" -g "$RESOURCE_GROUP" --query id -o tsv)"

# ---- 8. render the manifest and deploy -------------------------------------
say "Deploy"

WORKDIR="$(mktemp -d 2>/dev/null || mktemp -d -t voiceagent)"
chmod 700 "$WORKDIR"
RENDERED="$WORKDIR/containerapp.rendered.yaml"
( umask 077; : > "$RENDERED" )

# Substitution is done in python with json.dumps, not sed. A secret containing
# a colon, a '#', or a quote character will silently corrupt a YAML document if
# it is pasted in raw; json.dumps emits a correctly escaped double-quoted
# scalar, and JSON strings are valid YAML. Verified against keys containing
# ': ', '#', '"', "'", '/', '+' and '='.
# The heredoc is QUOTED (<<'PY'). An unquoted heredoc would expand $ and \
# inside the python source, which is a class of bug that only shows up when a
# secret happens to contain the wrong character. Every value crosses the
# boundary as an argv element instead.
python3 - "$MANIFEST" "$RENDERED" "$ENV_FILE" \
         "$LOCATION" "$ENVIRONMENT_ID" "$IDENTITY_ID" "$ACR_LOGIN_SERVER" "$IMAGE" <<'PY'
import json, sys

(manifest_path, out_path, env_path,
 location, environment_id, identity_id, acr_login_server, image) = sys.argv[1:9]


def envval(key):
    with open(env_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() != key:
                continue
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            return v
    raise SystemExit("missing %s in %s" % (key, env_path))


values = {
    "__LOCATION__": location,
    "__ENVIRONMENT_ID__": environment_id,
    "__IDENTITY_RESOURCE_ID__": identity_id,
    "__ACR_LOGIN_SERVER__": acr_login_server,
    "__IMAGE__": image,
}
for name in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
             "DEEPGRAM_API_KEY", "OPENAI_API_KEY"):
    values["__%s__" % name] = envval(name)

text = open(manifest_path, encoding="utf-8").read()
for token, value in values.items():
    text = text.replace(token, json.dumps(value))

leftover = sorted(t for t in values if t in text)
if leftover:
    raise SystemExit("unsubstituted tokens remain: %s" % leftover)

with open(out_path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY

info "manifest rendered to a 0600 file under $WORKDIR (deleted on exit)"

if az containerapp show -n "$APP_NAME" -g "$RESOURCE_GROUP" >/dev/null 2>&1; then
  info "app exists — updating to a new revision"
  az containerapp update -n "$APP_NAME" -g "$RESOURCE_GROUP" --yaml "$RENDERED" -o none
else
  info "creating $APP_NAME"
  az containerapp create -n "$APP_NAME" -g "$RESOURCE_GROUP" --yaml "$RENDERED" -o none
fi

cleanup
WORKDIR=""

# ---- 9. verify -------------------------------------------------------------
#
# Everything above this line has already been created. A read that fails here is
# a reporting problem, not a deployment problem — so this whole section runs with
# `set +e` and can never abort the script. The first version did abort, on the
# first real run, with "Subscription is not registered for the Microsoft.App
# resource provider" — AFTER the container app had been successfully created.
# An operator reading that output reasonably concludes the deploy failed. It had
# not. A verification step that reports failure for work that succeeded is worse
# than no verification step.
#
# Root cause of that read failure: `az provider register --wait` returns once the
# subscription-level registration is recorded, but the Container Apps control
# plane takes longer to see it everywhere. The containerapp extension absorbs
# this internally (it prints "Registering resource provider Microsoft.App ..."
# and retries); a plain `az containerapp show` does not. Hence the retry below.
say "Verify"

set +e

REG_STATE="$(az provider show -n Microsoft.App --query registrationState -o tsv 2>/dev/null)"
info "Microsoft.App     : ${REG_STATE:-unreadable}"

# Retry the first read: on a subscription where Microsoft.App was registered
# minutes ago, this is the call that trips over propagation lag.
PROVISIONING=""
ATTEMPT=1
while [ "$ATTEMPT" -le 6 ]; do
  PROVISIONING="$(az containerapp show -n "$APP_NAME" -g "$RESOURCE_GROUP" \
                   --query "properties.provisioningState" -o tsv 2>/dev/null)"
  [ -n "$PROVISIONING" ] && break
  info "provisioningState : not readable yet (attempt $ATTEMPT/6) — retrying in 10s"
  sleep 10
  ATTEMPT=$((ATTEMPT + 1))
done

if [ -n "$PROVISIONING" ]; then
  info "provisioningState : $PROVISIONING"
else
  info "provisioningState : STILL UNREADABLE after 6 attempts."
  info "                    This does NOT mean the deploy failed — the container"
  info "                    app was created above. Re-run the three commands"
  info "                    printed at the end of this script in a minute or two."
fi

FQDN="$(az containerapp show -n "$APP_NAME" -g "$RESOURCE_GROUP" \
         --query "properties.configuration.ingress.fqdn" -o tsv 2>/dev/null)"
if [ -z "$FQDN" ] || [ "$FQDN" = "null" ]; then
  info "public FQDN       : none  <-- correct, this worker has no ingress"
else
  info "public FQDN       : $FQDN  <-- UNEXPECTED. This app should have no ingress."
fi

info "replicas:"
az containerapp replica list -n "$APP_NAME" -g "$RESOURCE_GROUP" \
  --query "[].{replica:name, state:properties.runningState}" -o table 2>/dev/null

set -e

say "Last check: did the worker actually register with LiveKit?"
cat <<'EOS'
The deploy is only half the evidence. A container that is "Running" but never
registered takes no calls and looks perfectly healthy in the portal. These three
are the real check — and they are also what to re-run if anything above reported
"not readable yet":

  az containerapp show -n ca-voice-agent -g rg-voice-agent --query "properties.provisioningState" -o tsv
  az containerapp replica list -n ca-voice-agent -g rg-voice-agent -o table
  az containerapp logs show -n ca-voice-agent -g rg-voice-agent --follow --tail 100

Expected within ~30s of the replica starting:

  registered worker    id=AW_... region=... protocol=16

If instead you see the container restarting every ~90s, the liveness probe is
doing its job: the health endpoint returns 503 when the LiveKit connection
failed, which almost always means a wrong LIVEKIT_URL / API key pair rather
than anything wrong with the image.

Then place a real call and confirm the job is picked up by THIS worker, not by
a `dev` process still running on your laptop against the same LiveKit project.
Two registered workers means the job goes to whichever LiveKit picks.
EOS
