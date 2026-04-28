FROM python:3.12-slim

WORKDIR /lab_1

RUN pip install --no-cache-dir fastapi uvicorn

COPY . /lab_1

CMD ["python", "-m", "lab_1.main"]