using System.Collections.Immutable;

namespace TokenScope.Core.Pricing;

public sealed class PricingValidationException : Exception
{
    public PricingValidationException(ImmutableArray<string> errors)
        : base(BuildMessage(errors))
    {
        Errors = errors;
    }

    public ImmutableArray<string> Errors { get; }

    private static string BuildMessage(ImmutableArray<string> errors) =>
        errors.IsDefaultOrEmpty
            ? "Pricing configuration is invalid."
            : "Pricing configuration is invalid:\n  - " + string.Join("\n  - ", errors);
}
