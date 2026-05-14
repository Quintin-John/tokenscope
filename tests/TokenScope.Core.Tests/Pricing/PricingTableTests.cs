using System.Collections.Immutable;
using AwesomeAssertions;
using TokenScope.Core.Domain;
using TokenScope.Core.Pricing;
using Xunit;

namespace TokenScope.Core.Tests.Pricing;

public class PricingTableTests
{
    private static readonly ModelRate OldRate = new(3m, 15m, 0.3m, 3.75m, 6m);
    private static readonly ModelRate NewRate = new(5m, 25m, 0.5m, 6.25m, 10m);

    [Fact]
    public void Lookup_PicksLatestEffectiveRateAtOrBeforeTimestamp()
    {
        var table = Build(
            ("claude-opus-4-7", new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), OldRate),
            ("claude-opus-4-7", new DateTimeOffset(2026, 4, 1, 0, 0, 0, TimeSpan.Zero), NewRate));

        var beforeNewRate = table.Lookup("claude-opus-4-7", new DateTimeOffset(2026, 3, 31, 23, 0, 0, TimeSpan.Zero));
        var afterNewRate = table.Lookup("claude-opus-4-7", new DateTimeOffset(2026, 4, 2, 0, 0, 0, TimeSpan.Zero));

        beforeNewRate.Should().BeOfType<PricingLookupResult.Found>().Which.Rate.Should().Be(OldRate);
        afterNewRate.Should().BeOfType<PricingLookupResult.Found>().Which.Rate.Should().Be(NewRate);
    }

    [Fact]
    public void Lookup_AtExactEffectiveDate_UsesThatEntry()
    {
        var effective = new DateTimeOffset(2026, 4, 1, 0, 0, 0, TimeSpan.Zero);
        var table = Build(
            ("claude-opus-4-7", new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), OldRate),
            ("claude-opus-4-7", effective, NewRate));

        var result = table.Lookup("claude-opus-4-7", effective);

        result.Should().BeOfType<PricingLookupResult.Found>().Which.Rate.Should().Be(NewRate);
    }

    [Fact]
    public void Lookup_UnknownModel_ReturnsModelNotFound()
    {
        var table = Build(("claude-opus-4-7", new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero), NewRate));

        var result = table.Lookup("gpt-7", new DateTimeOffset(2026, 5, 1, 0, 0, 0, TimeSpan.Zero));

        result.Should().BeOfType<PricingLookupResult.ModelNotFound>();
    }

    [Fact]
    public void Lookup_BeforeAnyEffectiveDate_ReturnsNoRateEffective()
    {
        var table = Build(("claude-opus-4-7", new DateTimeOffset(2026, 4, 1, 0, 0, 0, TimeSpan.Zero), NewRate));

        var result = table.Lookup("claude-opus-4-7", new DateTimeOffset(2026, 1, 1, 0, 0, 0, TimeSpan.Zero));

        result.Should().BeOfType<PricingLookupResult.NoRateEffective>();
    }

    [Fact]
    public void Empty_HasNoModels()
    {
        PricingTable.Empty.KnownModelIds.Should().BeEmpty();
        PricingTable.Empty.Lookup("anything", DateTimeOffset.UtcNow)
            .Should().BeOfType<PricingLookupResult.ModelNotFound>();
    }

    private static PricingTable Build(params (string ModelId, DateTimeOffset At, ModelRate Rate)[] entries)
    {
        var builder = ImmutableDictionary.CreateBuilder<string, ImmutableArray<PricingEntry>>();
        foreach (var group in entries.GroupBy(e => e.ModelId))
        {
            builder[group.Key] = group
                .Select(e => new PricingEntry(e.At, e.Rate))
                .OrderBy(p => p.EffectiveDate)
                .ToImmutableArray();
        }
        return new PricingTable(builder.ToImmutable());
    }
}
