FROM python:3.9-slim

RUN pip install kopf kubernetes

COPY vpa_operator.py /vpa_operator.py

CMD ["kopf", "run", "/vpa_operator.py"]
