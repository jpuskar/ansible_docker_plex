FROM python:3.12-slim

WORKDIR /app

COPY godaddy_ddns.py /app/godaddy_ddns.py

RUN chmod +x /app/godaddy_ddns.py

# No pip packages needed — pure stdlib
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

# Run as non-root
RUN groupadd -r ddns && useradd -r -g ddns ddns
USER ddns

ENTRYPOINT ["tini", "--", "python3", "/app/godaddy_ddns.py"]
