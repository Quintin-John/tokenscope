using System.Collections.Immutable;
using AwesomeAssertions;
using TokenScope.Collector.State;
using Xunit;

namespace TokenScope.Collector.Tests.State;

public class StateFileStoreTests : IDisposable
{
    private readonly string _tempDir;
    private readonly string _statePath;

    public StateFileStoreTests()
    {
        _tempDir = Path.Combine(Path.GetTempPath(), "tokenscope-state-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_tempDir);
        _statePath = Path.Combine(_tempDir, "seen.json");
    }

    public void Dispose()
    {
        try { Directory.Delete(_tempDir, recursive: true); } catch { }
    }

    [Fact]
    public void Save_ThenLoad_RoundTrips()
    {
        var state = new ResumeState
        {
            Files = ImmutableArray.Create(
                new ResumeFileEntry
                {
                    Path = "/tmp/session-a.jsonl",
                    LastModifiedUtc = new DateTimeOffset(2026, 5, 14, 0, 0, 0, TimeSpan.Zero),
                    ByteOffset = 12345,
                    LastProcessedLineNumber = 42,
                }),
        };

        StateFileStore.Save(_statePath, state);
        var loaded = StateFileStore.Load(_statePath);

        loaded.Files.Should().HaveCount(1);
        loaded.Files[0].Path.Should().Be("/tmp/session-a.jsonl");
        loaded.Files[0].ByteOffset.Should().Be(12345);
        loaded.Files[0].LastProcessedLineNumber.Should().Be(42);
    }

    [Fact]
    public void Load_MissingFile_ReturnsEmpty()
    {
        var loaded = StateFileStore.Load(Path.Combine(_tempDir, "no-such-file.json"));

        loaded.Files.Should().BeEmpty();
    }

    [Fact]
    public void Load_CorruptJson_ReturnsEmptyAndWarns()
    {
        File.WriteAllText(_statePath, "{ not json");
        var warnings = new List<string>();

        var loaded = StateFileStore.Load(_statePath, warnings.Add);

        loaded.Files.Should().BeEmpty();
        warnings.Should().ContainSingle().Which.Should().Contain("corrupt");
    }

    [Fact]
    public void Save_AtomicWrite_LeavesNoTempFileBehindOnSuccess()
    {
        var state = new ResumeState { Files = ImmutableArray<ResumeFileEntry>.Empty };

        StateFileStore.Save(_statePath, state);

        File.Exists(_statePath).Should().BeTrue();
        File.Exists(_statePath + ".tmp").Should().BeFalse();
    }

    [Fact]
    public void Save_DirectoryMissing_IsCreated()
    {
        var nestedPath = Path.Combine(_tempDir, "nested", "deep", "seen.json");
        var state = new ResumeState();

        StateFileStore.Save(nestedPath, state);

        File.Exists(nestedPath).Should().BeTrue();
    }

    [Fact]
    public void Save_OverwritesExisting()
    {
        File.WriteAllText(_statePath, "OLD CONTENT");

        StateFileStore.Save(_statePath, new ResumeState());
        var contents = File.ReadAllText(_statePath);

        contents.Should().Contain("\"schema_version\"");
        contents.Should().NotContain("OLD CONTENT");
    }

    [Fact]
    public void Save_NullOrEmptyArgs_Throws()
    {
        var nullPath = () => StateFileStore.Save(null!, new ResumeState());
        var emptyPath = () => StateFileStore.Save("", new ResumeState());
        var nullState = () => StateFileStore.Save(_statePath, null!);

        nullPath.Should().Throw<ArgumentException>();
        emptyPath.Should().Throw<ArgumentException>();
        nullState.Should().Throw<ArgumentNullException>();
    }
}
