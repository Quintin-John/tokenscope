namespace TokenScope.Core.Domain;

public sealed record Cost(
    decimal Input,
    decimal Output,
    decimal CacheRead,
    decimal CacheWrite5m,
    decimal CacheWrite1h)
{
    public decimal Total => Input + Output + CacheRead + CacheWrite5m + CacheWrite1h;

    public static Cost Zero { get; } = new(0m, 0m, 0m, 0m, 0m);
}
