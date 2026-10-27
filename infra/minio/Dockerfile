FROM golang:1.27.3-alpine AS source
RUN apk add --no-cache git ca-certificates
ENV CGO_ENABLED=0

FROM source AS server-build
RUN git clone --depth 1 --branch RELEASE.2025-10-15T17-29-55Z https://github.com/minio/minio.git /src
WORKDIR /src
RUN go build -trimpath -ldflags="-s -w -X github.com/minio/minio/cmd.Version=2025-10-15T17:29:55Z -X github.com/minio/minio/cmd.ReleaseTag=RELEASE.2025-10-15T17-29-55Z" -o /out/minio .

FROM source AS client-build
RUN git clone --depth 1 --branch RELEASE.2025-08-13T08-35-41Z https://github.com/minio/mc.git /src
WORKDIR /src
RUN go build -trimpath -ldflags="-s -w" -o /out/mc .

FROM alpine:3 AS client
RUN apk add --no-cache ca-certificates
COPY --from=client-build /out/mc /usr/local/bin/mc
ENTRYPOINT ["mc"]

FROM client AS server
COPY --from=server-build /out/minio /usr/local/bin/minio
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
