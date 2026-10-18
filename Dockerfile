FROM python:3.13-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Europe/Paris
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY clubbot/ clubbot/
# Configuration par défaut, à remplacer par un montage de config.yaml
COPY config.example.yaml config.yaml

# docker build --target test . : lance les tests hors ligne pendant le build
FROM base AS test
RUN pip install --no-cache-dir pytest
COPY pytest.ini .
COPY tests/ tests/
RUN pytest -q

FROM base AS runtime
RUN useradd --system --uid 10001 clubbot && mkdir state && chown clubbot state
USER clubbot
# Verrou du coupe-circuit : doit survivre aux redémarrages du conteneur
VOLUME /app/state
ENTRYPOINT ["python", "-m", "clubbot"]
CMD ["--every-day", "07:50"]
