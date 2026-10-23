FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py cloud.py command_specs.py telegram_setup.py game_catalog.json .
COPY static ./static
ENV PORT=8000 DATA_DIR=/app/data
CMD ["python", "main.py"]
