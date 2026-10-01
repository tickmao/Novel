FROM eclipse-temurin:21-jdk-jammy AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /engine
RUN git init && git remote add origin https://github.com/lukelzlz/legado-server.git \
    && git fetch --depth=1 origin 3cb7acb2a2892e71184c1dd12c8e8d7b5a60fbb7 \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = 3cb7acb2a2892e71184c1dd12c8e8d7b5a60fbb7
COPY runtime/NovelProbe.kt server/src/main/kotlin/io/legado/server/NovelProbe.kt
RUN ./gradlew :server:installDist -x :server:buildWeb --no-daemon

FROM eclipse-temurin:17-jre-jammy
WORKDIR /engine
COPY --from=build /engine/server/build/install/legado-server/lib/ ./lib/
COPY --from=build /engine/LICENSE ./UPSTREAM-LICENSE
USER 65534:65534
ENTRYPOINT ["java", "-Xmx256m", "-cp", "/engine/lib/*", "io.legado.server.NovelProbeKt"]
