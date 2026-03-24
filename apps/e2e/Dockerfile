FROM mcr.microsoft.com/playwright:v1.49.0-noble

WORKDIR /app

# Install dependencies
COPY package.json ./
RUN npm install

# Copy project files
COPY playwright.config.ts ./
COPY runner.mjs ./

# Create dirs for tests and results
RUN mkdir -p tests results

# Default command: run the suite runner
ENTRYPOINT ["node", "runner.mjs"]
