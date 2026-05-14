using AwesomeAssertions;
using TokenScope.Core.SessionLogs;
using Xunit;

namespace TokenScope.Core.Tests.SessionLogs;

public class SessionLogDiscoveryTests : IDisposable
{
    private readonly string _fakeHome;
    private readonly string _projectsDir;

    public SessionLogDiscoveryTests()
    {
        _fakeHome = Path.Combine(Path.GetTempPath(), "tokenscope-discovery-" + Guid.NewGuid().ToString("N"));
        _projectsDir = Path.Combine(_fakeHome, ".claude", "projects");
        Directory.CreateDirectory(_projectsDir);
    }

    public void Dispose()
    {
        try { Directory.Delete(_fakeHome, recursive: true); } catch { /* best-effort */ }
    }

    [Fact]
    public void GetProjectsDirectory_DerivedFromHome()
    {
        var result = SessionLogDiscovery.GetProjectsDirectory(_fakeHome);

        result.Should().Be(_projectsDir);
    }

    [Fact]
    public void GetProjectsDirectory_WithoutHome_UsesEnvironmentUserProfile()
    {
        var expected = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile),
            ".claude",
            "projects");

        var actual = SessionLogDiscovery.GetProjectsDirectory();

        actual.Should().Be(expected);
    }

    [Fact]
    public void EnumerateProjectDirectories_ListsAllProjectDirs()
    {
        Directory.CreateDirectory(Path.Combine(_projectsDir, "-Users-test-projA"));
        Directory.CreateDirectory(Path.Combine(_projectsDir, "-Users-test-projB"));

        var dirs = SessionLogDiscovery.EnumerateProjectDirectories(_projectsDir).ToList();

        dirs.Should().HaveCount(2);
        dirs.Should().Contain(d => d.EndsWith("-Users-test-projA", StringComparison.Ordinal));
        dirs.Should().Contain(d => d.EndsWith("-Users-test-projB", StringComparison.Ordinal));
    }

    [Fact]
    public void EnumerateProjectDirectories_MissingProjectsDir_ReturnsEmpty()
    {
        var fake = Path.Combine(_fakeHome, "no-such-dir");

        var dirs = SessionLogDiscovery.EnumerateProjectDirectories(fake).ToList();

        dirs.Should().BeEmpty();
    }

    [Fact]
    public void EnumerateSessionFiles_OnlyReturnsJsonl()
    {
        var projDir = Path.Combine(_projectsDir, "-Users-test-projC");
        Directory.CreateDirectory(projDir);
        File.WriteAllText(Path.Combine(projDir, "abc.jsonl"), "{}\n");
        File.WriteAllText(Path.Combine(projDir, "def.jsonl"), "{}\n");
        File.WriteAllText(Path.Combine(projDir, "ignore.txt"), "not a session");

        var files = SessionLogDiscovery.EnumerateSessionFiles(projDir).ToList();

        files.Should().HaveCount(2);
        files.Should().AllSatisfy(p => p.Should().EndWith(".jsonl"));
    }

    [Fact]
    public void EnumerateSessionFiles_MissingDir_ReturnsEmpty()
    {
        var fake = Path.Combine(_fakeHome, "nothing");

        var files = SessionLogDiscovery.EnumerateSessionFiles(fake).ToList();

        files.Should().BeEmpty();
    }

    [Fact]
    public void EnumerateSessionFiles_NullOrEmptyArg_Throws()
    {
        var nullArg = () => SessionLogDiscovery.EnumerateSessionFiles(null!).ToList();
        var emptyArg = () => SessionLogDiscovery.EnumerateSessionFiles("").ToList();

        nullArg.Should().Throw<ArgumentException>();
        emptyArg.Should().Throw<ArgumentException>();
    }

    [Fact]
    public void EnumerateAllSessionFiles_WalksAllProjects()
    {
        var projA = Path.Combine(_projectsDir, "-Users-test-projA");
        var projB = Path.Combine(_projectsDir, "-Users-test-projB");
        Directory.CreateDirectory(projA);
        Directory.CreateDirectory(projB);
        File.WriteAllText(Path.Combine(projA, "s1.jsonl"), "{}\n");
        File.WriteAllText(Path.Combine(projB, "s2.jsonl"), "{}\n");
        File.WriteAllText(Path.Combine(projB, "s3.jsonl"), "{}\n");

        var files = SessionLogDiscovery.EnumerateAllSessionFiles(_projectsDir).ToList();

        files.Should().HaveCount(3);
    }
}
