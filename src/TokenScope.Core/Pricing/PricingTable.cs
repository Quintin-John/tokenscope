using System.Collections.Immutable;

namespace TokenScope.Core.Pricing;

public sealed class PricingTable : IPricingTable
{
    private readonly ImmutableDictionary<string, ImmutableArray<PricingEntry>> _entriesByModel;

    public PricingTable(ImmutableDictionary<string, ImmutableArray<PricingEntry>> entriesByModel)
    {
        _entriesByModel = entriesByModel;
        KnownModelIds = entriesByModel.Keys.ToImmutableArray();
    }

    public DateTimeOffset VerifiedAt { get; init; }

    public string? Source { get; init; }

    public ImmutableArray<string> KnownModelIds { get; }

    public PricingLookupResult Lookup(string modelId, DateTimeOffset at)
    {
        if (!_entriesByModel.TryGetValue(modelId, out var entries))
        {
            return PricingLookupResult.ModelNotFound.Instance;
        }

        // Entries are stored sorted by EffectiveDate ascending. Find the latest
        // entry whose EffectiveDate is <= `at`.
        PricingEntry? match = null;
        foreach (var entry in entries)
        {
            if (entry.EffectiveDate <= at)
            {
                match = entry;
            }
            else
            {
                break;
            }
        }

        return match is null
            ? PricingLookupResult.NoRateEffective.Instance
            : new PricingLookupResult.Found(match.Rate);
    }

    public static PricingTable Empty { get; } = new(
        ImmutableDictionary<string, ImmutableArray<PricingEntry>>.Empty);
}
