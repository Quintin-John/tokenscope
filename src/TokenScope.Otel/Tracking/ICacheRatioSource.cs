namespace TokenScope.Otel.Tracking;

/// <summary>
/// Per-session running totals of cache-read vs fresh (non-cached) input
/// tokens. Snapshot is consumed by the <c>tokenscope.cache.hit_ratio</c>
/// observable gauge to emit one ratio per session at scrape time.
/// </summary>
public interface ICacheRatioSource
{
    /// <summary>
    /// Record cache and input tokens for a session. <paramref name="project"/>
    /// and <paramref name="projectName"/> are recorded alongside so the
    /// observable-gauge snapshot can emit them as Prometheus labels.
    /// </summary>
    void Record(
        string sessionId,
        string project,
        string projectName,
        long cacheReadTokens,
        long inputTokens);

    /// <summary>
    /// Yields one entry per session whose denominator
    /// (cache_read + input) is non-zero. Sessions with no signal are
    /// intentionally skipped — an undefined ratio is not emitted.
    /// </summary>
    IEnumerable<CacheRatioSnapshot> Snapshot();
}

public readonly record struct CacheRatioSnapshot(
    string SessionId,
    string Project,
    string ProjectName,
    double Ratio);
