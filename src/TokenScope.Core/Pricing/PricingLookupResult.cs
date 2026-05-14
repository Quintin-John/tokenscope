using TokenScope.Core.Domain;

namespace TokenScope.Core.Pricing;

public abstract record PricingLookupResult
{
    private PricingLookupResult() { }

    public sealed record Found(ModelRate Rate) : PricingLookupResult;

    public sealed record ModelNotFound : PricingLookupResult
    {
        public static ModelNotFound Instance { get; } = new();
    }

    public sealed record NoRateEffective : PricingLookupResult
    {
        public static NoRateEffective Instance { get; } = new();
    }
}
