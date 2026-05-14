using System.Collections.Immutable;

namespace TokenScope.Core.Domain;

public sealed record Session(
    string Id,
    DateTimeOffset StartedAt,
    ImmutableArray<Request> Requests);
