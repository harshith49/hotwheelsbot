FROM python:3.12-slim

# Playwright system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl gnupg2 \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxkbcommon0 \
    libatspi2.0-0 libxcomposite1 libxdamage1 libxfixes3 \
    libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libx11-xcb1 fonts-noto-color-emoji fonts-freefont-ttf fonts-unifont \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps + Playwright browser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install chromium

# Copy bot
COPY bot.py .

# Run
CMD ["python", "-u", "bot.py"]
