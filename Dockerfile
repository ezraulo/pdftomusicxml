# pdftomusicxml: Audiveris (PDF/image -> MusicXML) behind a small Flask app,
# plus a Violy Creator Studio export (one MusicXML + one .mscz per part).
#
# Stage 1 builds Audiveris (AGPL-3.0) from its release tag.
# Stage 2 installs webmscore (MuseScore compiled to WebAssembly, GPL-3.0),
#   used only to write the optional .mscz files for the Violy export.
# Stage 3 is the runtime: JRE + Tesseract + Python (Flask) + Node.

# Audiveris 5.10.x declares Java 25 as its minimum; a 21 JDK fails compileJava.
FROM eclipse-temurin:25-jdk-noble AS audiveris-build
ARG AUDIVERIS_VERSION=5.10.2
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${AUDIVERIS_VERSION} \
      https://github.com/Audiveris/audiveris.git /build
WORKDIR /build
RUN ./gradlew --no-daemon :app:installDist

FROM node:22-bookworm-slim AS mscz-build
WORKDIR /opt/mscz
COPY mscz/package.json mscz/package-lock.json ./
RUN npm ci --omit=dev

FROM eclipse-temurin:25-jre-noble AS final
RUN apt-get update && apt-get install -y --no-install-recommends \
      tesseract-ocr tesseract-ocr-eng fontconfig fonts-dejavu-core \
      python3 python3-venv curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=audiveris-build /build/app/build/install/app /opt/audiveris
COPY --from=mscz-build /usr/local/bin/node /usr/local/bin/node
COPY --from=mscz-build /opt/mscz /opt/mscz
COPY mscz/to_mscz.cjs /opt/mscz/

WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY app.py omr.py violy_export.py mscz_export.py ./
COPY static ./static

ENV PATH=/opt/venv/bin:$PATH \
    AUDIVERIS_CMD=/opt/audiveris/bin/Audiveris \
    MSCZ_CMD="node /opt/mscz/to_mscz.cjs" \
    TESSDATA_PREFIX=/usr/share/tesseract-ocr/5/tessdata \
    JAVA_OPTS="-Djava.awt.headless=true -Xmx3g" \
    PORT=8080
EXPOSE 8080

# Not root: the app only needs /app (read) and its temp directories.
RUN useradd --create-home --uid 10001 app
USER app
HEALTHCHECK --interval=30s --timeout=5s CMD curl -sf http://localhost:${PORT}/healthz || exit 1
# One worker, one thread: OMR is memory-hungry, so a container converts one score at a time
# (matches Cloud Run concurrency 1). The timeout sits just above the OMR time budget.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 1 --timeout 900 app:app"]
