# pdftomusicxml: convert sheet music between PDF, MuseScore, MusicXML and MIDI behind a
# small Flask app, with a practice-tempo option and a Violy Creator Studio export.
#
# Stage 1 builds Audiveris (AGPL-3.0) from its release tag: it reads PDFs and images.
# Stage 2 installs webmscore (MuseScore compiled to WebAssembly, GPL-3.0): it reads
#   .mscz and writes .mscz, MusicXML, MIDI and PDF.
# Stage 3 is the runtime: JRE + Tesseract + Python (Flask) + Node.

# Audiveris 5.10+ declares Java 25 as its minimum; a 21 JDK fails compileJava.
FROM eclipse-temurin:25-jdk-noble AS audiveris-build
ARG AUDIVERIS_VERSION=5.11.0
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
LABEL org.opencontainers.image.source="https://github.com/ezraulo/pdftomusicxml" \
      org.opencontainers.image.description="Sheet music converter: PDF, MuseScore, MusicXML, MIDI; practice tempo; Violy export" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
RUN apt-get update && apt-get install -y --no-install-recommends \
      fontconfig fonts-dejavu-core python3 python3-venv curl \
    && rm -rf /var/lib/apt/lists/*

# Text recognition (titles, tempo words, pizz./arco...). Audiveris bundles Tesseract but
# runs its legacy engine, which needs the full language data of the tessdata 4.1.0
# release (the version Audiveris names); Ubuntu's tesseract-ocr-eng ships only the
# LSTM model, and Audiveris then reads no text at all.
ADD --checksum=sha256:daa0c97d651c19fba3b25e81317cd697e9908c8208090c94c3905381c23fc047 \
    https://raw.githubusercontent.com/tesseract-ocr/tessdata/4.1.0/eng.traineddata \
    /opt/tessdata/eng.traineddata
RUN chmod -R a+rX /opt/tessdata

COPY --from=audiveris-build /build/app/build/install/app /opt/audiveris
COPY --from=mscz-build /usr/local/bin/node /usr/local/bin/node
COPY --from=mscz-build /opt/mscz /opt/mscz
COPY mscz/convert.cjs /opt/mscz/

WORKDIR /app
COPY requirements.txt ./
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt
COPY app.py omr.py violy_export.py score_tools.py ./
COPY static ./static

# sun.java2d.uiScale: without it Audiveris 5.11 probes the GTK desktop library for
# screen scaling at start-up, and dies in this image, which has no GTK.
ENV PATH=/opt/venv/bin:$PATH \
    AUDIVERIS_CMD=/opt/audiveris/bin/Audiveris \
    WEBMSCORE_CMD="node /opt/mscz/convert.cjs" \
    TESSDATA_PREFIX=/opt/tessdata \
    JAVA_OPTS="-Djava.awt.headless=true -Dsun.java2d.uiScale=1 -Xmx3g" \
    PORT=8080
EXPOSE 8080

# Not root: the app only needs /app (read) and its temp directories.
RUN useradd --create-home --uid 10001 app
USER app
HEALTHCHECK --interval=30s --timeout=5s CMD curl -sf http://localhost:${PORT}/healthz || exit 1
# One worker, one thread: OMR is memory-hungry, so a container converts one score at a time
# (matches Cloud Run concurrency 1). The timeout sits just above the OMR time budget.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 1 --threads 1 --timeout 900 app:app"]
