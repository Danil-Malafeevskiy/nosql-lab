FROM python:3.12-slim

WORKDIR /lab_1

RUN pip install --no-cache-dir fastapi uvicorn redis pymongo bcrypt cassandra-driver neo4j

COPY . /lab_1

CMD ["python", "-m", "lab_1.main"]
