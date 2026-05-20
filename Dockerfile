FROM python:3.11-slim

# uv handles deps with the pyproject.toml + uv.lock already in the repo.
# Pinning a release tag keeps reproducible builds even when upstream uv moves.
COPY --from=ghcr.io/astral-sh/uv:0.5.4 /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency manifests + the local package source. uv sync builds the
# local 'argo' package via hatchling, which needs pyproject.toml, README.md
# (referenced in [project.readme]), and the package directory itself.
COPY pyproject.toml uv.lock README.md ./
COPY argo ./argo

# --frozen: refuse to update the lockfile inside the image build.
# --no-dev: pytest etc. don't ship in the runtime image.
RUN uv sync --frozen --no-dev

# Static UI bundle (served by uvicorn at /ui).
COPY static ./static

# uv's venv binaries (uvicorn) live here.
ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "argo.main:app", "--host", "0.0.0.0", "--port", "8000"]
