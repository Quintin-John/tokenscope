using AwesomeAssertions;
using TokenScope.Otel.Tracking;
using Xunit;

namespace TokenScope.Otel.Tests.Tracking;

public class SessionActivityTrackerTests
{
    private static readonly DateTimeOffset Now = new(2026, 5, 14, 12, 0, 0, TimeSpan.Zero);

    [Fact]
    public void CountActive_WithinWindow_Counted()
    {
        var t = new SessionActivityTracker();
        t.MarkActive("s1", Now - TimeSpan.FromMinutes(2));
        t.MarkActive("s2", Now - TimeSpan.FromMinutes(7));

        t.CountActive(Now, TimeSpan.FromMinutes(10)).Should().Be(2);
    }

    [Fact]
    public void CountActive_OutsideWindow_NotCounted()
    {
        var t = new SessionActivityTracker();
        t.MarkActive("s1", Now - TimeSpan.FromMinutes(15));

        t.CountActive(Now, TimeSpan.FromMinutes(10)).Should().Be(0);
    }

    [Fact]
    public void MarkActive_Twice_KeepsLatestTimestamp()
    {
        var t = new SessionActivityTracker();
        t.MarkActive("s1", Now - TimeSpan.FromMinutes(20));
        t.MarkActive("s1", Now - TimeSpan.FromMinutes(2));

        t.CountActive(Now, TimeSpan.FromMinutes(10)).Should().Be(1);
    }

    [Fact]
    public void MarkActive_OutOfOrder_StillKeepsLatest()
    {
        var t = new SessionActivityTracker();
        t.MarkActive("s1", Now - TimeSpan.FromMinutes(2));
        t.MarkActive("s1", Now - TimeSpan.FromMinutes(20));

        // Out-of-order arrival should NOT regress the "most recent" timestamp.
        t.CountActive(Now, TimeSpan.FromMinutes(10)).Should().Be(1);
    }

    [Fact]
    public void CountActive_NonPositiveWindow_Throws()
    {
        var t = new SessionActivityTracker();
        var zero = () => t.CountActive(Now, TimeSpan.Zero);
        var negative = () => t.CountActive(Now, TimeSpan.FromMinutes(-1));

        zero.Should().Throw<ArgumentOutOfRangeException>();
        negative.Should().Throw<ArgumentOutOfRangeException>();
    }

    [Fact]
    public void MarkActive_EmptySessionId_Throws()
    {
        var t = new SessionActivityTracker();
        var act = () => t.MarkActive("", Now);

        act.Should().Throw<ArgumentException>();
    }

    [Fact]
    public void NoSessionsRecorded_CountIsZero()
    {
        var t = new SessionActivityTracker();
        t.CountActive(Now, TimeSpan.FromMinutes(10)).Should().Be(0);
    }
}
