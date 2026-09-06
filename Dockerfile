FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY repeater_presence.py .
ENV PRESENCE_CONFIG=/config/config.json
CMD ["python", "repeater_presence.py"]
