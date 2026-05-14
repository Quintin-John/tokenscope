using System.Collections.Concurrent;

namespace TokenScope.Otel.Tracking;

public sealed class CacheRatioTracker : ICacheRatioSource
{
    private readonly ConcurrentDictionary<string, Counters> _state
        = new(StringComparer.Ordinal);

    public void Record(string sessionId, long cacheReadTokens, long inputTokens)
    {
        ArgumentException.ThrowIfNullOrEmpty(sessionId);

        if (cacheReadTokens < 0 || inputTokens < 0)
        {
            throw new ArgumentOutOfRangeException(
                cacheReadTokens < 0 ? nameof(cacheReadTokens) : nameof(inputTokens),
                "Token counts must be non-negative.");
        }

        _state.AddOrUpdate(
            sessionId,
            new Counters(cacheReadTokens, inputTokens),
            (_, existing) => new Counters(
                existing.CacheRead + cacheReadTokens,
                existing.Input + inputTokens));
    }

    public IEnumerable<(string SessionId, double Ratio)> Snapshot()
    {
        foreach (var (sessionId, counters) in _state)
        {
            var denominator = counters.CacheRead + counters.Input;
            if (denominator == 0)
            {
                continue;
            }
            yield return (sessionId, (double)counters.CacheRead / denominator);
        }
    }

    private readonly record struct Counters(long CacheRead, long Input);
}
