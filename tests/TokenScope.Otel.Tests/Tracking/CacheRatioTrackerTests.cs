using AwesomeAssertions;
using TokenScope.Otel.Tracking;
using Xunit;

namespace TokenScope.Otel.Tests.Tracking;

public class CacheRatioTrackerTests
{
    [Fact]
    public void Snapshot_AfterRecord_ReturnsRatio()
    {
        var t = new CacheRatioTracker();
        t.Record("s1", cacheReadTokens: 80, inputTokens: 20);

        var snapshot = t.Snapshot().ToList();

        snapshot.Should().ContainSingle();
        snapshot[0].SessionId.Should().Be("s1");
        snapshot[0].Ratio.Should().BeApproximately(0.8, precision: 1e-9);
    }

    [Fact]
    public void Snapshot_ZeroDenominator_OmitsSession()
    {
        var t = new CacheRatioTracker();
        t.Record("s1", 0, 0);

        var snapshot = t.Snapshot().ToList();

        snapshot.Should().BeEmpty();
    }

    [Fact]
    public void Record_AccumulatesAcrossCalls()
    {
        var t = new CacheRatioTracker();
        t.Record("s1", cacheReadTokens: 50, inputTokens: 50);
        t.Record("s1", cacheReadTokens: 50, inputTokens: 0);

        var snapshot = t.Snapshot().ToList();

        snapshot[0].Ratio.Should().BeApproximately(100.0 / 150.0, precision: 1e-9);
    }

    [Fact]
    public void Snapshot_PerSessionDistinct()
    {
        var t = new CacheRatioTracker();
        t.Record("alpha", cacheReadTokens: 100, inputTokens: 0);
        t.Record("beta", cacheReadTokens: 0, inputTokens: 100);

        var snapshot = t.Snapshot().ToDictionary(x => x.SessionId, x => x.Ratio);

        snapshot["alpha"].Should().Be(1.0);
        snapshot["beta"].Should().Be(0.0);
    }

    [Fact]
    public void Record_NegativeArgs_Throws()
    {
        var t = new CacheRatioTracker();
        var negRead = () => t.Record("s1", -1, 0);
        var negInput = () => t.Record("s1", 0, -1);

        negRead.Should().Throw<ArgumentOutOfRangeException>();
        negInput.Should().Throw<ArgumentOutOfRangeException>();
    }

    [Fact]
    public void Record_EmptySessionId_Throws()
    {
        var t = new CacheRatioTracker();
        var act = () => t.Record("", 1, 1);
        act.Should().Throw<ArgumentException>();
    }
}
