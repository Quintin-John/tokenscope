using AwesomeAssertions;
using Xunit;

namespace TokenScope.Collector.Tests;

public class SmokeTests
{
    [Fact]
    public void Phase1_SolutionBuilds()
    {
        true.Should().BeTrue();
    }
}
