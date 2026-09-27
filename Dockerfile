FROM python:3.12-slim
WORKDIR /app
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1
# Package source: override with --build-arg PIP_INDEX_URL=<your Artifactory PyPI URL>
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements.txt .
RUN pip install --no-cache-dir --index-url "$PIP_INDEX_URL" -r requirements.txt
COPY src ./src
COPY app ./app
COPY data ./data
COPY .streamlit ./.streamlit
RUN useradd -m meridian && chown -R meridian /app
USER meridian
EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["sh", "-c", "python -m meridian ingest && streamlit run app/streamlit_app.py --server.address 0.0.0.0 --server.port 8501"]
