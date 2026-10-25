FROM golang:1.24-alpine AS build
WORKDIR /src
COPY go.mod main.go index.html ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/dashboard .

FROM scratch
COPY --from=build /out/dashboard /dashboard
ENTRYPOINT ["/dashboard"]
