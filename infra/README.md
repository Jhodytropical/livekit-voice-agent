# Deploying this agent to Azure Container Apps

```bash
az login
PLAN_ONLY=1 ./infra/deploy-azure.sh   # print the plan, create nothing
./infra/deploy-azure.sh               # provision, build, deploy
```

Three files:

| File | What it is |
|---|---|
| `Dockerfile` (repo root) | Two-stage build. No model download by default — see below. |
| `infra/containerapp.yaml` | The app manifest. Reviewable, committed, contains no secrets. |
| `infra/deploy-azure.sh` | Provision → build → grant → deploy → verify. Idempotent. |

Everything below is a decision that could have gone the other way, with the
reason it did not.

---

## Scale-to-zero is wrong here, and that is the whole architecture

Container Apps' headline feature is scaling to zero between requests. For this
workload that would be a silent outage.

A LiveKit agent worker is not woken by an inbound request. It **dials out** to
LiveKit over a websocket, registers, and waits to be handed a job. If the worker
is not already connected when a call arrives, LiveKit has no worker to dispatch
to and the caller hears silence. There is no request to trigger a scale-up,
because the request never reaches Azure at all.

So:

- `minReplicas: 1` — the connection must pre-exist the call.
- **No `ingress` block.** Nothing dials in. Omitting ingress means no public
  FQDN and no listener exposed to the internet.
- `maxReplicas: 2` with `rules: []` — which, with no ingress, means pinned at 1.
  Container Apps' default scaler is HTTP concurrency and cannot fire without
  ingress. The headroom is there so adding a KEDA rule later is a manifest edit
  rather than a redeploy of a differently-shaped app.

**The honest limitation:** capacity today is one worker's job slots. That is the
right first limit to hit, because it is measurable. Scaling correctly means a
KEDA rule keyed on active jobs, which means exporting that metric first. Not
done, and not claimed.

The cost consequence is worth stating plainly to anyone paying the bill: this
deployment bills for one always-on replica (1 vCPU / 2 GiB), not per call. If
call volume is low and bursty, a per-second telephony provider may be cheaper
than a warm worker. That is a real trade-off, not a detail.

---

## The registry password does not exist

The container app authenticates to ACR with a **user-assigned managed identity**
holding `AcrPull`. The registry is created with `--admin-enabled false`. There is
no registry username or password anywhere — not in the manifest, not in a
pipeline variable, not in the app's secret store. A credential that was never
created cannot leak or need rotating.

**Why user-assigned rather than system-assigned.** A system-assigned identity does
not exist until the app exists, so it cannot hold `AcrPull` at create time. The
Azure CLI works around this by deploying a hello-world image first and swapping
it afterwards — a two-revision deploy, and one that only happens on the flag
path, not with `--yaml`. A user-assigned identity is created and granted
`AcrPull` *before* the app, so the first revision pulls the real image on the
first attempt.

The role assignment uses `--assignee-object-id` with an explicit
`--assignee-principal-type`. This skips the Microsoft Graph lookup, which is the
usual reason this step fails for a signed-in user who cannot read the directory.

---

## The health probe is a real signal, not decoration

`livekit-agents` serves `GET /` on the health port and returns **503** when the
inference subprocess has died or the LiveKit connection failed
(`worker.py`, `health_check`). That is what makes a liveness probe worth having:
a worker that has silently lost its websocket takes no calls, looks perfectly
healthy to any process-level check, and would sit there indefinitely. This probe
restarts it.

Port **8081** is the `livekit-agents` production default (`worker.py`,
`prod_default=8081`). `dev` mode binds `:0` and picks a random port — that is
the five-digit port in local logs, and pointing a probe at it would fail.

A startup probe with a 150-second budget covers loading the Silero session and
registering with LiveKit, so a slow cold start does not trip liveness and put
the app in a restart loop.

Probes are only expressible in the YAML manifest, not through the CLI flags.
That is one of the two reasons this deploy is manifest-driven.

---

## Model weights: the bug this file exists to remember

The first version of the Dockerfile baked model weights with
`ENV XDG_DATA_HOME=/opt/models`. **That variable is wrong**, and the failure is
invisible: the image builds, the directory is created, `COPY --from=builder`
succeeds, and the image looks pre-baked. Measured instead of assumed:

| Variable set | Target dir after `download-files` | Where 461 MB actually went |
|---|---|---|
| `XDG_DATA_HOME=/tmp/models_test` | **4.0K** | `/root/.cache/huggingface` |
| `HF_HOME=/tmp/hf_test` | **461M** | nothing left behind |

The turn detector is a HuggingFace model and obeys HuggingFace's own cache
variable. With `XDG_DATA_HOME` the weights would have re-downloaded on every
cold start — the exact failure the bake exists to prevent.

Then the follow-up question: does this agent need them at all?

- **Silero VAD needs no download.** The onnx ships inside the
  `livekit-plugins-silero` wheel. Verified: with an empty cache and
  `HF_HUB_OFFLINE=1`, `silero.VAD.load()` returns in 2.3 s and writes nothing.
- **The turn-detector weights are therefore the only thing `download-files`
  fetches** — and this agent does not load them. `turn_handling.py` pins
  `turn_detection: "vad"` deliberately, so the pipeline stays Deepgram + OpenAI
  + Silero and nothing else.

Baking 461 MB for a code path that never executes is 461 MB of attack surface,
pull time and storage for nothing. So the bake is behind a build argument,
**off by default**:

```bash
BAKE_TURN_DETECTOR=1 ./infra/deploy-azure.sh   # only if you make the swap
```

---

## Why the secrets look like this

`infra/containerapp.yaml` is committed and contains no secrets — the values are
double-underscore placeholder tokens. At deploy time the script reads
`.env.local` (gitignored), renders a copy into a `0600` file inside a private
`mktemp -d`, deploys from it, and deletes it via an `EXIT`/`INT`/`TERM` trap.

Three things this avoids:

1. **Secrets in `argv`.** `az containerapp create --secrets key=value` puts every
   credential in the process table and in shell history. Nothing here passes a
   secret as an argument.
2. **Secrets in the repo.** The reviewable artifact and the rendered artifact are
   different files, and only one of them is ever written to the working tree.
3. **YAML corruption from a hostile secret.** Substitution is done in Python with
   `json.dumps`, not `sed`. A key containing `: `, `#`, or a quote will silently
   break a YAML document if pasted in raw; JSON strings are valid YAML and are
   correctly escaped. Tested with values containing `: `, `#`, `"`, `'`, `\`,
   `/`, `+` and `=` — all five round-trip byte-for-byte.

**The remaining trade-off, stated rather than hidden:** the secret values do land
in the Container Apps secret store, and briefly in a temp file. The stronger
option is Key Vault references, so Azure holds the only copy and rotation is a
vault operation. That is not wired up here, and the reason is honest: the
`Secret` model in the API version this CLI extension targets exposes only
`name` and `value`. Adding Key Vault means a different deploy path, and it is
the first thing to do if this ever holds a client's credentials rather than a
demo project's.

`LOG_TRANSCRIPTS` is deliberately **absent** from the manifest. Unset means
caller speech is stripped from log lines and only word counts and timings
survive (`log_redaction.py`). Setting it to `1` in a hosted deployment would put
every caller's spoken phone number into Log Analytics.

---

## Why `az acr build` and not `docker build`

The image is built **in Azure**, from a streamed build context. No local Docker
daemon is required, and the result is `linux/amd64` regardless of whether you
are on an Apple Silicon Mac — which otherwise produces an arm64 image that
Container Apps cannot run, with an error message that does not say so.

Images are tagged with the short git SHA, plus `-dirty` when the working tree
has uncommitted changes, so a deployed revision is never mistaken for a clean
commit.

---

## Verifying a deploy actually worked

`provisioningState: Succeeded` is not evidence. A container that is "Running"
but never registered with LiveKit takes no calls and looks entirely healthy in
the portal.

```bash
az containerapp logs show -n ca-voice-agent -g rg-voice-agent --follow --tail 100
```

Look for:

```
registered worker    id=AW_... region=... protocol=16
```

If the container restarts roughly every 90 seconds instead, the liveness probe
is working correctly: the health endpoint returns 503 when the LiveKit
connection failed, which almost always means a wrong `LIVEKIT_URL` / API key
pair rather than anything wrong with the image.

Then place a real call and confirm the job is picked up by **this** worker — not
by a `dev` process still running on a laptop against the same LiveKit project.
Two registered workers means the job goes to whichever LiveKit picks, and the
deploy proves nothing.

### Save the log BEFORE you tear down

```bash
az containerapp logs show -n ca-voice-agent -g rg-voice-agent --tail 300 > logs/azure_run8.log
az group delete -n rg-voice-agent --yes --no-wait
```

That order is a rule, not a suggestion. On the first real deploy it was done the
other way round: the export was attempted two minutes after `az group delete` and
returned `ResourceNotFound`, taking the Log Analytics workspace with it. The
evidence survived only because it was still in the operator's terminal scrollback.
The teardown command is cheap to re-run; the evidence is not.

---

## What has and has not been run

Stated precisely, because this repo's rule is that a claim needs a run behind it.

**Verified by execution:**

- `HF_HOME` vs `XDG_DATA_HOME`, both measured (table above).
- Silero VAD loading offline from an empty cache with `HF_HUB_OFFLINE=1`.
- `infra/containerapp.yaml` deserializing through the *same* SDK models the
  `containerapp` CLI extension uses for `--yaml` (API `2025-10-02-preview`) —
  confirming no ingress, identity-based registry auth with no username or
  password, both probes, `minReplicas: 1`, and all five secret references.
- The manifest rendering step, extracted from `deploy-azure.sh` itself and run
  against secrets containing `: `, `#`, quotes, backslashes, `/`, `+` and `=`.
- `bash -n` and `shellcheck -S warning` clean on `deploy-azure.sh`.
- Every `az` flag used here checked against `az --help` on the installed CLI
  (2.88.0, `containerapp` extension 1.3.0b4) rather than written from memory.

**Verified by running it — 2026-07-28** (full write-up: `docs/acceptance-findings.md`,
run 8):

- The whole script, end to end, against subscription *MITS SFL Learning*. Build
  `ch1` succeeded in **2m1s**; total wall clock about ten minutes.
- **`registered worker`** at `02:16:14`, **2.46 s** after `starting worker` — with
  the 461 MB bake skipped, proving Silero loads from the wheel in a real image.
- Only three plugins registered (`deepgram`, `openai`, `silero`). The turn
  detector was never loaded, exactly as `BAKE_TURN_DETECTOR=0` assumes.
- `HTTP server listening on :8081` — the port the probes point at.
- `ingress: null` on the deployed resource. No public FQDN.
- One live call handled end to end with **no local `dev` process running**, so the
  job could only have gone to the container.
- PII redaction held into **Log Analytics**, a third-party sink: `transcript
  logging: redacted`, `caller_name: "M***[redacted]"`, `phone: "******0142"`.

**One bug this deploy found, in the script itself.** The Verify block ran under
`set -euo pipefail` and aborted on `Subscription ... is not registered for the
Microsoft.App resource provider` — *after* the container app had been created
successfully. `az provider register --wait` returns before the Container Apps
control plane sees the registration everywhere; the extension retries internally,
a plain `az containerapp show` does not. A verification step that reports failure
for work that succeeded is worse than none, so Verify now runs under `set +e`,
retries six times, and says explicitly that an unreadable state is not a failed
deploy.

**Still not verified:** autoscaling. Capacity is one worker's job slots, because a
KEDA rule keyed on active jobs needs that metric exported first. And this is one
call in one region — say *"deployed to Azure Container Apps and verified with a
live call"*, not *"running in production"*.
