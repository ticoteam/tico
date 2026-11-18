# Standalone relay utility. Normal installations use the main Dockerfile server target.
FROM rust:1.99.1-slim-trixie@sha256:f6c4a35d21dbb3c89d7a8d056c193e94fc09037f2b58dd858b09c39a47dd02d7 AS build
WORKDIR /build
COPY LICENSE /LICENSE
COPY relay-rs/Cargo.toml relay-rs/Cargo.lock relay-rs/build.rs ./
COPY relay-rs/src ./src
RUN cargo build --release --locked

FROM debian:trixie-slim@sha256:a29215f6a35e51e22adffa17f89e9d2ef06214e64a2bad10d765c46aea49f11f
COPY --from=build /build/target/release/tico-screen-relay /usr/local/bin/tico-screen-relay
COPY LICENSE NOTICE /usr/share/doc/tico/
USER 10001:10001
ENV TICO_SCREEN_RELAY_BIND=0.0.0.0:8766
EXPOSE 8766
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s CMD ["tico-screen-relay", "--healthcheck"]
ENTRYPOINT ["tico-screen-relay"]
