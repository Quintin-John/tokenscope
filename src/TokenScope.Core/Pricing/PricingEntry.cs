using TokenScope.Core.Domain;

namespace TokenScope.Core.Pricing;

public sealed record PricingEntry(DateTimeOffset EffectiveDate, ModelRate Rate);
