using AwesomeAssertions;
using Xunit;

namespace TokenScope.Otel.Tests;

public class SmokeTests
{
    [Fact]
    public void Phase1_AssemblyMarker_IsReachable()
    {
        typeof(AssemblyMarker).Should().NotBeNull();
    }
}
