using System.Collections.Immutable;
using AwesomeAssertions;
using TokenScope.Core.Domain;
using Xunit;

namespace TokenScope.Core.Tests.Domain;

public class DomainRecordTests
{
    [Fact]
    public void Session_HoldsImmutableSequenceOfRequests()
    {
        var startedAt = new DateTimeOffset(2026, 5, 13, 12, 0, 0, TimeSpan.Zero);
        var req = new Request("r1", "claude-opus-4-7", startedAt, TokenUsage.Empty);
        var session = new Session("s1", startedAt, ImmutableArray.Create(req));

        session.Id.Should().Be("s1");
        session.StartedAt.Should().Be(startedAt);
        session.Requests.Should().ContainSingle().Which.Should().Be(req);
    }

    [Fact]
    public void TokenUsage_Total_SumsAllComponents()
    {
        var usage = new TokenUsage(1, 2, 4, 8, 16);

        usage.Total.Should().Be(31);
    }

    [Fact]
    public void TokenUsage_Empty_HasZeroTotal()
    {
        TokenUsage.Empty.Total.Should().Be(0);
    }
}
