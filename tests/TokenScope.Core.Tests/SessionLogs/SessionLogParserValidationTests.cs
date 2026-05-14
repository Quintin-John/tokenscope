using System.Text;
using AwesomeAssertions;
using TokenScope.Core.SessionLogs;
using Xunit;

namespace TokenScope.Core.Tests.SessionLogs;

public class SessionLogParserValidationTests
{
    // Each test feeds an assistant event with a single required field missing
    // and asserts the parser yields a MissingRequiredField warning for that field.

    [Fact]
    public void MissingSessionId_Warns()
    {
        AssertWarns("sessionId", AssistantEvent(omit: "sessionId"));
    }

    [Fact]
    public void MissingRequestId_Warns()
    {
        AssertWarns("requestId", AssistantEvent(omit: "requestId"));
    }

    [Fact]
    public void MissingTimestamp_Warns()
    {
        AssertWarns("timestamp", AssistantEvent(omit: "timestamp"));
    }

    [Fact]
    public void MissingMessage_Warns()
    {
        AssertWarns("message", AssistantEvent(omit: "message"));
    }

    [Fact]
    public void MissingMessageModel_Warns()
    {
        AssertWarns("message.model", AssistantEvent(omit: "message.model"));
    }

    [Fact]
    public void MissingMessageId_Warns()
    {
        AssertWarns("message.id", AssistantEvent(omit: "message.id"));
    }

    [Fact]
    public void MissingCacheCreation_DefaultsBothCacheWriteTokensToZero()
    {
        // cache_creation is allowed to be absent; the parser must still produce
        // an event with CacheWrite5m and CacheWrite1h defaulted to 0.
        const string json = """
            {"type":"assistant","requestId":"req_X","sessionId":"s","timestamp":"2026-05-13T00:00:00.000Z","message":{"id":"msg_X","model":"claude-opus-4-7","role":"assistant","type":"message","usage":{"input_tokens":5,"output_tokens":3,"cache_read_input_tokens":0}}}
            """;
        using var stream = new MemoryStream(Encoding.UTF8.GetBytes(json));
        var warnings = new List<ParseWarning>();

        var events = SessionLogParser.EnumerateAssistantEvents(stream, "mem", warnings.Add).ToList();

        events.Should().ContainSingle();
        events[0].Usage.CacheWrite5m.Should().Be(0);
        events[0].Usage.CacheWrite1h.Should().Be(0);
        warnings.Should().BeEmpty();
    }

    [Fact]
    public void ParsedAssistantEvent_IsDuplicateRecordEquality_Works()
    {
        var ts = new DateTimeOffset(2026, 5, 13, 0, 0, 0, TimeSpan.Zero);
        var u = new TokenScope.Core.Domain.TokenUsage(1, 2, 3, 4, 5);
        var a = new ParsedAssistantEvent("s", "r", "m", "model", ts, null, null, null, false, u, null, null, null, null, false);
        var b = a with { IsDuplicate = true };

        a.IsDuplicate.Should().BeFalse();
        b.IsDuplicate.Should().BeTrue();
        a.Should().NotBe(b);
    }

    [Fact]
    public void ParseWarning_RecordEquality_Distinguishes_Variants()
    {
        var malformed = new ParseWarning.MalformedJson("src", 1, "reason");
        var missing = new ParseWarning.MissingRequiredField("src", 1, "field");
        var io = new ParseWarning.IoFailure("src", 1, "reason");

        // Same base fields, different derived type → not equal.
        ((ParseWarning)malformed).Should().NotBe((ParseWarning)missing);
        ((ParseWarning)missing).Should().NotBe((ParseWarning)io);
        ((ParseWarning)io).Should().NotBe((ParseWarning)malformed);

        // Self equality preserved.
        ((ParseWarning)malformed).Should().Be(new ParseWarning.MalformedJson("src", 1, "reason"));
    }

    private static void AssertWarns(string expectedFieldPath, string json)
    {
        using var stream = new MemoryStream(Encoding.UTF8.GetBytes(json));
        var warnings = new List<ParseWarning>();

        var events = SessionLogParser.EnumerateAssistantEvents(stream, "mem", warnings.Add).ToList();

        events.Should().BeEmpty();
        warnings.OfType<ParseWarning.MissingRequiredField>()
            .Should().Contain(mrf => mrf.FieldPath == expectedFieldPath);
    }

    private static string AssistantEvent(string omit)
    {
        var sessionId = """ "sessionId":"s","""; if (omit == "sessionId") sessionId = "";
        var requestId = """ "requestId":"req_X","""; if (omit == "requestId") requestId = "";
        var timestamp = """ "timestamp":"2026-05-13T00:00:00.000Z","""; if (omit == "timestamp") timestamp = "";

        var messageId = """ "id":"msg_X","""; if (omit == "message.id") messageId = "";
        var messageModel = """ "model":"claude-opus-4-7","""; if (omit == "message.model") messageModel = "";
        var usage = """ "usage":{"input_tokens":1,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation":{"ephemeral_5m_input_tokens":0,"ephemeral_1h_input_tokens":0}} """;

        var message = $$""" "message":{{{messageId}}{{messageModel}}"role":"assistant","type":"message",{{usage}}}, """;
        if (omit == "message") message = "";

        return $$"""
            {"type":"assistant",{{sessionId}}{{requestId}}{{timestamp}}{{message}}"uuid":"u"}
            """;
    }
}
