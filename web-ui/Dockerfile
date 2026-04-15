FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

ENV MQTT_HOST=mosquitto \
    MQTT_PORT=1883 \
    INFLUXDB_URL=http://influxdb:8086 \
    INFLUXDB_ORG=labwifimon \
    INFLUXDB_BUCKET=labwifimon \
    HOST=0.0.0.0 \
    PORT=5000

CMD ["python", "app.py"]
