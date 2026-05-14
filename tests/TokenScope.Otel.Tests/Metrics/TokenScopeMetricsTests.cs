using AwesomeAssertions;
using TokenScope.Core.Domain;
using TokenScope.Otel.Metrics;
using TokenScope.Otel.Tracking;
using Xunit;

namespace TokenScope.Otel.Tests.Metrics;

public class TokenScopeMetricsTests
{
    private static readonly DateTimeOffset Now = new(2026, 5, 14, 12, 0, 0, TimeSpan.Zero);

    [Fact]
    public void RecordRequest_EmitsTokenCountersWithCorrectTags()
    {
        using var capture = new MetricCapture();
        using var metrics = NewMetrics();

        var usage = new TokenUsage(Input: 100, Output: 50, CacheRead: 200, CacheWrite5m: 30, CacheWrite1h: 70);
        var cost = new Cost(Input: 0.001m, Output: 0.002m, CacheRead: 0.0003m, CacheWrite5m: 0.0004m, CacheWrite1h: 0.0005m);

        metrics.RecordRequest("claude-opus-4-7", "session-1", usage, cost, Now);

        var inputSample = capture.ByName("tokenscope.tokens.input").Should().ContainSingle().Subject;
        inputSample.Value.Should().Be(100L);
        Tag(inputSample, "model").Should().Be("claude-opus-4-7");
        Tag(inputSample, "session_id").Should().Be("session-1");

        capture.ByName("tokenscope.tokens.output").Should().ContainSingle().Which.Value.Should().Be(50L);
        capture.ByName("tokenscope.tokens.cache_read").Should().ContainSingle().Which.Value.Should().Be(200L);
    }

    [Fact]
    public void RecordRequest_CacheWriteEmitsTwoMeasurementsWithTtlLabels()
    {
        using var capture = new MetricCapture();
        using var metrics = NewMetrics();

        var usage = new TokenUsage(0, 0, 0, CacheWrite5m: 30, CacheWrite1h: 70);
        metrics.RecordRequest("m", "s", usage, Cost.Zero, Now);

        var writes = capture.ByName("tokenscope.tokens.cache_write").ToList();
        writes.Should().HaveCount(2);

        var fiveMin = writes.Single(s => "5m".Equals(Tag(s, "ttl")));
        var oneHour = writes.Single(s => "1h".Equals(Tag(s, "ttl")));
        fiveMin.Value.Should().Be(30L);
        oneHour.Value.Should().Be(70L);
    }

    [Fact]
    public void RecordRequest_ZeroComponents_NotEmitted()
    {
        using var capture = new MetricCapture();
        using var metrics = NewMetrics();

        var usage = new TokenUsage(0, 0, 0, 0, 0);
        metrics.RecordRequest("m", "s", usage, Cost.Zero, Now);

        capture.ByName("tokenscope.tokens.input").Should().BeEmpty();
        capture.ByName("tokenscope.tokens.output").Should().BeEmpty();
        capture.ByName("tokenscope.tokens.cache_read").Should().BeEmpty();
        capture.ByName("tokenscope.tokens.cache_write").Should().BeEmpty();
        capture.ByName("tokenscope.cost.usd").Should().BeEmpty();

        // requests.total IS emitted on every recorded request — that's how we count requests.
        capture.ByName("tokenscope.requests.total").Should().ContainSingle().Which.Value.Should().Be(1L);
    }

    [Fact]
    public void RecordRequest_CostEmittedPerComponent()
    {
        using var capture = new MetricCapture();
        using var metrics = NewMetrics();

        var cost = new Cost(0.10m, 0.20m, 0.05m, 0.07m, 0.13m);
        metrics.RecordRequest("m", "s", new TokenUsage(1, 1, 1, 1, 1), cost, Now);

        var costSamples = capture.ByName("tokenscope.cost.usd").ToList();
        costSamples.Should().HaveCount(5);

        costSamples.Single(s => "input".Equals(Tag(s, "component"))).Value.Should().Be(0.10);
        costSamples.Single(s => "output".Equals(Tag(s, "component"))).Value.Should().Be(0.20);
        costSamples.Single(s => "cache_read".Equals(Tag(s, "component"))).Value.Should().Be(0.05);
        costSamples.Single(s => "cache_write_5m".Equals(Tag(s, "component"))).Value.Should().Be(0.07);
        costSamples.Single(s => "cache_write_1h".Equals(Tag(s, "component"))).Value.Should().Be(0.13);
    }

    [Fact]
    public void RequestsTotal_IncrementsByOnePerCall()
    {
        using var capture = new MetricCapture();
        using var metrics = NewMetrics();

        metrics.RecordRequest("m", "s1", TokenUsage.Empty, Cost.Zero, Now);
        metrics.RecordRequest("m", "s1", TokenUsage.Empty, Cost.Zero, Now);
        metrics.RecordRequest("m", "s2", TokenUsage.Empty, Cost.Zero, Now);

        var samples = capture.ByName("tokenscope.requests.total").ToList();
        samples.Should().HaveCount(3);
        samples.Should().AllSatisfy(s => s.Value.Should().Be(1L));
    }

    [Fact]
    public void CacheHitRatio_ObservableGauge_EmitsPerSession()
    {
        using var capture = new MetricCapture();
        using var metrics = NewMetrics();

        metrics.RecordRequest("m", "session-alpha", new TokenUsage(Input: 25, Output: 0, CacheRead: 75, CacheWrite5m: 0, CacheWrite1h: 0), Cost.Zero, Now);
        metrics.RecordRequest("m", "session-beta",  new TokenUsage(Input: 50, Output: 0, CacheRead: 50, CacheWrite5m: 0, CacheWrite1h: 0), Cost.Zero, Now);

        capture.SampleObservables();

        var ratios = capture.ByName("tokenscope.cache.hit_ratio")
            .ToDictionary(s => (string)Tag(s, "session_id")!, s => (double)s.Value);

        ratios["session-alpha"].Should().BeApproximately(0.75, precision: 1e-9);
        ratios["session-beta"].Should().BeApproximately(0.50, precision: 1e-9);
    }

    [Fact]
    public void CacheHitRatio_UndefinedSession_NotEmitted()
    {
        using var capture = new MetricCapture();
        using var metrics = NewMetrics();

        // <synthetic> request: 0/0 across the board — ratio undefined.
        metrics.RecordRequest("m", "synthetic-session", TokenUsage.Empty, Cost.Zero, Now);

        capture.SampleObservables();

        capture.ByName("tokenscope.cache.hit_ratio").Should().BeEmpty();
    }

    [Fact]
    public void SessionsActive_CountsWithinWindow()
    {
        var clock = new FakeClock(Now);
        var activity = new SessionActivityTracker();
        using var capture = new MetricCapture();
        using var metrics = new TokenScopeMetrics(
            activity,
            new CacheRatioTracker(),
            clock,
            activeWindow: TimeSpan.FromMinutes(10));

        activity.MarkActive("s1", Now - TimeSpan.FromMinutes(2));
        activity.MarkActive("s2", Now - TimeSpan.FromMinutes(20));   // outside the window

        capture.SampleObservables();

        var sample = capture.ByName("tokenscope.sessions.active").Should().ContainSingle().Subject;
        sample.Value.Should().Be(1L);
    }

    [Fact]
    public void RecordRequest_NullOrEmptyArgs_Throw()
    {
        using var metrics = NewMetrics();
        var nullModel = () => metrics.RecordRequest(null!, "s", TokenUsage.Empty, Cost.Zero, Now);
        var nullSession = () => metrics.RecordRequest("m", null!, TokenUsage.Empty, Cost.Zero, Now);
        var nullUsage = () => metrics.RecordRequest("m", "s", null!, Cost.Zero, Now);
        var nullCost = () => metrics.RecordRequest("m", "s", TokenUsage.Empty, null!, Now);

        nullModel.Should().Throw<ArgumentException>();
        nullSession.Should().Throw<ArgumentException>();
        nullUsage.Should().Throw<ArgumentNullException>();
        nullCost.Should().Throw<ArgumentNullException>();
    }

    [Fact]
    public void Constructor_NullDependencies_Throw()
    {
        var nullActivity = () => new TokenScopeMetrics(null!, new CacheRatioTracker());
        var nullRatios = () => new TokenScopeMetrics(new SessionActivityTracker(), null!);

        nullActivity.Should().Throw<ArgumentNullException>();
        nullRatios.Should().Throw<ArgumentNullException>();
    }

    private static TokenScopeMetrics NewMetrics() =>
        new(new SessionActivityTracker(), new CacheRatioTracker(), TimeProvider.System);

    private static object? Tag(MetricCapture.Sample sample, string key) =>
        sample.Tags.FirstOrDefault(t => t.Key == key).Value;

    private sealed class FakeClock : TimeProvider
    {
        private readonly DateTimeOffset _now;
        public FakeClock(DateTimeOffset now) => _now = now;
        public override DateTimeOffset GetUtcNow() => _now;
    }
}
