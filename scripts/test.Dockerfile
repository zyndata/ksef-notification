# The image `python scripts/test.py` runs the suite in on Windows, where Home Assistant
# does not import. The build context is only requirements-dev.txt; the working tree is
# mounted read-only at run time. Changing this file or the requirements rebuilds it.
FROM ghcr.io/astral-sh/uv:0.12.5-python3.14-trixie-slim

COPY requirements-dev.txt /tmp/requirements-dev.txt
RUN uv pip install --system --no-cache -r /tmp/requirements-dev.txt

ENV PYTHONDONTWRITEBYTECODE=1
