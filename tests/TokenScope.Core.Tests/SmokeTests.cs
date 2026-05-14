using AwesomeAssertions;
using Xunit;

namespace TokenScope.Core.Tests;

public class SmokeTests
{
    [Fact]
    public void Phase1_AssemblyMarker_IsReachable()
    {
        typeof(AssemblyMarker).Should().NotBeNull();
    }
}
