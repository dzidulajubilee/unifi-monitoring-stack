FROM python:3.12-slim
# pango/harfbuzz are what WeasyPrint uses to lay out text for the PDF
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY templates ./templates
COPY fonts ./fonts
COPY report.py portal.py ./
ENV PYTHONUNBUFFERED=1 \
    XDG_CACHE_HOME=/tmp/cache
EXPOSE 8088
# web portal + report worker + scheduler in one process
CMD ["python", "portal.py"]
