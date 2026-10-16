FROM python:3.14-slim

COPY ci_doctor /src/ci_doctor
COPY pyproject.toml README.md LICENSE /src/
RUN pip install --no-cache-dir --only-binary :all: /src && rm -rf /src

WORKDIR /app

ENTRYPOINT ["ci-doctor"]
