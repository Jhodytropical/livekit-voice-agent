# Inbound voice agent, containerised for Azure Container Apps.
#
# Four things here are not boilerplate, and each comes from how LiveKit agents
# actually behave rather than from a generic Python Dockerfile:
#
# 1. Silero VAD needs no download. The onnx weights ship inside the
#    livekit-plugins-silero wheel. Verified: with an empty HF cache and
#    HF_HUB_OFFLINE=1, silero.VAD.load() returns in 2.3s and writes nothing.
#
# 2. The turn-detector weights (461 MB) are therefore the ONLY thing
#    `download-files` fetches — and this agent does not load them
#    (turn_handling.py pins turn_detection="vad"). Baking them by default would
#    be 461 MB of image for a code path that never executes. The bake is behind
#    a build ARG, off by default, for whoever makes the swap the README describes.
#
# 3. If you do bake them, the variable is HF_HOME — not XDG_DATA_HOME. The first
#    version of this file used XDG_DATA_HOME; verified empirically that the target
#    stayed at 4.0K while 461 MB went to ~/.cache/huggingface. That produces an
#    image that LOOKS pre-baked and re-downloads on every cold start — the exact
#    failure the bake exists to prevent.
#
# 4. The entrypoint is `start`, not `dev`. `dev` binds the health server to a
#    random port (:0) — the 5-digit port in local logs. `start` binds 8081, which
#    is what a platform health probe needs to be pointed at. `dev` also enables
#    debug logging, which in this project is where raw caller transcripts appear.
#
# This worker takes NO inbound traffic. It dials out to LiveKit and holds the
# connection, so the deployment has no ingress and cannot scale to zero.
# See infra/README.md.

# ---- build stage -----------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# build-essential is needed by a few wheels that have no aarch64/slim prebuilt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Lock file, not requirements.txt: the point of the lock is a byte-identical
# transitive set, and an image is exactly where that matters.
COPY requirements.lock.txt .
RUN pip install -r requirements.lock.txt

# Optional turn-detector weight bake. Off by default — see note 2 above.
# Turn it on with:  az acr build --build-arg BAKE_TURN_DETECTOR=1 ...
# NOTE: `python src/agent.py download-files` is DEPRECATED in livekit-agents 1.6.7;
# it warns and points at the module form. Use the module form.
ARG BAKE_TURN_DETECTOR=0
ENV HF_HOME=/opt/hf
RUN mkdir -p /opt/hf \
 && if [ "$BAKE_TURN_DETECTOR" = "1" ]; then \
        python -m livekit.agents download-files; \
    fi

# ---- runtime stage ---------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    HF_HOME=/opt/hf

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /opt/hf /opt/hf

WORKDIR /app
COPY src/ ./src/

# Non-root. The agent needs no write access to anything it ships with.
RUN useradd --create-home --shell /usr/sbin/nologin agent \
 && chown -R agent:agent /app /opt/hf
USER agent

# Health check endpoint. 8081 is the livekit-agents production default
# (worker.py: prod_default=8081); dev mode uses :0 and picks a random port.
# EXPOSE is documentation only here — this container app has no ingress.
EXPOSE 8081

CMD ["python", "src/agent.py", "start"]
