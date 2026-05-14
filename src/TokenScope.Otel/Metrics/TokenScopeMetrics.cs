using System.Diagnostics;
using System.Diagnostics.Metrics;
using TokenScope.Core.Domain;
using TokenScope.Otel.Tracking;

namespace TokenScope.Otel.Metrics;

/// <summary>
/// All eight tokenscope metrics, grouped behind one disposable owner.
///
/// <list type="bullet">
///   <item>Six synchronous counters: <c>tokens.{input,output,cache_read,cache_write}</c>,
///         <c>cost.usd</c>, <c>requests.total</c>.</item>
///   <item>Two observable gauges: <c>cache.hit_ratio</c> (per session) and
///         <c>sessions.active</c> (singleton).</item>
/// </list>
///
/// Emission rules:
/// <list type="bullet">
///   <item>Synchronous instruments only emit on positive measurements
///         (no zero-pollution series in Prometheus).</item>
///   <item><see cref="RecordRequest"/> is the only entry point for
///         counter increments; it also updates the trackers feeding
///         the observable gauges.</item>
///   <item>Callers must dedupe by <c>(SessionId, RequestId)</c> before
///         calling <see cref="RecordRequest"/>. tokenscope counts unique
///         API requests, not log events.</item>
/// </list>
/// </summary>
public sealed class TokenScopeMetrics : IDisposable
{
    public const string MeterName = "tokenscope";
    public const string MeterVersion = "0.1.0";

    private readonly Meter _meter;
    private readonly ISessionActivityTracker _activity;
    private readonly ICacheRatioSource _cacheRatios;
    private readonly TimeProvider _clock;
    private readonly TimeSpan _activeWindow;

    private readonly Counter<long> _tokensInput;
    private readonly Counter<long> _tokensOutput;
    private readonly Counter<long> _tokensCacheRead;
    private readonly Counter<long> _tokensCacheWrite;
    private readonly Counter<double> _costUsd;
    private readonly Counter<long> _requestsTotal;

    // ObservableGauges are kept as fields so the meter retains the registration.
#pragma warning disable IDE0052 // Remove unread private members
    private readonly ObservableGauge<double> _cacheHitRatio;
    private readonly ObservableGauge<long> _sessionsActive;
#pragma warning restore IDE0052

    public TokenScopeMetrics(
        ISessionActivityTracker activity,
        ICacheRatioSource cacheRatios,
        TimeProvider? clock = null,
        TimeSpan? activeWindow = null)
    {
        ArgumentNullException.ThrowIfNull(activity);
        ArgumentNullException.ThrowIfNull(cacheRatios);

        _activity = activity;
        _cacheRatios = cacheRatios;
        _clock = clock ?? TimeProvider.System;
        _activeWindow = activeWindow ?? TimeSpan.FromMinutes(10);

        _meter = new Meter(MeterName, MeterVersion);

        _tokensInput = _meter.CreateCounter<long>(
            "tokenscope.tokens.input",
            unit: "{tokens}",
            description: "Non-cached input tokens billed at full input rate.");

        _tokensOutput = _meter.CreateCounter<long>(
            "tokenscope.tokens.output",
            unit: "{tokens}",
            description: "Output tokens generated.");

        _tokensCacheRead = _meter.CreateCounter<long>(
            "tokenscope.tokens.cache_read",
            unit: "{tokens}",
            description: "Cache hit tokens. No TTL attribute — reads do not carry TTL.");

        _tokensCacheWrite = _meter.CreateCounter<long>(
            "tokenscope.tokens.cache_write",
            unit: "{tokens}",
            description: "Cache write tokens. ttl attribute distinguishes 5m and 1h TTLs.");

        // Annotation unit "{usd}" (curly braces are the OTEL convention for
        // annotated/non-UCUM units) prevents the Prometheus exporter from
        // appending an extra _USD to the metric name. Result in Prometheus:
        // tokenscope_cost_usd_total — not tokenscope_cost_usd_USD_total.
        _costUsd = _meter.CreateCounter<double>(
            "tokenscope.cost.usd",
            unit: "{usd}",
            description: "USD cost per component. component ∈ {input, output, cache_read, cache_write_5m, cache_write_1h}.");

        _requestsTotal = _meter.CreateCounter<long>(
            "tokenscope.requests.total",
            unit: "{requests}",
            description: "Unique API requests. Callers dedupe by (sessionId, requestId) before recording.");

        _cacheHitRatio = _meter.CreateObservableGauge<double>(
            "tokenscope.cache.hit_ratio",
            ObserveCacheHitRatios,
            unit: "{ratio}",
            description: "Per-session rolling cache hit ratio in [0.0, 1.0]. Sessions with zero cache_read + zero input are omitted.");

        _sessionsActive = _meter.CreateObservableGauge<long>(
            "tokenscope.sessions.active",
            ObserveActiveSessions,
            unit: "{sessions}",
            description: "Count of sessions whose most recent event is within the active-session window (default 10 min).");
    }

    /// <summary>
    /// Record one (already-deduplicated) API request. Updates token counters,
    /// cost counter, request counter, and the trackers feeding the
    /// observable gauges.
    /// </summary>
    public void RecordRequest(string model, string sessionId, TokenUsage usage, Cost cost, DateTimeOffset at)
    {
        ArgumentException.ThrowIfNullOrEmpty(model);
        ArgumentException.ThrowIfNullOrEmpty(sessionId);
        ArgumentNullException.ThrowIfNull(usage);
        ArgumentNullException.ThrowIfNull(cost);

        var baseTags = new TagList
        {
            { "model", model },
            { "session_id", sessionId },
        };

        if (usage.Input > 0)
        {
            _tokensInput.Add(usage.Input, baseTags);
        }
        if (usage.Output > 0)
        {
            _tokensOutput.Add(usage.Output, baseTags);
        }
        if (usage.CacheRead > 0)
        {
            _tokensCacheRead.Add(usage.CacheRead, baseTags);
        }
        if (usage.CacheWrite5m > 0)
        {
            _tokensCacheWrite.Add(usage.CacheWrite5m, TagsWith(baseTags, "ttl", "5m"));
        }
        if (usage.CacheWrite1h > 0)
        {
            _tokensCacheWrite.Add(usage.CacheWrite1h, TagsWith(baseTags, "ttl", "1h"));
        }

        AddCostComponent(cost.Input, baseTags, "input");
        AddCostComponent(cost.Output, baseTags, "output");
        AddCostComponent(cost.CacheRead, baseTags, "cache_read");
        AddCostComponent(cost.CacheWrite5m, baseTags, "cache_write_5m");
        AddCostComponent(cost.CacheWrite1h, baseTags, "cache_write_1h");

        _requestsTotal.Add(1, baseTags);

        _activity.MarkActive(sessionId, at);
        _cacheRatios.Record(sessionId, usage.CacheRead, usage.Input);
    }

    public void Dispose() => _meter.Dispose();

    private void AddCostComponent(decimal value, TagList baseTags, string component)
    {
        if (value <= 0m)
        {
            return;
        }
        _costUsd.Add((double)value, TagsWith(baseTags, "component", component));
    }

    private static TagList TagsWith(TagList baseTags, string key, string value)
    {
        var copy = baseTags;
        copy.Add(key, value);
        return copy;
    }

    private IEnumerable<Measurement<double>> ObserveCacheHitRatios()
    {
        foreach (var (sessionId, ratio) in _cacheRatios.Snapshot())
        {
            yield return new Measurement<double>(ratio, new KeyValuePair<string, object?>("session_id", sessionId));
        }
    }

    private IEnumerable<Measurement<long>> ObserveActiveSessions()
    {
        var count = _activity.CountActive(_clock.GetUtcNow(), _activeWindow);
        yield return new Measurement<long>(count);
    }
}
