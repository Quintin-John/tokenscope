using AwesomeAssertions;
using TokenScope.Core.SessionLogs;
using Xunit;

namespace TokenScope.Core.Tests.SessionLogs;

public class SessionLogParserParseLineTests
{
    [Fact]
    public void ParseLine_EmptyString_ReturnsBlank()
    {
        SessionLogParser.ParseLine("").Should().BeOfType<ParseLineOutcome.Blank>();
    }

    [Fact]
    public void ParseLine_MalformedJson_ReturnsWarning_WithSourceAndLineNumber()
    {
        var outcome = SessionLogParser.ParseLine("{ not json", source: "fixture", lineNumber: 42);

        var mj = outcome.Should().BeOfType<ParseLineOutcome.MalformedJson>().Subject;
        mj.Warning.Source.Should().Be("fixture");
        mj.Warning.LineNumber.Should().Be(42);
    }

    [Fact]
    public void ParseLine_NonAssistantType_ReturnsSkippedNonAssistant()
    {
        const string queueOp = """{"type":"queue-operation","operation":"enqueue","sessionId":"s"}""";

        SessionLogParser.ParseLine(queueOp)
            .Should().BeOfType<ParseLineOutcome.SkippedNonAssistant>();
    }

    [Fact]
    public void ParseLine_ValidAssistant_ReturnsEvent()
    {
        const string line = """{"type":"assistant","requestId":"req-1","sessionId":"s","timestamp":"2026-05-14T00:00:00.000Z","message":{"id":"msg-1","model":"claude-opus-4-7","role":"assistant","type":"message","usage":{"input_tokens":1,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":0}}}}""";

        var outcome = SessionLogParser.ParseLine(line);

        var ae = outcome.Should().BeOfType<ParseLineOutcome.AssistantEvent>().Subject;
        ae.Value.RequestId.Should().Be("req-1");
        ae.Value.Model.Should().Be("claude-opus-4-7");
    }

    [Fact]
    public void ParseLine_AssistantMissingUsage_ReturnsInvalidEvent()
    {
        const string line = """{"type":"assistant","requestId":"req-1","sessionId":"s","timestamp":"2026-05-14T00:00:00.000Z","message":{"id":"msg-1","model":"claude-opus-4-7","role":"assistant","type":"message"}}""";

        var outcome = SessionLogParser.ParseLine(line);

        var ie = outcome.Should().BeOfType<ParseLineOutcome.InvalidEvent>().Subject;
        ie.Warnings.OfType<ParseWarning.MissingRequiredField>()
            .Should().Contain(mrf => mrf.FieldPath == "message.usage");
    }

    [Fact]
    public void ParseLine_NullJson_Throws()
    {
        var act = () => SessionLogParser.ParseLine(null!);
        act.Should().Throw<ArgumentNullException>();
    }

    [Fact]
    public void ParseLine_NullSource_Throws()
    {
        var act = () => SessionLogParser.ParseLine("{}", source: null!);
        act.Should().Throw<ArgumentNullException>();
    }
}
