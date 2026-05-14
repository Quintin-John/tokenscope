namespace TokenScope.Core.Domain;

public sealed record ModelRate(
    decimal InputPerMTok,
    decimal OutputPerMTok,
    decimal CacheReadPerMTok,
    decimal CacheWrite5mPerMTok,
    decimal CacheWrite1hPerMTok);
