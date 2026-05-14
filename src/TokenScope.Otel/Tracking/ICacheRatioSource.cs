namespace TokenScope.Otel.Tracking;

/// <summary>
/// Per-session running totals of cache-read vs fresh (non-cached) input
/// tokens. Snapshot is consumed by the <c>tokenscope.cache.hit_ratio</c>
/// observable gauge to emit one ratio per session at scrape time.
/// </summary>
public interface ICacheRatioSource
{
    void Record(string sessionId, long cacheReadTokens, long inputTokens);

    /// <summary>
    /// Yields one (session_id, ratio) tuple per session whose denominator
    /// (cache_read + input) is non-zero. Sessions with no signal are
    /// intentionally skipped — an undefined ratio is not emitted.
    /// </summary>
    IEnumerable<(string SessionId, double Ratio)> Snapshot();
}
