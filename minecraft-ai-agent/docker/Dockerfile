FROM python:3.12-slim

WORKDIR /app

# psycopg2-binary needs libpq at runtime even though it ships its own libs
# for the build; keeping this minimal rather than installing a full
# build toolchain since psycopg2-binary is a prebuilt wheel.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
