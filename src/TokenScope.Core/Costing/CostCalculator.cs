using TokenScope.Core.Domain;
using TokenScope.Core.Pricing;

namespace TokenScope.Core.Costing;

public static class CostCalculator
{
    private const decimal TokensPerMTok = 1_000_000m;

    /// <summary>
    /// Computes the cost of a single <see cref="Request"/> by looking up the rate
    /// that was effective at the request's timestamp. The pricing table is treated
    /// as the historical record: a request's cost is always determined by the rate
    /// that was effective when the request happened, regardless of when this method
    /// is called.
    /// </summary>
    public static CostResult Calculate(Request request, IPricingTable pricing)
    {
        ArgumentNullException.ThrowIfNull(request);
        ArgumentNullException.ThrowIfNull(pricing);

        return pricing.Lookup(request.ModelId, request.Timestamp) switch
        {
            PricingLookupResult.Found f => new CostResult.Success(Compute(request.Usage, f.Rate)),
            PricingLookupResult.ModelNotFound => new CostResult.ModelNotFound(request.ModelId, request.Timestamp),
            PricingLookupResult.NoRateEffective => new CostResult.NoRateEffective(request.ModelId, request.Timestamp),
            _ => throw new InvalidOperationException("Unknown PricingLookupResult variant."),
        };
    }

    /// <summary>
    /// Computes a <see cref="Cost"/> from a known token usage and rate. Exposed for
    /// callers that have already resolved the rate (e.g. test code or aggregations).
    /// </summary>
    public static Cost Compute(TokenUsage usage, ModelRate rate)
    {
        ArgumentNullException.ThrowIfNull(usage);
        ArgumentNullException.ThrowIfNull(rate);

        return new Cost(
            Input: PerMTok(usage.Input, rate.InputPerMTok),
            Output: PerMTok(usage.Output, rate.OutputPerMTok),
            CacheRead: PerMTok(usage.CacheRead, rate.CacheReadPerMTok),
            CacheWrite5m: PerMTok(usage.CacheWrite5m, rate.CacheWrite5mPerMTok),
            CacheWrite1h: PerMTok(usage.CacheWrite1h, rate.CacheWrite1hPerMTok));
    }

    private static decimal PerMTok(long tokens, decimal ratePerMTok) =>
        (decimal)tokens / TokensPerMTok * ratePerMTok;
}
