using System.Collections.Immutable;

namespace TokenScope.Collector.Configuration;

public sealed class TokenScopeOptionsValidationException : Exception
{
    public TokenScopeOptionsValidationException(ImmutableArray<string> errors)
        : base(BuildMessage(errors))
    {
        Errors = errors;
    }

    public ImmutableArray<string> Errors { get; }

    private static string BuildMessage(ImmutableArray<string> errors) =>
        errors.IsDefaultOrEmpty
            ? "tokenscope.yaml is invalid."
            : "tokenscope.yaml is invalid:\n  - " + string.Join("\n  - ", errors);
}
