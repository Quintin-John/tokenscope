using System.Text;
using AwesomeAssertions;
using TokenScope.Core.SessionLogs;
using Xunit;

namespace TokenScope.Core.Tests.SessionLogs;

public class SessionLogParserTests
{
    [Fact]
    public void HappyPath_YieldsAssistantEventsOnly()
    {
        var path = FixturePath("happy-path.jsonl");
        var warnings = new List<ParseWarning>();

        var events = SessionLogParser.EnumerateAssistantEvents(path, warnings.Add).ToList();

        events.Should().HaveCount(2);
        warnings.Should().BeEmpty();

        events[0].SessionId.Should().Be("session-A");
        events[0].RequestId.Should().Be("req_01A");
        events[0].MessageId.Should().Be("msg_01A");
        events[0].Model.Should().Be("claude-opus-4-7");
        events[0].Timestamp.Should().Be(new DateTimeOffset(2026, 5, 13, 10, 0, 5, TimeSpan.Zero));
        events[0].Usage.Input.Should().Be(100);
        events[0].Usage.Output.Should().Be(50);
        events[0].Usage.CacheRead.Should().Be(0);
        events[0].Usage.CacheWrite5m.Should().Be(0);
        events[0].Usage.CacheWrite1h.Should().Be(0);
        events[0].StopReason.Should().Be("end_turn");
        events[0].ServiceTier.Should().Be("standard");
        events[0].InferenceGeo.Should().Be("");
        events[0].Speed.Should().Be("standard");
        events[0].IsDuplicate.Should().BeFalse();
        events[0].IsSidechain.Should().BeFalse();

        events[1].Model.Should().Be("claude-sonnet-4-6");
        events[1].Usage.CacheWrite5m.Should().Be(1000);
        events[1].Usage.CacheWrite1h.Should().Be(500);
        events[1].Usage.CacheRead.Should().Be(800);
    }

    [Fact]
    public void DuplicatedRequestId_MarksSecondAsDuplicate()
    {
        var path = FixturePath("duplicates.jsonl");
        var events = SessionLogParser.EnumerateAssistantEvents(path).ToList();

        events.Should().HaveCount(2);
        events[0].IsDuplicate.Should().BeFalse();
        events[1].IsDuplicate.Should().BeTrue();
        events[1].RequestId.Should().Be(events[0].RequestId);
        events[1].Usage.Should().Be(events[0].Usage); // identical, that's the point
    }

    [Fact]
    public void MalformedAndIncompleteEntries_WarnAndContinue()
    {
        var path = FixturePath("malformed-mixed.jsonl");
        var warnings = new List<ParseWarning>();

        var events = SessionLogParser.EnumerateAssistantEvents(path, warnings.Add).ToList();

        events.Should().HaveCount(2);
        events.Select(e => e.RequestId).Should().BeEquivalentTo(new[] { "req_G1", "req_G2" });

        warnings.OfType<ParseWarning.MalformedJson>().Should().NotBeEmpty();
        warnings.OfType<ParseWarning.MissingRequiredField>()
            .Should().Contain(mrf => mrf.FieldPath == "message.usage");
    }

    [Fact]
    public void SyntheticModel_IsParsedThroughUnchanged()
    {
        var path = FixturePath("synthetic-model.jsonl");
        var warnings = new List<ParseWarning>();

        var events = SessionLogParser.EnumerateAssistantEvents(path, warnings.Add).ToList();

        events.Should().ContainSingle();
        events[0].Model.Should().Be("<synthetic>");
        events[0].Usage.Input.Should().Be(0);
        events[0].Usage.Output.Should().Be(0);
        warnings.Should().BeEmpty();
    }

    [Fact]
    public void PartialLastLine_TreatedAsMalformedJsonWarning()
    {
        // Simulate a writer that crashed mid-write: a complete first line, then
        // a truncated second line without trailing newline.
        const string content = """
            {"type":"assistant","requestId":"req_OK","sessionId":"s","timestamp":"2026-05-13T00:00:00.000Z","message":{"id":"msg_OK","model":"claude-opus-4-7","role":"assistant","type":"message","usage":{"input_tokens":1,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":0}}}}
            {"type":"assistant","requestId":"req_TRUNCATED","ses
            """;
        using var stream = new MemoryStream(Encoding.UTF8.GetBytes(content));
        var warnings = new List<ParseWarning>();

        var events = SessionLogParser.EnumerateAssistantEvents(stream, "memory", warnings.Add).ToList();

        events.Should().ContainSingle();
        events[0].RequestId.Should().Be("req_OK");
        warnings.Should().ContainSingle().Which.Should().BeOfType<ParseWarning.MalformedJson>();
    }

    [Fact]
    public void EmptyStream_YieldsNothing()
    {
        using var stream = new MemoryStream();
        var warnings = new List<ParseWarning>();

        var events = SessionLogParser.EnumerateAssistantEvents(stream, "memory", warnings.Add).ToList();

        events.Should().BeEmpty();
        warnings.Should().BeEmpty();
    }

    [Fact]
    public void BlankLines_SkippedSilently()
    {
        const string content = """
            {"type":"assistant","requestId":"req_X","sessionId":"s","timestamp":"2026-05-13T00:00:00.000Z","message":{"id":"msg_X","model":"claude-opus-4-7","role":"assistant","type":"message","usage":{"input_tokens":1,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":0}}}}

            {"type":"queue-operation","operation":"enqueue","timestamp":"2026-05-13T00:00:01.000Z","sessionId":"s"}
            """;
        using var stream = new MemoryStream(Encoding.UTF8.GetBytes(content));
        var warnings = new List<ParseWarning>();

        var events = SessionLogParser.EnumerateAssistantEvents(stream, "memory", warnings.Add).ToList();

        events.Should().ContainSingle();
        warnings.Should().BeEmpty();
    }

    [Fact]
    public void ToRequest_ProducesRequestSuitableForCostCalculator()
    {
        var path = FixturePath("happy-path.jsonl");

        var events = SessionLogParser.EnumerateAssistantEvents(path).ToList();
        var request = events[0].ToRequest();

        request.ModelId.Should().Be("claude-opus-4-7");
        request.Timestamp.Should().Be(events[0].Timestamp);
        request.Usage.Should().Be(events[0].Usage);
        request.Id.Should().Be(events[0].RequestId);
    }

    [Fact]
    public void EnumerateFromFile_HoldsSharedLockSoLiveSessionsCanBeReadInParallel()
    {
        // Open the same fixture file for writing in another stream; the parser
        // must succeed in opening it because it uses FileShare.ReadWrite|Delete.
        var path = FixturePath("happy-path.jsonl");
        using var concurrentWriter = new FileStream(
            path,
            FileMode.Open,
            FileAccess.Read,
            FileShare.ReadWrite | FileShare.Delete);

        var act = () => SessionLogParser.EnumerateAssistantEvents(path).ToList();

        act.Should().NotThrow();
    }

    [Fact]
    public void NullOrInvalidArgs_Throw()
    {
        var enumNull = () => SessionLogParser.EnumerateAssistantEvents((string)null!).ToList();
        var enumEmpty = () => SessionLogParser.EnumerateAssistantEvents("").ToList();

        enumNull.Should().Throw<ArgumentException>();
        enumEmpty.Should().Throw<ArgumentException>();
    }

    [Fact]
    public void EnumerateFromStream_NullArgs_Throw()
    {
        var nullStream = () => SessionLogParser.EnumerateAssistantEvents(null!, "src").ToList();
        var nullSource = () =>
        {
            using var s = new MemoryStream();
            return SessionLogParser.EnumerateAssistantEvents(s, null!).ToList();
        };

        nullStream.Should().Throw<ArgumentNullException>();
        nullSource.Should().Throw<ArgumentNullException>();
    }

    private static string FixturePath(string fileName)
    {
        var assemblyDir = Path.GetDirectoryName(typeof(SessionLogParserTests).Assembly.Location)!;
        return Path.Combine(assemblyDir, "Fixtures", "SessionLogs", fileName);
    }
}
