namespace TokenScope.Core.SessionLogs;

public abstract record ParseWarning(string Source, int LineNumber)
{
    public sealed record MalformedJson(string Source, int LineNumber, string Reason)
        : ParseWarning(Source, LineNumber);

    public sealed record MissingRequiredField(string Source, int LineNumber, string FieldPath)
        : ParseWarning(Source, LineNumber);

    public sealed record IoFailure(string Source, int LineNumber, string Reason)
        : ParseWarning(Source, LineNumber);
}
