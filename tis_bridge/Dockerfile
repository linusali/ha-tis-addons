FROM python:3.11-alpine

RUN pip install --no-cache-dir paho-mqtt

COPY tis-mqtt-bridge.py /app/tis-mqtt-bridge.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
