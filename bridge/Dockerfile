# Bridge Dockerfile
FROM node:20-alpine

WORKDIR /app

# Install dependencies first for caching layers
COPY package*.json ./
RUN npm install

# Copy source code
COPY . .

# Build TypeScript
RUN npm run build

# Default env vars
ENV PORT=3000

EXPOSE 3000

CMD ["npm", "start"]
