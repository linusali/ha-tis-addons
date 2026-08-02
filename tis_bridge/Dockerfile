FROM python:3.11-alpine

RUN pip install --no-cache-dir paho-mqtt
# --no-deps: we only use BytesHelper.checkCRC and crc.packCRC, neither of
# which need the homeassistant package that TISControlProtocol otherwise
# pulls in as a dependency (large and unnecessary for our use).
RUN pip install --no-cache-dir --no-deps TISControlProtocol

COPY tis-mqtt-bridge.py /app/tis-mqtt-bridge.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
