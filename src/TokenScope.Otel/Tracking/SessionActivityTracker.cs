using System.Collections.Concurrent;

namespace TokenScope.Otel.Tracking;

public sealed class SessionActivityTracker : ISessionActivityTracker
{
    private readonly ConcurrentDictionary<string, DateTimeOffset> _lastSeen
        = new(StringComparer.Ordinal);

    public void MarkActive(string sessionId, DateTimeOffset at)
    {
        ArgumentException.ThrowIfNullOrEmpty(sessionId);
        _lastSeen.AddOrUpdate(sessionId, at, (_, existing) => existing > at ? existing : at);
    }

    public int CountActive(DateTimeOffset now, TimeSpan window)
    {
        if (window <= TimeSpan.Zero)
        {
            throw new ArgumentOutOfRangeException(nameof(window), "Window must be positive.");
        }

        var threshold = now - window;
        var count = 0;
        foreach (var (_, at) in _lastSeen)
        {
            if (at >= threshold)
            {
                count++;
            }
        }
        return count;
    }
}
