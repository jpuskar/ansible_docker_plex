FROM python:3.12-slim

WORKDIR /app

# No pip packages needed — pure stdlib
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

COPY godaddy_ddns.py /app/godaddy_ddns.py
RUN chmod +x /app/godaddy_ddns.py \
    && sed -i 's/\r$//' /app/godaddy_ddns.py

# Run as non-root
RUN groupadd -r ddns && useradd -r -g ddns ddns
USER ddns

ENTRYPOINT ["tini", "--", "python3", "/app/godaddy_ddns.py"]
