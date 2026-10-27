# Build MinIO from the official release source

Date: 2026-10-27

The configured Docker Hub images for MinIO and its client failed to pull. Manifest
checks for the same official Quay release tags also failed. Reusing those image
references prevents a clean checkout from starting the storage service.

Keep the existing S3-compatible storage architecture, bucket names, credentials,
and ports. Build local images from the official MinIO and mc release tags with a
multi-stage Dockerfile. The server tag is RELEASE.2025-10-15T17-29-55Z and the
client tag is RELEASE.2025-08-13T08-35-41Z; both were checked against the official
GitHub latest-release endpoints on the date above. The MinIO server release
explicitly instructs container users to build from source.

Source: https://github.com/minio/minio/releases/tag/RELEASE.2025-10-15T17-29-55Z

First startup now compiles Go binaries and takes longer. Subsequent builds use
Docker's cache. Bucket initialization fails on real errors instead of masking
authentication or connectivity failures as success.
