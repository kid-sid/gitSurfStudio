# Stage 1: Build stage
FROM node:20-slim AS build

WORKDIR /app

# Install dependencies
COPY package*.json ./
RUN npm install

# Copy source code
COPY . .

# Build the application
# We can pass ENGINE_URL as a build arg if needed, but we'll 
# handle it in api.js to be more flexible (e.g. using relative path)
RUN npm run build

# Stage 2: Production stage
FROM nginx:stable-alpine

# Copy the built application from the build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Use our custom config (SPA fallback + cache headers)
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Expose port 80
EXPOSE 80

# Start Nginx
CMD ["nginx", "-g", "daemon off;"]
