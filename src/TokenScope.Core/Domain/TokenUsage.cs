namespace TokenScope.Core.Domain;

public sealed record TokenUsage(
    long Input,
    long Output,
    long CacheRead,
    long CacheWrite5m,
    long CacheWrite1h)
{
    public static TokenUsage Empty { get; } = new(0, 0, 0, 0, 0);

    public long Total => Input + Output + CacheRead + CacheWrite5m + CacheWrite1h;
}
