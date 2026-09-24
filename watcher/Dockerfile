FROM python:3.11-slim

# poppler-utils fuer pdf2image (Thumbnail-Erzeugung)
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY watcher.py pdf_parser.py ./

CMD ["python", "-u", "watcher.py"]