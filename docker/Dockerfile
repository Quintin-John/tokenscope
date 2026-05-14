# Multi-stage build for the tokenscope collector.
#
# Stage 1 (build): compiles + publishes against the .NET 8 SDK.
# Stage 2 (runtime): copies the published output onto the slim .NET 8
#                    runtime image and runs as a fixed non-root UID.
#
# Image pins verified 2026-05-14 against mcr.microsoft.com. Stay on the
# 8.0.x line — do not move to .NET 9 or 10 without a deliberate decision.

# ---------- build stage ----------
FROM mcr.microsoft.com/dotnet/sdk:8.0.421-bookworm-slim AS build
WORKDIR /src

# Copy only what's needed for restore first, so the package cache layer
# is reused across builds when source changes but dependencies don't.
# .editorconfig is required by the analyzer suppressions (notably CA1848).
COPY global.json Directory.Build.props Directory.Packages.props .editorconfig ./
COPY src/TokenScope.Core/TokenScope.Core.csproj          src/TokenScope.Core/
COPY src/TokenScope.Otel/TokenScope.Otel.csproj          src/TokenScope.Otel/
COPY src/TokenScope.Collector/TokenScope.Collector.csproj src/TokenScope.Collector/
RUN dotnet restore src/TokenScope.Collector/TokenScope.Collector.csproj

# Copy the rest of the source. Test projects are deliberately NOT copied
# so the build context stays small and the publish output is test-free.
COPY src/TokenScope.Core/      src/TokenScope.Core/
COPY src/TokenScope.Otel/      src/TokenScope.Otel/
COPY src/TokenScope.Collector/ src/TokenScope.Collector/

RUN dotnet publish src/TokenScope.Collector/TokenScope.Collector.csproj \
    --configuration Release \
    --output /app

# ---------- runtime stage ----------
FROM mcr.microsoft.com/dotnet/runtime:8.0.27-bookworm-slim AS runtime

# Fixed non-root user. UID 10001 is well above the typical host-user
# range, so the container's identity won't collide with a real user's
# UID on the host filesystem. World-readable session logs are the
# documented assumption — see docs/architecture.md and
# docs/troubleshooting.md for the rationale and the chmod fix.
RUN groupadd --system --gid 10001 tokenscope \
 && useradd  --system --gid 10001 --uid 10001 --shell /sbin/nologin tokenscope \
 && mkdir -p /data/claude-logs /data/state /data/config /app \
 && chown -R tokenscope:tokenscope /data /app

WORKDIR /app

# Bring over the publish output. Note: published binaries from build
# stage only — no test assemblies are included.
COPY --from=build --chown=tokenscope:tokenscope /app .

# Ship the example YAML inside the image as a baked-in fallback. In
# normal compose-driven runs, TOKENSCOPE_* env vars override every
# path field, so the file's path values are never used; only the
# non-path keys (subscription_mode, logging, etc.) come from YAML.
COPY --chown=tokenscope:tokenscope config/tokenscope.example.yaml /app/tokenscope.yaml

USER tokenscope

# DOTNET_RUNNING_IN_CONTAINER is set automatically by Microsoft's
# runtime image — redundant to set it explicitly. Kept here as a
# documented marker; remove if you want a minimal env.
ENV DOTNET_NOLOGO=1 \
    DOTNET_CLI_TELEMETRY_OPTOUT=true

# No HEALTHCHECK in Phase 6: the collector has no HTTP endpoint, and a
# file-touch-based liveness probe is a Phase 9 polish item. The
# orchestration layer (docker-compose depends_on) handles ordering.

ENTRYPOINT ["dotnet", "/app/TokenScope.Collector.dll"]
