# syntax=docker/dockerfile:1

FROM node:16-alpine AS base
WORKDIR /app

FROM base AS build
COPY package*.json ./
RUN npm install

FROM base AS final
COPY --from=build /app/node_modules ./node_modules
COPY . .
RUN npm run build

USER node
ENV PORT=8080
EXPOSE 8080

CMD ["npm", "start"]
