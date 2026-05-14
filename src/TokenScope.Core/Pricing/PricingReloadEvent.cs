using System.Collections.Immutable;

namespace TokenScope.Core.Pricing;

public abstract record PricingReloadEvent
{
    private PricingReloadEvent() { }

    public sealed record Loaded(PricingTable Table, DateTimeOffset At) : PricingReloadEvent;

    public sealed record ValidationFailed(ImmutableArray<string> Errors, DateTimeOffset At) : PricingReloadEvent;

    public sealed record IoFailed(string Message, DateTimeOffset At) : PricingReloadEvent;
}
