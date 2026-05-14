namespace TokenScope.Core.Domain;

public sealed record Request(
    string Id,
    string ModelId,
    DateTimeOffset Timestamp,
    TokenUsage Usage);
