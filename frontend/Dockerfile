# Stage 1: Development
FROM node:20-alpine AS development
WORKDIR /app
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ .

# Stage 2: Build
FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci --only=production
COPY frontend/ .
RUN npm run build

# Stage 3: Production with Nginx
FROM nginx:alpine AS production
WORKDIR /usr/share/nginx/html
COPY --from=build /app/dist /usr/share/nginx/html
COPY docker/frontend-nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
