# Standalone relay utility. Normal installations use the main Dockerfile server target.
FROM rust:1.92.0-bookworm@sha256:e90e846de4124376164ddfbaab4b0774c7bdeef5e738866295e5a90a34a307a2 AS build
WORKDIR /build
COPY LICENSE /LICENSE
COPY relay-rs/Cargo.toml relay-rs/Cargo.lock relay-rs/build.rs ./
COPY relay-rs/src ./src
RUN cargo build --release --locked

FROM debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
COPY --from=build /build/target/release/tico-screen-relay /usr/local/bin/tico-screen-relay
COPY LICENSE NOTICE /usr/share/doc/tico/
USER 10001:10001
ENV TICO_SCREEN_RELAY_BIND=0.0.0.0:8766
EXPOSE 8766
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s CMD ["tico-screen-relay", "--healthcheck"]
ENTRYPOINT ["tico-screen-relay"]
